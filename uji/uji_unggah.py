"""Uji unggah YouTube (resumable + refresh token) terhadap server tiruan Google. Jalankan: python uji/uji_unggah.py"""
import json, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "studio"))
import unggah as U

DITERIMA = bytearray(); META = {}; LOG = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); badan = self.rfile.read(n)
        if self.path == "/token":
            LOG.append(("token", badan.decode()))
            out = json.dumps({"access_token": "baru", "expires_in": 3600}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out); return
        META.update(json.loads(badan)); LOG.append(("mulai", self.headers.get("Authorization")))
        self.send_response(200); self.send_header("Location", f"http://127.0.0.1:{PORT}/sesi"); self.send_header("Content-Length", "0"); self.end_headers()
    def do_PUT(self):
        n = int(self.headers["Content-Length"]); blok = self.rfile.read(n)
        a, total = self.headers["Content-Range"].split(" ")[1].split("/")
        mulai = int(a.split("-")[0]); assert mulai == len(DITERIMA), (mulai, len(DITERIMA))
        DITERIMA.extend(blok)
        if len(DITERIMA) < int(total):
            self.send_response(308); self.send_header("Range", f"bytes=0-{len(DITERIMA)-1}"); self.send_header("Content-Length", "0"); self.end_headers()
        else:
            out = json.dumps({"id": "abc123"}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

srv = ThreadingHTTPServer(("127.0.0.1", 0), H); PORT = srv.server_port
threading.Thread(target=srv.serve_forever, daemon=True).start()
U.URL_UNGGAH = f"http://127.0.0.1:{PORT}/unggah"; U.URL_TOKEN = f"http://127.0.0.1:{PORT}/token"; U.POTONGAN = 256 * 1024

data = Path(tempfile.mkdtemp())
U.simpan_klien(data, "1-a.apps.googleusercontent.com", "rahasia")
U.simpan_token(data, {"access_token": "lama", "refresh_token": "r1", "kedaluwarsa": time.time() - 5})
video = data / "v.mp4"; isi = bytes(range(256)) * 2600; video.write_bytes(isi)
out = U.unggah_youtube(data, video, {"judul": "  Uji <b>judul</b>  ", "deskripsi": "d", "tag": ["#a", "b"], "privasi": "private"})
ok = [bytes(DITERIMA) == isi, out["id"] == "abc123", META["snippet"]["title"] == "Uji bjudul/b",
      META["status"]["privacyStatus"] == "private", META["snippet"]["tags"] == ["a", "b"],
      LOG[0][0] == "token" and "refresh_token=r1" in LOG[0][1], LOG[1] == ("mulai", "Bearer baru"),
      U.baca_token(data)["refresh_token"] == "r1"]
print(ok, out)
try:
    U.rapikan_meta({"judul": "x", "privasi": "semua"}); ok.append(False)
except ValueError: ok.append(True)
print("SEMUA LULUS" if all(ok) else "ADA GAGAL"); sys.exit(0 if all(ok) else 1)
