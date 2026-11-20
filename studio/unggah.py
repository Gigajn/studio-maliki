# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# kredit-maliki: Template oleh Maliki Saputra (@malikisaputra) — https://malikisaputra.biz.id · Lisensi Nonkomersial Maliki Studio 1.0 (alat/LISENSI.md): dilarang dijual ulang.
"""Unggah klip ke media sosial lewat API resmi: YouTube Shorts (di sini), TikTok, dan Instagram Reels.

Hanya pustaka standar Python, jadi tidak menambah paket di installer.

Alur YouTube:
  1. Pengguna membuat OAuth client "Desktop app" di Google Cloud Console miliknya sendiri (BYOK) dan
     menempelkan client_id + client_secret di Studio.
  2. Studio membuka halaman izin Google (scope youtube.upload saja). Google mengembalikan kode ke
     http://127.0.0.1:<port>/oauth/youtube, lalu Studio menukarnya dengan token (PKCE + state).
  3. Unggah memakai resumable upload, potongan 8 MB, dan selalu dimulai dari pengguna lewat layar review.

Token disimpan di <data>/.rahasia/youtube-token.json (hanya bisa dibaca akunmu di macOS/Linux).

Jalankan unggah dari Terminal:
  python unggah.py --data "~/Studio Maliki" --berkas klip-01.mp4 --meta meta.json
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# upload = unggah; readonly = membaca jumlah views untuk halaman Statistik
SCOPE = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
URL_IZIN = "https://accounts.google.com/o/oauth2/v2/auth"
URL_TOKEN = "https://oauth2.googleapis.com/token"
URL_CABUT = "https://oauth2.googleapis.com/revoke"
URL_UNGGAH = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
POTONGAN = 8 * 1024 * 1024  # kelipatan 256 KiB sesuai syarat resumable upload Google
PRIVASI = ("private", "unlisted", "public")
KATEGORI_BAWAAN = "22"  # People & Blogs


# ── Penyimpanan rahasia ─────────────────────────────────────────────────────
def folder_rahasia(data: Path) -> Path:
    f = data / ".rahasia"
    f.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(f, 0o700)
    except OSError:
        pass
    return f


def kunci_pemilik(p: Path) -> None:
    """Hanya pemilik akun yang boleh membaca file rahasia. Di macOS/Linux lewat mode 600; di Windows chmod tidak
    berlaku, jadi warisan izin folder diputus dan akses diberikan hanya ke pengguna saat ini (icacls)."""
    try:
        os.chmod(p, 0o600)
        if os.name == "nt" and os.environ.get("USERNAME"):
            subprocess.run(["icacls", str(p), "/inheritance:r", "/grant:r", f"{os.environ['USERNAME']}:F"],
                           capture_output=True, timeout=20, creationflags=0x08000000)
    except (OSError, subprocess.SubprocessError):
        pass


def _tulis_rahasia(p: Path, isi: dict) -> None:
    sementara = p.with_suffix(".tmp")
    # buat file dengan izin 600 sejak awal (tidak sempat terbaca akun lain)
    fd = os.open(sementara, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(isi, fh)
    os.replace(sementara, p)
    kunci_pemilik(p)


def _baca(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def baca_klien(data: Path) -> dict:
    return _baca(folder_rahasia(data) / "youtube-klien.json")


def simpan_klien(data: Path, client_id: str, client_secret: str) -> None:
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id.endswith(".apps.googleusercontent.com"):
        raise ValueError("Client ID harus berakhiran .apps.googleusercontent.com (OAuth client jenis Desktop app).")
    if not client_secret or len(client_secret) > 200:
        raise ValueError("Client secret belum diisi.")
    _tulis_rahasia(folder_rahasia(data) / "youtube-klien.json", {"client_id": client_id, "client_secret": client_secret})


def klien_dari_json(teks: str) -> tuple[str, str]:
    """Terima isi file client_secret_*.json yang diunduh dari Google Cloud Console."""
    d = json.loads(teks)
    d = d.get("installed") or d.get("web") or d
    return d.get("client_id", ""), d.get("client_secret", "")


def baca_token(data: Path) -> dict:
    return _baca(folder_rahasia(data) / "youtube-token.json")


def simpan_token(data: Path, tok: dict) -> None:
    _tulis_rahasia(folder_rahasia(data) / "youtube-token.json", tok)


def hapus_token(data: Path) -> None:
    tok = baca_token(data)
    if tok.get("refresh_token") or tok.get("access_token"):
        try:  # cabut izin di sisi Google juga (best effort)
            badan = urllib.parse.urlencode({"token": tok.get("refresh_token") or tok["access_token"]}).encode()
            urllib.request.urlopen(urllib.request.Request(URL_CABUT, data=badan), timeout=15).close()
        except Exception:  # noqa: BLE001
            pass
    (folder_rahasia(data) / "youtube-token.json").unlink(missing_ok=True)


# ── OAuth (PKCE + state) ────────────────────────────────────────────────────
def mulai_izin(data: Path, redirect: str) -> tuple[str, dict]:
    """Kembalikan (URL halaman izin Google, sesi rahasia yang disimpan server sampai kode kembali)."""
    k = baca_klien(data)
    if not k.get("client_id"):
        raise ValueError("Isi Client ID dan Client secret YouTube dulu.")
    verifier = secrets.token_urlsafe(64)
    tantangan = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    url = URL_IZIN + "?" + urllib.parse.urlencode({
        "client_id": k["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": tantangan, "code_challenge_method": "S256",
    })
    return url, {"state": state, "verifier": verifier, "redirect": redirect, "waktu": time.time()}


def _post_token(badan: dict) -> dict:
    req = urllib.request.Request(URL_TOKEN, data=urllib.parse.urlencode(badan).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            pesan = json.loads(e.read()).get("error_description") or e.reason
        except Exception:  # noqa: BLE001
            pesan = e.reason
        raise RuntimeError(f"Google menolak token ({e.code}): {pesan}") from None


def tukar_kode(data: Path, sesi: dict, kode: str) -> None:
    k = baca_klien(data)
    tok = _post_token({"code": kode, "client_id": k["client_id"], "client_secret": k["client_secret"],
                       "redirect_uri": sesi["redirect"], "grant_type": "authorization_code",
                       "code_verifier": sesi["verifier"]})
    tok["kedaluwarsa"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    simpan_token(data, tok)
    try:
        tok["kanal"] = nama_kanal(tok["access_token"])
        simpan_token(data, tok)
    except Exception:  # noqa: BLE001 — scope upload saja tidak selalu boleh membaca kanal
        pass


def token_segar(data: Path) -> str:
    tok = baca_token(data)
    if not tok.get("refresh_token") and not tok.get("access_token"):
        raise RuntimeError("Akun YouTube belum disambungkan. Buka Pengaturan → Akun unggah.")
    if tok.get("access_token") and time.time() < tok.get("kedaluwarsa", 0):
        return tok["access_token"]
    if not tok.get("refresh_token"):
        raise RuntimeError("Sesi YouTube kedaluwarsa. Sambungkan ulang akunnya.")
    k = baca_klien(data)
    baru = _post_token({"refresh_token": tok["refresh_token"], "client_id": k["client_id"],
                        "client_secret": k["client_secret"], "grant_type": "refresh_token"})
    tok.update(access_token=baru["access_token"], kedaluwarsa=time.time() + int(baru.get("expires_in", 3600)) - 60)
    simpan_token(data, tok)
    return tok["access_token"]


def nama_kanal(akses: str) -> str:
    req = urllib.request.Request("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                                 headers={"Authorization": f"Bearer {akses}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        item = (json.loads(r.read()).get("items") or [{}])[0]
    return (item.get("snippet") or {}).get("title", "")


def status(data: Path) -> dict:
    k, t = baca_klien(data), baca_token(data)
    return {"klien": bool(k.get("client_id")), "terhubung": bool(t.get("refresh_token")), "kanal": t.get("kanal", "")}


# ── Metadata ────────────────────────────────────────────────────────────────
def rapikan_meta(m: dict) -> dict:
    """Validasi metadata sesuai batas YouTube: judul ≤100 karakter, deskripsi ≤5000 byte, tag total ≤500."""
    judul = " ".join(str(m.get("judul", "")).split()).replace("<", "").replace(">", "")[:100]
    if not judul:
        raise ValueError("Judul video wajib diisi.")
    desk = str(m.get("deskripsi", "")).replace("<", "").replace(">", "")
    while len(desk.encode("utf-8")) > 5000:
        desk = desk[:-50]
    tag, total = [], 0
    for t in m.get("tag") or []:
        t = str(t).strip().lstrip("#").replace("<", "").replace(">", "")[:60]
        if t and total + len(t) + 1 <= 480:
            tag.append(t)
            total += len(t) + 1
    privasi = m.get("privasi", "private")
    if privasi not in PRIVASI:
        raise ValueError("Privasi harus private, unlisted, atau public.")
    return {"judul": judul, "deskripsi": desk, "tag": tag, "privasi": privasi,
            "anak": bool(m.get("untuk_anak", False)), "kategori": str(m.get("kategori") or KATEGORI_BAWAAN)}


# ── Unggah resumable ────────────────────────────────────────────────────────
def log(msg: str) -> None:
    print(f"• {msg}", flush=True)


def _minta(req: urllib.request.Request, coba: int = 5):
    """Kirim permintaan dengan backoff untuk galat jaringan / 5xx."""
    for i in range(coba):
        try:
            return urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as e:
            if e.code == 308:
                return e  # "Resume Incomplete": potongan diterima
            if e.code in (500, 502, 503, 504) and i < coba - 1:
                time.sleep(2 ** i)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if i == coba - 1:
                raise
            time.sleep(2 ** i)


def _jelaskan(e: urllib.error.HTTPError) -> str:
    try:
        isi = json.loads(e.read())
        err = isi.get("error", {})
        alasan = ((err.get("errors") or [{}])[0]).get("reason", "")
        pesan = err.get("message", "")
    except Exception:  # noqa: BLE001
        alasan, pesan = "", e.reason
    if alasan in ("quotaExceeded", "dailyLimitExceeded"):
        return "Kuota harian YouTube API habis (1 unggahan ≈ 1.600 dari 10.000 unit). Coba lagi besok."
    if alasan == "uploadLimitExceeded":
        return "Batas unggah kanal YouTube tercapai untuk hari ini."
    if e.code in (401, 403):
        return f"YouTube menolak ({e.code} {alasan}): {pesan}. Coba sambungkan ulang akunnya."
    return f"YouTube galat {e.code} {alasan}: {pesan}"


def unggah_youtube(data: Path, berkas: Path, meta: dict) -> dict:
    m = rapikan_meta(meta)
    if not berkas.is_file():
        raise RuntimeError(f"Video tidak ditemukan: {berkas.name}")
    ukuran = berkas.stat().st_size
    akses = token_segar(data)
    badan = {"snippet": {"title": m["judul"], "description": m["deskripsi"], "tags": m["tag"], "categoryId": m["kategori"]},
             "status": {"privacyStatus": m["privasi"], "selfDeclaredMadeForKids": m["anak"]}}
    req = urllib.request.Request(URL_UNGGAH, data=json.dumps(badan).encode(), method="POST", headers={
        "Authorization": f"Bearer {akses}", "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(ukuran)})
    try:
        with _minta(req) as r:
            sesi = r.headers["Location"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(_jelaskan(e)) from None
    log(f"Mengunggah {berkas.name} ({ukuran / 2**20:.1f} MB) ke YouTube sebagai {m['privasi']}…")
    terkirim, hasil = 0, None
    with open(berkas, "rb") as fh:
        while terkirim < ukuran:
            fh.seek(terkirim)
            blok = fh.read(POTONGAN)
            akhir = terkirim + len(blok) - 1
            req = urllib.request.Request(sesi, data=blok, method="PUT", headers={
                "Authorization": f"Bearer {token_segar(data)}", "Content-Length": str(len(blok)),
                "Content-Range": f"bytes {terkirim}-{akhir}/{ukuran}"})
            try:
                r = _minta(req)
            except urllib.error.HTTPError as e:
                raise RuntimeError(_jelaskan(e)) from None
            if getattr(r, "status", getattr(r, "code", 0)) == 308:
                rentang = r.headers.get("Range")  # mis. "bytes=0-8388607"
                terkirim = int(rentang.split("-")[-1]) + 1 if rentang else 0
            else:
                hasil = json.loads(r.read())
                terkirim = ukuran
            r.close()
            log(f"Terkirim {min(terkirim, ukuran) * 100 // max(ukuran, 1)}%")
    if not hasil or not hasil.get("id"):
        raise RuntimeError("YouTube tidak mengembalikan ID video.")
    vid = hasil["id"]
    return {"platform": "youtube", "id": vid, "url": f"https://youtube.com/shorts/{vid}", "privasi": m["privasi"],
            "judul": m["judul"], "waktu": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def statistik(data: Path, id_video: list[str]) -> dict:
    """views/suka/komentar per video (butuh scope youtube.readonly; akun lama perlu disambungkan ulang)."""
    ids = [i for i in id_video if i][:50]
    if not ids:
        return {}
    req = urllib.request.Request("https://www.googleapis.com/youtube/v3/videos?part=statistics&id=" + ",".join(ids),
                                 headers={"Authorization": f"Bearer {token_segar(data)}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            items = json.loads(r.read()).get("items", [])
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise RuntimeError("Izin membaca statistik YouTube belum ada. Putuskan lalu sambungkan ulang akun YouTube.") from None
        raise RuntimeError(_jelaskan(e)) from None
    return {x["id"]: {"views": int(x["statistics"].get("viewCount", 0)), "suka": int(x["statistics"].get("likeCount", 0)),
                      "komentar": int(x["statistics"].get("commentCount", 0)), "bagikan": 0} for x in items}


def main() -> None:
    p = argparse.ArgumentParser(description="Unggah video ke YouTube, TikTok, atau Instagram lewat API resmi.")
    p.add_argument("--platform", default="youtube", choices=["youtube", "tiktok", "instagram"])
    p.add_argument("--data", required=True, help="folder data Studio (berisi .rahasia)")
    p.add_argument("--berkas", required=True, help="video MP4")
    p.add_argument("--meta", required=True, help="JSON metadata (judul, deskripsi, tag, privasi)")
    p.add_argument("--hasil", help="tulis hasil (JSON) ke file ini")
    a = p.parse_args()
    data = Path(a.data).expanduser()
    try:
        meta = json.loads(Path(a.meta).read_text(encoding="utf-8"))
        if a.platform == "tiktok":
            from unggah_tiktok import unggah_tiktok as fungsi
        elif a.platform == "instagram":
            from unggah_instagram import unggah_instagram as fungsi
        else:
            fungsi = unggah_youtube
        out = fungsi(data, Path(a.berkas), meta)
    except (RuntimeError, ValueError) as e:
        sys.exit(str(e))
    if a.hasil:
        Path(a.hasil).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    log(f"Selesai: {out.get('url') or out.get('id')} ({out['privasi']})")


if __name__ == "__main__":
    main()
