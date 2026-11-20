"""Uji server Studio: keamanan host/asal, akun YouTube, OAuth state, aturan unggah, traversal unduh. Jalankan: python uji/uji_server.py"""
import json, os, subprocess, sys, time, urllib.request, urllib.error, tempfile
from pathlib import Path

data = Path(tempfile.mkdtemp())
env = dict(os.environ, STUDIO_DATA=str(data), STUDIO_PORT="8899", BROWSER="true")
srv = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent.parent / "studio" / "studio.py"), "--tanpa-buka"], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
B = "http://127.0.0.1:8899"
for _ in range(50):
    try:
        urllib.request.urlopen(B + "/api/status", timeout=1); break
    except Exception:
        time.sleep(0.2)


def req(path, body=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    r = urllib.request.Request(B + path, data=None if body is None else json.dumps(body).encode(), headers=h,
                               method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(r, timeout=10) as x:
            return x.status, x.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


hasil = []
def cek(nama, kondisi, info=""):
    hasil.append(kondisi); print(("LULUS " if kondisi else "GAGAL ") + nama, info if not kondisi else "")

cek("status normal", req("/api/status")[0] == 200)
cek("host asing ditolak (GET)", req("/api/status", headers={"Host": "evil.com:8899"})[0] == 403)
cek("host asing ditolak (POST)", req("/api/batal", {}, headers={"Host": "evil.com:8899"})[0] == 403)
cek("GET lintas situs ditolak", req("/api/proyek", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403)
cek("halaman utama tetap jalan", req("/", headers={"Sec-Fetch-Site": "none"})[0] == 200)
cek("POST dari origin lain ditolak", req("/api/batal", {}, headers={"Origin": "https://evil.com"})[0] == 403)
k, t = req("/api/akun-unggah"); cek("akun-unggah awal", k == 200 and json.loads(t)["youtube"]["klien"] is False, t)
k, t = req("/api/youtube-klien", {"client_id": "salah", "client_secret": "x"}); cek("client id salah ditolak", k == 400, t)
k, t = req("/api/youtube-klien", {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-uji"})
cek("client tersimpan", k == 200 and json.loads(t)["youtube"]["klien"], t)
f = data / ".rahasia" / "youtube-klien.json"
cek("izin file client 600", oct(f.stat().st_mode)[-3:] == "600", oct(f.stat().st_mode))
cek("secret tidak dikirim balik", "GOCSPX" not in t)
k, t = req("/api/youtube-masuk", {}); u = json.loads(t).get("url", "")
cek("URL izin Google benar", k == 200 and u.startswith("https://accounts.google.com/") and "code_challenge=" in u and "youtube.upload" in u, t)
k, t = req("/oauth/youtube?state=palsu&code=abc", headers={"Sec-Fetch-Site": "cross-site"})
cek("state palsu ditolak", k == 200 and "tidak berlaku" in t, t[:200])
k, t = req("/api/unggah-klip", {"nama": "x", "no": 1, "judul": "a", "izin": False}); cek("unggah tanpa izin ditolak", k == 400 and "berhak" in t, t)
k, t = req("/api/unggah-klip", {"nama": "x", "no": 1, "judul": "a", "izin": True}); cek("unggah tanpa akun ditolak", k == 400 and "Sambungkan" in t, t)
# traversal di /api/unduh
p = data / "klip" / "20260101-000000"; p.mkdir(parents=True); (p / "klip-01.mp4").write_bytes(b"x")
k, t = req("/api/unduh", {"jenis": "klip", "nama": "20260101-000000/../../../../../tmp/jahat", "file": "../20260101-000000/klip-01.mp4"})
tujuan = json.loads(t).get("jalur", "") if k == 200 else ""
cek("unduh tidak keluar dari Downloads", k != 200 or Path(tujuan).parent == Path.home() / "Downloads", t)
if tujuan: Path(tujuan).unlink(missing_ok=True)
k, t = req("/api/klip-render", {"nama": None}); cek("galat tak terduga tetap dijawab", k in (400, 500), str(k))
k, t = req("/api/antrean"); d = json.loads(t)
cek("endpoint antrean", k == 200 and set(d["jalur"]) == {"utama", "unggah"}, t)
k, t = req("/api/antrean-batal", {"id": "tidak-ada"}); cek("batal id tak dikenal aman", k == 200 and json.loads(t)["ok"] is False, t)
k, t = req("/api/unggah-klip", {"platform": "tiktok", "nama": "x", "no": 1, "izin": True}); cek("tiktok tanpa akun ditolak", k == 400 and "TikTok" in t, t)
k, t = req("/api/unggah-klip", {"platform": "facebook", "nama": "x", "no": 1, "izin": True}); cek("platform asing ditolak", k == 400, t)
k, t = req("/api/akun-unggah"); cek("status tiga akun", set(json.loads(t)) == {"youtube", "tiktok", "instagram"}, t)
k, t = req("/oauth/tiktok?state=palsu&code=x", headers={"Sec-Fetch-Site": "cross-site"}); cek("state TikTok palsu ditolak", "tidak berlaku" in t, t[:200])
# ── perbaikan keamanan K7–K9 ──
k, t = req("/api/pengaturan", {"GEMINI_MODEL": "x\nOPENAI_BASE_URL=http://jahat.com"}); cek("baris baru di pengaturan ditolak", k == 400, t)
k, t = req("/api/pengaturan", {"OPENAI_BASE_URL": "http://jahat.com/v1"}); cek("URL AI http publik ditolak", k == 400 and "https" in t, t)
for url in ("https://api.groq.com/openai/v1", "http://localhost:11434/v1", "http://192.168.1.5:11434/v1"):
    k, t = req("/api/pengaturan", {"OPENAI_BASE_URL": url}); cek(f"URL AI diterima: {url}", k == 200, t)
cek("file pengaturan 600", oct((data / "pengaturan.env").stat().st_mode)[-3:] == "600" if (data / "pengaturan.env").exists() else True)
k, t = req("/api/klip", {"sumber": "--keluar=/etc"}); cek("sumber berawalan - ditolak", k == 400, t)
def unggah(nama, isi):
    r = urllib.request.Request(B + "/api/unggah", data=isi, headers={"X-Nama-File": nama, "Content-Type": "application/octet-stream"}, method="POST")
    with urllib.request.urlopen(r, timeout=10) as x:
        return json.loads(x.read())["jalur"]
j1, j2 = unggah("../../a.mp4", b"1"), unggah("a.mp4", b"2")
cek("unggah: nama unik, tidak menimpa", j1 != j2 and Path(j1).read_bytes() == b"1" and Path(j2).read_bytes() == b"2", (j1, j2))
cek("unggah: tetap di folder unggahan", Path(j1).parent == data / "unggahan", j1)
r = urllib.request.Request(B + "/api/unggah", data=b"", headers={"X-Nama-File": "x.mp4", "Content-Length": str(30 * 2**30)}, method="POST")
try:
    urllib.request.urlopen(r, timeout=10); cek("unggah > 20 GB ditolak", False)
except urllib.error.HTTPError as e:
    cek("unggah > 20 GB ditolak", e.code == 413, e.code)
except Exception as e:  # server menutup koneksi lebih awal juga dianggap menolak
    cek("unggah > 20 GB ditolak", True)
srv.terminate()
print(f"{sum(hasil)}/{len(hasil)} lulus"); sys.exit(0 if all(hasil) else 1)
