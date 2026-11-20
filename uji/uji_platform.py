"""Uji unggah TikTok (draf & posting langsung) dan Instagram Reels terhadap server tiruan. Jalankan: python uji/uji_platform.py"""
import json
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "studio"))
import unggah_instagram as IG  # noqa: E402
import unggah_tiktok as TT  # noqa: E402

CATAT = {"tt_put": bytearray(), "tt_init": None, "ig_upload": b"", "ig_param": {}, "jalur": []}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _kirim(self, obj, kode=200):
        b = json.dumps(obj).encode()
        self.send_response(kode)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _badan(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        badan = self._badan()
        CATAT["jalur"].append(u.path)
        if u.path == "/tt/oauth/token/":
            return self._kirim({"access_token": "tt-baru", "expires_in": 86400, "refresh_token": "r2", "open_id": "o"})
        if u.path == "/tt/post/publish/creator_info/query/":
            return self._kirim({"data": {"creator_nickname": "kudo", "privacy_level_options": ["SELF_ONLY", "PUBLIC_TO_EVERYONE"],
                                         "max_video_post_duration_sec": 600}, "error": {"code": "ok"}})
        if u.path in ("/tt/post/publish/video/init/", "/tt/post/publish/inbox/video/init/"):
            CATAT["tt_init"] = (u.path, json.loads(badan))
            return self._kirim({"data": {"publish_id": "p_1", "upload_url": f"http://127.0.0.1:{PORT}/tt-unggah"}, "error": {"code": "ok"}})
        if u.path == "/tt/post/publish/status/fetch/":
            langsung = "inbox" not in CATAT["tt_init"][0]
            return self._kirim({"data": {"status": "PUBLISH_COMPLETE" if langsung else "SEND_TO_USER_INBOX",
                                         "publicaly_available_post_id": [7311] if langsung else []}, "error": {"code": "ok"}})
        if u.path == "/ig/123/media":
            CATAT["ig_param"] = dict(urllib.parse.parse_qsl(badan.decode()))
            return self._kirim({"id": "c9", "uri": f"http://127.0.0.1:{PORT}/rup/c9"})
        if u.path == "/rup/c9":
            CATAT["ig_upload"] = badan
            CATAT["ig_hdr"] = {k.lower(): v for k, v in self.headers.items()}
            return self._kirim({"success": True})
        if u.path == "/ig/123/media_publish":
            return self._kirim({"id": "m77"})
        self._kirim({"error": {"code": "not_found"}}, 404)

    def do_PUT(self):
        badan = self._badan()
        a = int(self.headers["Content-Range"].split(" ")[1].split("-")[0])
        assert a == len(CATAT["tt_put"]), (a, len(CATAT["tt_put"]))
        CATAT["tt_put"].extend(badan)
        self._kirim({}, 201)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/ig/me":
            return self._kirim({"user_id": "123", "username": "kudo.clips", "account_type": "BUSINESS"})
        if u.path == "/ig/c9":
            return self._kirim({"status_code": "FINISHED"})
        if u.path == "/ig/m77":
            return self._kirim({"permalink": "https://www.instagram.com/reel/abc/"})
        self._kirim({"error": {"code": 100}}, 404)


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
PORT = srv.server_port
threading.Thread(target=srv.serve_forever, daemon=True).start()
TT.API = f"http://127.0.0.1:{PORT}/tt"
TT.POTONGAN, TT.MIN_POTONGAN = 300_000, 250_000
IG.GRAPH = f"http://127.0.0.1:{PORT}/ig"
hasil = []


def cek(nama, kondisi, info=""):
    hasil.append(bool(kondisi))
    print(("LULUS " if kondisi else "GAGAL ") + nama, "" if kondisi else info)


data = Path(tempfile.mkdtemp())
video = data / "v.mp4"
isi = bytes(range(256)) * 4000  # ±1 MB → 3 potongan, sisa masuk potongan terakhir
video.write_bytes(isi)

# ── TikTok ──
TT.simpan_klien(data, "kunci", "rahasia")
url, sesi = TT.mulai_izin(data, "http://127.0.0.1:8787/oauth/tiktok")
q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
cek("tiktok: URL izin memuat PKCE hex dan scope", len(q["code_challenge"]) == 64 and "video.publish" in q["scope"], q)
TT._tulis_rahasia(TT.folder_rahasia(data) / "tiktok-token.json", {"access_token": "lama", "refresh_token": "r1", "kedaluwarsa": 0, "nama": "kudo"})
out = TT.unggah_tiktok(data, video, {"mode": "draf", "teks": "halo"})
cek("tiktok draf: semua byte terkirim", bytes(CATAT["tt_put"]) == isi, len(CATAT["tt_put"]))
cek("tiktok draf: memakai endpoint inbox", CATAT["tt_init"][0].endswith("/inbox/video/init/"))
src = CATAT["tt_init"][1]["source_info"]
cek("tiktok: rencana potongan benar", src["total_chunk_count"] == len(isi) // 300_000 and src["chunk_size"] == 300_000, src)
cek("tiktok draf: status inbox", out["status"] == "SEND_TO_USER_INBOX" and out["privasi"] == "draf", out)
cek("tiktok: token diperbarui", TT.baca_token(data)["access_token"] == "tt-baru")
try:
    TT.unggah_tiktok(data, video, {"mode": "langsung", "teks": "x", "privasi": ""})
    cek("tiktok langsung: privasi wajib dipilih", False)
except RuntimeError:
    cek("tiktok langsung: privasi wajib dipilih", True)
CATAT["tt_put"] = bytearray()
out = TT.unggah_tiktok(data, video, {"mode": "langsung", "teks": "caption #a", "privasi": "SELF_ONLY", "konten_bermerek": True, "cover_ms": 1500})
post = CATAT["tt_init"][1]["post_info"]
cek("tiktok langsung: post_info lengkap", post["privacy_level"] == "SELF_ONLY" and post["brand_content_toggle"] is True
    and post["video_cover_timestamp_ms"] == 1500 and post["title"] == "caption #a", post)
cek("tiktok langsung: url video", out["url"] == "https://www.tiktok.com/@kudo/video/7311", out)

# ── Instagram ──
st = IG.simpan_token(data, "IGQ-token")
cek("instagram: token tersimpan dan akun terbaca", st["terhubung"] and st["kanal"] == "kudo.clips", st)
out = IG.unggah_instagram(data, video, {"teks": "caption ig", "ke_feed": False, "cover_ms": 2000})
cek("instagram: wadah REELS resumable", CATAT["ig_param"].get("media_type") == "REELS" and CATAT["ig_param"].get("upload_type") == "resumable"
    and CATAT["ig_param"].get("share_to_feed") == "false" and CATAT["ig_param"].get("thumb_offset") == "2000", CATAT["ig_param"])
cek("instagram: semua byte terkirim dengan header resmi", CATAT["ig_upload"] == isi and CATAT["ig_hdr"].get("file_size") == str(len(isi))
    and CATAT["ig_hdr"].get("authorization") == "OAuth IGQ-token")
cek("instagram: terbit dengan permalink", out["id"] == "m77" and out["url"].startswith("https://www.instagram.com/reel/"), out)
print(f"{sum(hasil)}/{len(hasil)} lulus")
sys.exit(0 if all(hasil) else 1)
