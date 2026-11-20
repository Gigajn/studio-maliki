"""Uji caption per platform dan profil kampanye. Jalankan: python uji/uji_caption.py"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "studio"))
import caption as C  # noqa: E402

hasil = []


def cek(nama, kondisi, info=""):
    hasil.append(bool(kondisi))
    print(("LULUS " if kondisi else "GAGAL ") + nama, "" if kondisi else info)


klip = {"judul": "Streamer kaget lihat skor", "caption": "Reaksi paling jujur malam ini " * 20, "mulai": 10, "selesai": 45,
        "kata_kunci": ["skor", "kaget"]}
info = {"hashtag": ["gaming", "#lucu"] + [f"tag{i}" for i in range(40)]}
data = Path(tempfile.mkdtemp())
k = C.simpan_kampanye(data, {"nama": "Whop Podcast X", "platform": ["tiktok", "youtube"], "tag_wajib": "#podcastx, clips",
                             "mention_wajib": "@podcastx", "teks_wajib": "#ad Klip resmi Podcast X", "durasi_min": 40,
                             "durasi_maks": 90, "tarif_per_1000": "1.5", "sumber_izin": "https://whop.com/podcastx"})
cek("kampanye tersimpan dengan id", k["id"] and C.cari_kampanye(data, k["id"])["nama"] == "Whop Podcast X")
cek("tag wajib dibersihkan", k["tag_wajib"] == ["podcastx", "clips"], k["tag_wajib"])
try:
    C.simpan_kampanye(data, {"nama": "Tanpa izin"}); cek("kampanye tanpa bukti izin ditolak", False)
except ValueError:
    cek("kampanye tanpa bukti izin ditolak", True)

for p, b in C.PLATFORM.items():
    c = C.susun(klip, info, p, k)
    cek(f"{p}: panjang teks ≤ {b['teks']}", len(c["teks"]) <= b["teks"], len(c["teks"]))
    cek(f"{p}: jumlah hashtag ≤ {b['hashtag']}", len(c["tag"]) <= b["hashtag"], len(c["tag"]))
    if b["hashtag"] >= 3:
        cek(f"{p}: tag wajib ada di depan", c["tag"][:2] == ["podcastx", "clips"], c["tag"][:3])
    if b["teks"] >= 280:
        cek(f"{p}: mention dan teks wajib ikut", "@podcastx" in c["teks"] and ("#ad" in c["teks"] or b["teks"] < 300), c["teks"][-120:])
    cek(f"{p}: peringatan durasi muncul", any("minimal 40" in x for x in c["peringatan"]), c["peringatan"])

yt = C.susun(klip, info, "youtube", k)
cek("youtube: judul ada dan ≤100", 0 < len(yt["judul"]) <= 100)
cek("youtube: #Shorts ikut", "Shorts" in yt["tag"])
ig = C.susun(klip, info, "instagram", k)
cek("instagram: peringatan platform di luar kampanye", any("tidak mencantumkan" in x for x in ig["peringatan"]))
cek("instagram: tanpa judul terpisah", ig["judul"] == "")
x = C.susun(klip, info, "x", None)
cek("x: ≤280 karakter tanpa kampanye", len(x["teks"]) <= 280, len(x["teks"]))
cek("sumber izin diteruskan", yt["sumber_izin"] == "https://whop.com/podcastx")
C.hapus_kampanye(data, k["id"])
cek("kampanye terhapus", C.cari_kampanye(data, k["id"]) is None)
print(f"{sum(hasil)}/{len(hasil)} lulus")
sys.exit(0 if all(hasil) else 1)
