"""Uji antrean kerja: jalur paralel, urutan, batal, jadwal, jeda platform, dan pemulihan setelah Studio ditutup.

Jalankan: python uji/uji_antrean.py
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "studio"))
from antrean import Antrean, Kerja  # noqa: E402

PY = sys.executable
tidur = lambda d: [PY, "-c", f"import time; print('mulai', flush=True); time.sleep({d}); print('beres')"]  # noqa: E731
gagal = [PY, "-c", "import sys; print('rusak'); sys.exit(3)"]
hasil = []


def cek(nama, kondisi, info=""):
    hasil.append(bool(kondisi))
    print(("LULUS " if kondisi else "GAGAL ") + nama, "" if kondisi else info)


def tunggu(a, sampai, batas=15):
    t0 = time.time()
    while time.time() - t0 < batas:
        a.detak()
        if sampai():
            return True
        time.sleep(0.05)
    return False


tmp = Path(tempfile.mkdtemp())
dipanggil = []
utama, unggah = Kerja(), Kerja()
jeda = {"youtube": 0}
a = Antrean(tmp / "antrean.json", {"utama": utama, "unggah": unggah},
            {"catat": lambda k, pesan: dipanggil.append(pesan)}, lambda p: jeda.get(p, 0))

t1 = a.tambah("utama", "render", [(tidur(0.6), tmp)], tmp, ("catat", {"pesan": "t1"}), label="render 1")
t2 = a.tambah("utama", "render", [(tidur(0.1), tmp)], tmp, ("catat", {"pesan": "t2"}), label="render 2")
t3 = a.tambah("unggah", "unggah", [(tidur(0.2), tmp)], tmp, label="unggah 1", platform="youtube")
cek("tugas pertama langsung jalan", t1["status"] == "jalan", t1)
cek("tugas kedua menunggu di jalur yang sama", t2["status"] == "menunggu" and t2["posisi"] == 1, t2)
cek("jalur unggah jalan paralel", t3["status"] == "jalan", t3)
cek("semua selesai berurutan", tunggu(a, lambda: all(a.cari(x["id"])["status"] == "selesai" for x in (t1, t2, t3))),
    [a.cari(x["id"])["status"] for x in (t1, t2, t3)])
cek("fungsi setelah dipanggil sesuai urutan", dipanggil == ["t1", "t2"], dipanggil)
cek("render 2 mulai setelah render 1 selesai", a.cari(t2["id"])["mulai"] >= a.cari(t1["id"])["selesai"])

t4 = a.tambah("utama", "x", [(gagal, tmp)], tmp, label="rusak")
cek("tugas gagal tercatat dengan pesannya", tunggu(a, lambda: a.cari(t4["id"])["status"] == "gagal") and "rusak" in a.cari(t4["id"])["galat"],
    a.cari(t4["id"]))
u = a.ulangi(t4["id"])
cek("tugas gagal bisa diulang", u["id"] != t4["id"] and tunggu(a, lambda: a.cari(u["id"])["status"] == "gagal"))

t5 = a.tambah("utama", "x", [(tidur(5), tmp)], tmp, label="lama")
t6 = a.tambah("utama", "x", [(tidur(0.1), tmp)], tmp, label="dibatalkan saat menunggu")
cek("batal tugas menunggu", a.batal(t6["id"]) and a.cari(t6["id"])["status"] == "batal")
time.sleep(0.3)
a.batal(t5["id"])
cek("batal tugas berjalan", tunggu(a, lambda: a.cari(t5["id"])["status"] == "batal"), a.cari(t5["id"]))

t7 = a.tambah("utama", "x", [(tidur(0.1), tmp)], tmp, label="terjadwal", jadwal=time.time() + 1.0)
cek("tugas terjadwal menunggu jadwalnya", t7["status"] == "menunggu")
cek("tugas terjadwal jalan setelah waktunya", tunggu(a, lambda: a.cari(t7["id"])["status"] == "selesai")
    and a.cari(t7["id"])["mulai"] >= a.cari(t7["id"])["jadwal"])

jeda["youtube"] = 3600
t8 = a.tambah("unggah", "unggah", [(tidur(0.1), tmp)], tmp, label="unggah 2", platform="youtube")
time.sleep(0.3)
a.detak()
cek("jeda antar-posting menahan unggahan berikutnya", a.cari(t8["id"])["status"] == "menunggu")
t9 = a.tambah("unggah", "unggah", [(tidur(0.1), tmp)], tmp, label="unggah tiktok", platform="tiktok")
cek("platform lain tidak ikut tertahan", tunggu(a, lambda: a.cari(t9["id"])["status"] == "selesai"))

# Pemulihan: tugas yang menunggu bertahan, tugas yang jalan ditandai terputus
t10 = a.tambah("utama", "x", [(tidur(5), tmp)], tmp, label="sedang jalan saat ditutup")
d = json.loads((tmp / "antrean.json").read_text())
b = Antrean(tmp / "antrean.json", {"utama": Kerja(), "unggah": Kerja()}, {}, lambda p: 3600)
cek("tugas menunggu tetap ada setelah dibuka ulang", b.cari(t8["id"])["status"] == "menunggu")
cek("tugas yang terputus ditandai gagal", b.cari(t10["id"])["status"] == "gagal" and "Terputus" in b.cari(t10["id"])["galat"])
cek("daftar tidak memuat perintah mentah", all("langkah" not in t for t in b.daftar()["tugas"]))
a.batal(t10["id"])
print(f"{sum(hasil)}/{len(hasil)} lulus")
sys.exit(0 if all(hasil) else 1)
