# kredit-maliki: Template oleh Maliki Saputra (@malikisaputra) — https://malikisaputra.biz.id · Lisensi Nonkomersial Maliki Studio 1.0 (alat/LISENSI.md): dilarang dijual ulang.
"""Unggah Reels ke Instagram lewat Instagram API with Instagram Login (pustaka standar saja).

- Butuh akun Instagram Business/Creator dan aplikasi Meta milikmu dengan produk "Instagram API".
- Token akses jangka panjang dibuat di Meta App Dashboard (Generate token) lalu ditempel di Studio,
  karena Instagram hanya menerima alamat https sebagai redirect OAuth. Token diperpanjang otomatis (60 hari).
- Video dikirim dengan *resumable upload* langsung dari laptop, jadi tidak perlu hosting URL publik.
- Batas resmi: 100 posting lewat API per 24 jam per akun.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from unggah import _baca, _tulis_rahasia, folder_rahasia, log

VERSI = "v23.0"
GRAPH = f"https://graph.instagram.com/{VERSI}"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{VERSI}"


def baca_token(data: Path) -> dict:
    return _baca(folder_rahasia(data) / "instagram-token.json")


def status(data: Path) -> dict:
    t = baca_token(data)
    return {"klien": True, "terhubung": bool(t.get("token")), "kanal": t.get("username", ""),
            "kedaluwarsa": t.get("kedaluwarsa", 0)}


def _get(url: str, **param) -> dict:
    try:
        with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(param)}", timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(_jelaskan(e)) from None


def _post(url: str, **param) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(param).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(_jelaskan(e)) from None


def _jelaskan(e: urllib.error.HTTPError) -> str:
    try:
        err = json.loads(e.read()).get("error", {})
    except Exception:  # noqa: BLE001
        err = {}
    kode, pesan = err.get("code"), err.get("error_user_msg") or err.get("message") or e.reason
    if kode == 190:
        return "Token Instagram kedaluwarsa atau dicabut. Buat token baru di Meta App Dashboard lalu tempel ulang."
    if kode in (4, 9, 32, 613) or "limit" in str(pesan).lower():
        return "Batas posting/permintaan Instagram tercapai (100 posting API per 24 jam). Coba lagi nanti."
    return f"Instagram galat {e.code}: {pesan}"


def simpan_token(data: Path, token: str) -> dict:
    token = token.strip()
    if not token or len(token) > 1000 or any(c.isspace() for c in token):
        raise ValueError("Tempel token akses Instagram (satu baris, tanpa spasi).")
    me = _get(f"{GRAPH}/me", fields="user_id,username,account_type", access_token=token)
    if me.get("account_type") not in (None, "BUSINESS", "MEDIA_CREATOR", "CREATOR"):
        raise ValueError("Akun ini bukan akun Business/Creator. Ubah jenis akun di aplikasi Instagram.")
    tok = {"token": token, "ig_id": str(me.get("user_id") or me.get("id")), "username": me.get("username", ""),
           "kedaluwarsa": time.time() + 59 * 86400, "disimpan": time.time()}
    _tulis_rahasia(folder_rahasia(data) / "instagram-token.json", tok)
    return status(data)


def hapus_token(data: Path) -> None:
    (folder_rahasia(data) / "instagram-token.json").unlink(missing_ok=True)


def token_segar(data: Path) -> tuple[str, str]:
    t = baca_token(data)
    if not t.get("token"):
        raise RuntimeError("Akun Instagram belum disambungkan. Buka Pengaturan → Akun unggah.")
    # perpanjang bila sisa < 7 hari (token harus berumur ≥ 24 jam)
    if t.get("kedaluwarsa", 0) - time.time() < 7 * 86400 and time.time() - t.get("disimpan", 0) > 86400:
        d = _get("https://graph.instagram.com/refresh_access_token", grant_type="ig_refresh_token", access_token=t["token"])
        t.update(token=d["access_token"], kedaluwarsa=time.time() + int(d.get("expires_in", 5184000)) - 3600, disimpan=time.time())
        _tulis_rahasia(folder_rahasia(data) / "instagram-token.json", t)
    return t["token"], t["ig_id"]


def unggah_instagram(data: Path, berkas: Path, meta: dict) -> dict:
    if not berkas.is_file():
        raise RuntimeError(f"Video tidak ditemukan: {berkas.name}")
    token, ig = token_segar(data)
    ukuran = berkas.stat().st_size
    param = {"media_type": "REELS", "upload_type": "resumable", "caption": str(meta.get("teks", ""))[:2200],
             "share_to_feed": "true" if meta.get("ke_feed", True) else "false", "access_token": token}
    if meta.get("cover_ms") is not None:
        param["thumb_offset"] = str(int(meta["cover_ms"]))
    wadah = _post(f"{GRAPH}/{ig}/media", **param)
    cid = wadah["id"]
    log(f"Mengunggah {berkas.name} ({ukuran / 2**20:.1f} MB) ke Instagram Reels…")
    with open(berkas, "rb") as fh:
        req = urllib.request.Request(wadah.get("uri") or f"{RUPLOAD}/{cid}", data=fh, method="POST", headers={
            "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(ukuran), "Content-Length": str(ukuran)})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                ok = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(_jelaskan(e)) from None
    if ok.get("success") is False:
        raise RuntimeError(f"Instagram menolak unggahan: {ok}")
    log("Terkirim 100%")
    st = {}
    for _ in range(120):  # Instagram memproses video (biasanya < 2 menit)
        st = _get(f"{GRAPH}/{cid}", fields="status_code,status", access_token=token)
        if st.get("status_code") in ("FINISHED", "ERROR", "EXPIRED"):
            break
        time.sleep(3)
    if st.get("status_code") != "FINISHED":
        raise RuntimeError(f"Instagram gagal memproses video: {st.get('status') or st.get('status_code') or 'waktu habis'}")
    terbit = _post(f"{GRAPH}/{ig}/media_publish", creation_id=cid, access_token=token)
    mid = terbit["id"]
    try:
        link = _get(f"{GRAPH}/{mid}", fields="permalink", access_token=token).get("permalink", "")
    except RuntimeError:
        link = ""
    return {"platform": "instagram", "id": mid, "url": link, "privasi": "public", "judul": str(meta.get("teks", ""))[:100],
            "waktu": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def statistik(data: Path, id_media: list[str]) -> dict:
    token, _ = token_segar(data)
    out = {}
    for mid in [i for i in id_media if i][:50]:
        try:
            d = _get(f"{GRAPH}/{mid}/insights", metric="views,likes,comments,shares", access_token=token)
        except RuntimeError:
            continue
        nilai = {x["name"]: (x.get("values") or [{}])[0].get("value", 0) for x in d.get("data", [])}
        out[mid] = {"views": nilai.get("views", 0), "suka": nilai.get("likes", 0), "komentar": nilai.get("comments", 0),
                    "bagikan": nilai.get("shares", 0)}
    return out
