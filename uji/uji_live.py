"""Uji pemantau live (Twitch, Kick, YouTube tiruan) dan deteksi momen perekam. Jalankan: python uji/uji_live.py"""
import json
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AKAR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AKAR / "studio"))
sys.path.insert(0, str(AKAR / "klip"))
import live as L  # noqa: E402
import rekam_live as R  # noqa: E402

CATAT = {"token": 0, "twitch_q": None}
VID = "abcdefghijk"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _kirim(self, obj, kode=200, tipe="application/json"):
        b = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(kode)
        self.send_header("Content-Type", tipe)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        CATAT["token"] += 1
        self._kirim({"access_token": "app", "expires_in": 3600})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/helix/streams":
            CATAT["twitch_q"] = q
            assert self.headers["Authorization"] == "Bearer app" and self.headers["Client-Id"] == "tw-id"
            return self._kirim({"data": [{"user_login": "Kudostream", "type": "live", "title": "Main horor", "viewer_count": 1500}]})
        if u.path == "/kick/channels":
            return self._kirim({"data": [{"slug": "kicker", "stream_title": "Ngobrol", "stream": {"is_live": True, "viewer_count": 40}},
                                         {"slug": "sepi", "stream": {"is_live": False}}]})
        if u.path == "/rss":
            return self._kirim(f"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
              <entry><yt:videoId>{VID}</yt:videoId></entry><entry><yt:videoId>lamaaaaaaaa</yt:videoId></entry></feed>""".encode(), tipe="application/xml")
        if u.path == "/yt/videos":
            assert q.get("key") == ["yt-kunci"]
            return self._kirim({"items": [
                {"id": VID, "snippet": {"title": "Live podcast"}, "liveStreamingDetails": {"actualStartTime": "2026-11-20T10:00:00Z", "concurrentViewers": "88"}},
                {"id": "lamaaaaaaaa", "snippet": {"title": "Lama"}, "liveStreamingDetails": {"actualStartTime": "x", "actualEndTime": "y"}}]})
        if u.path == "/yt/channels":
            return self._kirim({"items": [{"id": "UC" + "a" * 22}]})
        self._kirim({}, 404)


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
B = f"http://127.0.0.1:{srv.server_port}"
L.TWITCH_TOKEN, L.TWITCH_API = B + "/token", B + "/helix"
L.KICK_TOKEN, L.KICK_API = B + "/token", B + "/kick"
L.YT_API, L.YT_RSS = B + "/yt", B + "/rss?channel_id="
hasil = []


def cek(nama, kondisi, info=""):
    hasil.append(bool(kondisi))
    print(("LULUS " if kondisi else "GAGAL ") + nama, "" if kondisi else info)


class ProsesPalsu:
    def __init__(self):
        self.selesai = None

    def poll(self):
        return self.selesai

    def wait(self, timeout=None):
        self.selesai = 0

    def terminate(self):
        self.selesai = -1


direkam = []


def rekam(k, folder):
    direkam.append((k["nama"], folder))
    return ProsesPalsu()


data = Path(tempfile.mkdtemp())
P = L.Pemantau(data, rekam)

# ── validasi kanal ──
for platform, nama in (("twitch", "a b"), ("kick", "x/../.."), ("youtube", "bukan-id"), ("lain", "x")):
    try:
        P.simpan_kanal({"platform": platform, "nama": nama})
        cek(f"kanal tidak valid ditolak: {platform} {nama}", False)
    except ValueError:
        cek(f"kanal tidak valid ditolak: {platform} {nama}", True)
try:
    P.simpan_kanal({"platform": "twitch", "nama": "kudostream", "auto_rekam": True})
    cek("rekam otomatis tanpa izin ditolak", False)
except ValueError:
    cek("rekam otomatis tanpa izin ditolak", True)

tw = P.simpan_kanal({"platform": "twitch", "nama": "https://www.twitch.tv/KudoStream", "izin": "https://whop.com/x", "auto_rekam": True})
cek("nama Twitch dari URL dirapikan", tw["nama"] == "kudostream", tw)
ki = P.simpan_kanal({"platform": "kick", "nama": "@Kicker"})
P.simpan_kanal({"platform": "kick", "nama": "sepi", "izin": "izin tertulis", "auto_rekam": True})
yt = P.simpan_kanal({"platform": "youtube", "nama": "UC" + "b" * 22, "izin": "izin", "auto_rekam": False})
cek("simpan ulang kanal yang sama tidak menggandakan", len([k for k in P.kanal() if k["nama"] == "kudostream"]) == 1)

# ── tanpa kunci: galat jelas, tidak mati ──
P.cek()
cek("tanpa kunci: galat per platform", "twitch" in P.galat and "kick" in P.galat and "youtube" in P.galat, P.galat)

P.simpan_kunci({"twitch_id": "tw-id", "twitch_rahasia": "tw-r", "kick_id": "ki", "kick_rahasia": "kr", "youtube_kunci": "yt-kunci"})
cek("kunci tersimpan dengan mode 600", oct((data / ".rahasia" / "live-kunci.json").stat().st_mode)[-3:] == "600")
cek("status kunci", P.status_kunci() == {"twitch": True, "kick": True, "youtube": True}, P.status_kunci())
daftar = {k["nama"]: k for k in P.cek()}
cek("cek tanpa galat", not P.galat, P.galat)
cek("twitch: live terdeteksi", daftar["kudostream"]["live"] and daftar["kudostream"]["penonton"] == 1500, daftar["kudostream"])
cek("twitch: query user_login", CATAT["twitch_q"] == {"user_login": ["kudostream"]}, CATAT["twitch_q"])
cek("kick: live dan offline", daftar["kicker"]["live"] and not daftar["sepi"]["live"])
cek("youtube: live dari RSS + videos.list", daftar["UC" + "b" * 22]["live"] and daftar["UC" + "b" * 22]["url_live"].endswith(VID))
cek("rekam otomatis hanya kanal live + izin + auto", [n for n, _ in direkam] == ["kudostream"], direkam)
cek("sesi.json ditulis", (direkam[0][1] / "sesi.json").exists())
P.cek()
cek("tidak merekam dua kali", len(direkam) == 1)
token_awal = CATAT["token"]
P.cek()
cek("token aplikasi dipakai ulang", CATAT["token"] == token_awal)
try:
    P.mulai_rekam(daftar["kicker"])
    cek("rekam manual tanpa izin ditolak", False)
except ValueError:
    cek("rekam manual tanpa izin ditolak", True)
cek("hentikan menulis BERHENTI", P.hentikan(tw["id"]) and (direkam[0][1] / "BERHENTI").exists())
cek("handle YouTube diubah ke ID kanal", P.simpan_kanal({"platform": "youtube", "nama": "@kudo"})["nama"] == "UC" + "a" * 22)

# ── kandidat ──
sesi = direkam[0][1]
(sesi / "kandidat-001.mp4").write_bytes(b"x")
(sesi / "kandidat.json").write_text(json.dumps([{"file": "kandidat-001.mp4", "waktu": 1, "alasan": "chat", "skor": 0.5}]))
cek("kandidat terdaftar", [c["file"] for c in P.kandidat()] == ["kandidat-001.mp4"])
for jahat in (("..", "..", "kanal.json"), (tw["id"], sesi.name, "../../kanal.json"), (tw["id"], sesi.name, "sesi.json/..")):
    try:
        p = P.berkas_kandidat(*jahat)
        cek(f"jalur jahat ditolak {jahat}", p.parent == sesi and p.name.startswith("kandidat"), p)
    except ValueError:
        cek(f"jalur jahat ditolak {jahat}", True)
P.buang_kandidat(tw["id"], sesi.name, "kandidat-001.mp4")
cek("kandidat dibuang", not P.kandidat() and not (sesi / "kandidat-001.mp4").exists())
P.hapus_kanal(ki["id"])
cek("kanal dihapus", all(k["id"] != ki["id"] for k in P.kanal()))

# ── deteksi momen ──
energi = [1.0] * 400
energi[100], energi[150], energi[300] = 9.0, 8.0, 5.0
m = R.cari_momen(energi, [], [])
cek("momen suara: puncak tertinggi, jeda 90 dtk", [x["waktu"] for x in m] == [100.0, 300.0], m)
chat = [2] * 80
chat[40] = 30
m = R.cari_momen([1.0] * 400, chat, [])
cek("momen chat: lonjakan 15× terdeteksi", len(m) == 1 and m[0]["alasan"] == "chat" and m[0]["waktu"] == 202.5, m)
cek("momen yang sudah diambil tidak diulang", R.cari_momen(energi, [], [100.0, 300.0]) == [])
cek("chat sepi (<5 pesan) tidak dianggap lonjakan", R.cari_momen([1.0] * 100, [0] * 10 + [4] + [0] * 5, []) == [])

# ── perekam end-to-end dengan file lokal (butuh ffmpeg) ──
ff = R.ffmpeg()
try:
    subprocess.run([ff, "-version"], capture_output=True, check=True)
    ada_ff = True
except (OSError, subprocess.CalledProcessError):
    ada_ff = False
if ada_ff:
    kerja = Path(tempfile.mkdtemp())
    sumber = kerja / "sumber.mp4"
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=15", "-f", "lavfi", "-i",
                    "aevalsrc='0.02*sin(2*PI*440*t)*(1+20*between(t\\,25\\,27))':s=22050", "-t", "45", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(sumber)], check=True)
    r = subprocess.run([sys.executable, str(AKAR / "klip" / "rekam_live.py"), "--url", str(sumber), "--folder", str(kerja / "sesi")],
                       capture_output=True, text=True, timeout=180, env={**__import__("os").environ, "REKAM_SEGMEN": "10"})
    kd = json.loads((kerja / "sesi" / "kandidat.json").read_text()) if (kerja / "sesi" / "kandidat.json").exists() else []
    cek("perekam: kandidat dipotong dari lonjakan suara", len(kd) == 1 and kd[0]["alasan"] == "suara" and (kerja / "sesi" / kd[0]["file"]).stat().st_size > 0,
        r.stdout + r.stderr)
    cek("perekam: potongan sementara dibersihkan", not list((kerja / "sesi").glob("seg-*.ts")))
else:
    print("LEWATI perekam end-to-end (ffmpeg tidak ada)")

print(f"{sum(hasil)}/{len(hasil)} lulus")
sys.exit(0 if all(hasil) else 1)
