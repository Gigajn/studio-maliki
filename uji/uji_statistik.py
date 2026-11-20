"""Uji pelacak views dan estimasi pendapatan. Jalankan: python uji/uji_statistik.py"""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "studio"))
import statistik as S  # noqa: E402

hasil = []


def cek(nama, kondisi, info=""):
    hasil.append(bool(kondisi))
    print(("LULUS " if kondisi else "GAGAL ") + nama, "" if kondisi else info)


data = Path(tempfile.mkdtemp())
(data / "unggah").mkdir()
riwayat = [
    {"platform": "youtube", "id": f"yt{i}", "judul": f"Klip {i}", "kampanye": "k1", "waktu": f"2026-11-01T10:{i:02d}:00"} for i in range(60)
] + [
    {"platform": "tiktok", "id": "v_inbox_file~abc", "judul": "Draf", "kampanye": "k1", "waktu": "2026-11-19T10:00:00"},
    {"platform": "instagram", "id": "m1", "judul": "Reel", "kampanye": "", "waktu": "2026-11-19T11:00:00"},
]
(data / "unggah" / "riwayat.jsonl").write_text("\n".join(json.dumps(x) for x in riwayat) + "\nbukan json\n")
panggilan = {"youtube": [], "tiktok": []}


def yt(_data, ids):
    panggilan["youtube"].append(len(ids))
    return {i: {"views": 1000, "suka": 5, "komentar": 1} for i in ids}


def tt(_data, ids):
    panggilan["tiktok"].append(list(ids))
    return {"7311234567890123456": {"views": 20000, "suka": 9}} if "7311234567890123456" in ids else {}


def ig(_data, ids):
    raise RuntimeError("token kedaluwarsa")


kampanye = [{"id": "k1", "nama": "Podcast X", "tarif_per_1000": 1.5, "mata_uang": "USD"}]
P = S.Pelacak(data, {"youtube": SimpleNamespace(statistik=yt), "tiktok": SimpleNamespace(statistik=tt),
                     "instagram": SimpleNamespace(statistik=ig)}, lambda: kampanye)

r = P.segarkan()
cek("youtube dipanggil per 50 ID", panggilan["youtube"] == [50, 10], panggilan["youtube"])
cek("galat instagram dicatat tanpa menghentikan platform lain", "instagram" in r["galat"] and "youtube" not in r["galat"], r["galat"])
cek("baris rusak di riwayat dilewati", r["total"]["unggahan"] == 62, r["total"])
draf = next(u for u in r["unggahan"] if u["platform"] == "tiktok")
cek("draf TikTok minta ditautkan", draf["perlu_tautan"] and draf["views"] == 0, draf)
cek("estimasi per kampanye", r["kampanye"][0]["views"] == 60000 and r["kampanye"][0]["estimasi"] == 90.0, r["kampanye"])
cek("total pendapatan per mata uang", r["total"]["pendapatan"] == {"USD": 90.0}, r["total"])

try:
    P.tautkan("tiktok", "v_inbox_file~abc", "https://example.com/bukan")
    cek("link TikTok tidak valid ditolak", False)
except ValueError:
    cek("link TikTok tidak valid ditolak", True)
P.tautkan("tiktok", "v_inbox_file~abc", "https://www.tiktok.com/@kudo/video/7311234567890123456?lang=id")
r = P.segarkan()
draf = next(u for u in r["unggahan"] if u["platform"] == "tiktok")
cek("draf tertaut: views terambil lewat ID posting", draf["views"] == 20000 and not draf["perlu_tautan"] and draf["estimasi"] == 30.0, draf)
cek("riwayat views satu titik per hari", len(draf["riwayat"]) == 1)

P.isi_manual("instagram", "m1", "5000")
r = P.ringkasan()
reel = next(u for u in r["unggahan"] if u["platform"] == "instagram")
cek("views manual dipakai", reel["views"] == 5000 and reel["manual"] and reel["estimasi"] == 0, reel)
try:
    P.isi_manual("instagram", "m1", "-3")
    cek("views negatif ditolak", False)
except ValueError:
    cek("views negatif ditolak", True)
P.isi_manual("instagram", "m1", "")
cek("views manual bisa dihapus", not next(u for u in P.ringkasan()["unggahan"] if u["platform"] == "instagram")["manual"])
cek("urut terbaru dulu", P.ringkasan()["unggahan"][0]["platform"] == "instagram")
print(f"{sum(hasil)}/{len(hasil)} lulus")
sys.exit(0 if all(hasil) else 1)
