# kredit-maliki: Template oleh Maliki Saputra (@malikisaputra) — https://malikisaputra.biz.id · Lisensi Nonkomersial Maliki Studio 1.0 (alat/LISENSI.md): dilarang dijual ulang.
"""Unggah ke TikTok lewat Content Posting API resmi (pustaka standar saja).

Dua mode:
- "draf"     (scope video.upload): video masuk kotak masuk/draf aplikasi TikTok, kamu menyelesaikan posting di HP.
                                   Paling aman dan tidak butuh audit.
- "langsung" (scope video.publish): Direct Post. Sebelum aplikasi lolos audit TikTok, hanya bisa SELF_ONLY (privat).

Sesuai pedoman berbagi konten TikTok, layar review Studio menampilkan nama kreator, pilihan privasi dari
creator_info (tanpa pilihan bawaan), izin komentar/duet/stitch, dan pengungkapan konten bermerek.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from unggah import _baca, _tulis_rahasia, folder_rahasia, log

URL_IZIN = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
SCOPE = "user.info.basic,video.upload,video.publish,video.list"
POTONGAN = 10 * 1024 * 1024  # 5–64 MB per potongan sesuai dokumentasi
MIN_POTONGAN = 5 * 1024 * 1024


def baca_klien(data: Path) -> dict:
    return _baca(folder_rahasia(data) / "tiktok-klien.json")


def simpan_klien(data: Path, client_key: str, client_secret: str) -> None:
    client_key, client_secret = client_key.strip(), client_secret.strip()
    if not client_key or not client_secret or len(client_key) > 100 or len(client_secret) > 200:
        raise ValueError("Isi Client key dan Client secret dari TikTok for Developers.")
    _tulis_rahasia(folder_rahasia(data) / "tiktok-klien.json", {"client_key": client_key, "client_secret": client_secret})


def baca_token(data: Path) -> dict:
    return _baca(folder_rahasia(data) / "tiktok-token.json")


def status(data: Path) -> dict:
    k, t = baca_klien(data), baca_token(data)
    return {"klien": bool(k.get("client_key")), "terhubung": bool(t.get("refresh_token")), "kanal": t.get("nama", ""),
            "scope": t.get("scope", "")}


def mulai_izin(data: Path, redirect: str) -> tuple[str, dict]:
    k = baca_klien(data)
    if not k.get("client_key"):
        raise ValueError("Isi Client key dan Client secret TikTok dulu.")
    verifier = secrets.token_urlsafe(64)
    # Login Kit untuk desktop memakai SHA-256 berformat hex sebagai code_challenge
    tantangan = hashlib.sha256(verifier.encode()).hexdigest()
    state = secrets.token_urlsafe(24)
    url = URL_IZIN + "?" + urllib.parse.urlencode({
        "client_key": k["client_key"], "scope": SCOPE, "response_type": "code", "redirect_uri": redirect,
        "state": state, "code_challenge": tantangan, "code_challenge_method": "S256"})
    return url, {"state": state, "verifier": verifier, "redirect": redirect, "waktu": time.time(), "platform": "tiktok"}


def _form(url: str, badan: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(badan).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        d = _json_galat(e)
    if d.get("error") and d.get("error") not in ("", "ok"):
        raise RuntimeError(f"TikTok menolak: {d.get('error_description') or d.get('error')}")
    return d


def _json_galat(e: urllib.error.HTTPError) -> dict:
    try:
        return json.loads(e.read())
    except Exception:  # noqa: BLE001
        return {"error": {"code": str(e.code), "message": e.reason}}


def _simpan_token(data: Path, d: dict, lama: dict | None = None) -> dict:
    tok = {**(lama or {}), "access_token": d["access_token"], "refresh_token": d.get("refresh_token") or (lama or {}).get("refresh_token"),
           "open_id": d.get("open_id") or (lama or {}).get("open_id"), "scope": d.get("scope") or (lama or {}).get("scope", ""),
           "kedaluwarsa": time.time() + int(d.get("expires_in", 86400)) - 120}
    _tulis_rahasia(folder_rahasia(data) / "tiktok-token.json", tok)
    return tok


def tukar_kode(data: Path, sesi: dict, kode: str) -> None:
    k = baca_klien(data)
    d = _form(f"{API}/oauth/token/", {"client_key": k["client_key"], "client_secret": k["client_secret"], "code": kode,
                                       "grant_type": "authorization_code", "redirect_uri": sesi["redirect"],
                                       "code_verifier": sesi["verifier"]})
    tok = _simpan_token(data, d)
    try:
        info = info_kreator(data)
        tok["nama"] = info.get("creator_nickname") or info.get("creator_username", "")
        _tulis_rahasia(folder_rahasia(data) / "tiktok-token.json", tok)
    except Exception:  # noqa: BLE001 — scope video.publish mungkin belum disetujui
        pass


def token_segar(data: Path) -> str:
    tok = baca_token(data)
    if not tok.get("refresh_token"):
        raise RuntimeError("Akun TikTok belum disambungkan. Buka Pengaturan → Akun unggah.")
    if time.time() < tok.get("kedaluwarsa", 0):
        return tok["access_token"]
    k = baca_klien(data)
    d = _form(f"{API}/oauth/token/", {"client_key": k["client_key"], "client_secret": k["client_secret"],
                                       "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    return _simpan_token(data, d, tok)["access_token"]


def hapus_token(data: Path) -> None:
    tok, k = baca_token(data), baca_klien(data)
    if tok.get("access_token") and k.get("client_key"):
        try:
            _form(f"{API}/oauth/revoke/", {"client_key": k["client_key"], "client_secret": k["client_secret"], "token": tok["access_token"]})
        except Exception:  # noqa: BLE001
            pass
    (folder_rahasia(data) / "tiktok-token.json").unlink(missing_ok=True)


def _api(data: Path, jalur: str, badan: dict) -> dict:
    req = urllib.request.Request(f"{API}{jalur}", data=json.dumps(badan).encode(), method="POST", headers={
        "Authorization": f"Bearer {token_segar(data)}", "Content-Type": "application/json; charset=UTF-8"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        d = _json_galat(e)
    err = d.get("error") or {}
    if err.get("code") not in (None, "", "ok"):
        pesan = {"spam_risk_too_many_posts": "Batas posting TikTok harian tercapai. Coba lagi besok.",
                 "spam_risk_user_banned_from_posting": "Akun ini sedang dibatasi TikTok untuk posting.",
                 "unaudited_client_can_only_post_to_private_accounts": "Aplikasi TikTok-mu belum diaudit: hanya bisa posting privat (SELF_ONLY).",
                 "privacy_level_option_mismatch": "Pilihan privasi tidak tersedia untuk akun ini.",
                 "scope_not_authorized": "Izin belum diberikan. Sambungkan ulang akun TikTok."}.get(err.get("code"))
        raise RuntimeError(pesan or f"TikTok galat {err.get('code')}: {err.get('message', '')}")
    return d.get("data") or {}


def info_kreator(data: Path) -> dict:
    """Wajib dipanggil sebelum Direct Post: nama kreator, pilihan privasi, batas durasi, setelan interaksi."""
    return _api(data, "/post/publish/creator_info/query/", {})


def _rencana_potongan(ukuran: int) -> tuple[int, int]:
    if ukuran < MIN_POTONGAN:
        return ukuran, 1
    return POTONGAN, max(ukuran // POTONGAN, 1)  # potongan terakhir menampung sisa (maks 128 MB)


def unggah_tiktok(data: Path, berkas: Path, meta: dict) -> dict:
    if not berkas.is_file():
        raise RuntimeError(f"Video tidak ditemukan: {berkas.name}")
    ukuran = berkas.stat().st_size
    besar, jumlah = _rencana_potongan(ukuran)
    sumber = {"source": "FILE_UPLOAD", "video_size": ukuran, "chunk_size": besar, "total_chunk_count": jumlah}
    mode = meta.get("mode", "draf")
    if mode == "langsung":
        info = info_kreator(data)
        privasi = meta.get("privasi")
        if privasi not in (info.get("privacy_level_options") or []):
            raise RuntimeError("Pilih privasi TikTok dari pilihan yang tersedia untuk akunmu.")
        durasi = float(meta.get("durasi") or 0)
        if info.get("max_video_post_duration_sec") and durasi > info["max_video_post_duration_sec"]:
            raise RuntimeError(f"Video {durasi:.0f} dtk melebihi batas akun ({info['max_video_post_duration_sec']} dtk).")
        post = {"title": str(meta.get("teks", ""))[:2200], "privacy_level": privasi,
                "disable_comment": bool(meta.get("tanpa_komentar") or info.get("comment_disabled")),
                "disable_duet": bool(meta.get("tanpa_duet") or info.get("duet_disabled")),
                "disable_stitch": bool(meta.get("tanpa_stitch") or info.get("stitch_disabled")),
                "brand_content_toggle": bool(meta.get("konten_bermerek")),
                "brand_organic_toggle": bool(meta.get("promosi_sendiri"))}
        if meta.get("cover_ms") is not None:
            post["video_cover_timestamp_ms"] = int(meta["cover_ms"])
        awal = _api(data, "/post/publish/video/init/", {"post_info": post, "source_info": sumber})
    else:
        awal = _api(data, "/post/publish/inbox/video/init/", {"source_info": sumber})
    publish_id, url = awal["publish_id"], awal["upload_url"]
    log(f"Mengunggah {berkas.name} ({ukuran / 2**20:.1f} MB) ke TikTok ({'posting langsung' if mode == 'langsung' else 'draf'})…")
    with open(berkas, "rb") as fh:
        for i in range(jumlah):
            mulai = i * besar
            akhir = ukuran - 1 if i == jumlah - 1 else mulai + besar - 1
            fh.seek(mulai)
            blok = fh.read(akhir - mulai + 1)
            req = urllib.request.Request(url, data=blok, method="PUT", headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(blok)), "Content-Range": f"bytes {mulai}-{akhir}/{ukuran}"})
            for coba in range(4):
                try:
                    urllib.request.urlopen(req, timeout=180).close()
                    break
                except urllib.error.HTTPError as e:
                    if e.code in (200, 201, 206):
                        break
                    if e.code < 500 or coba == 3:
                        raise RuntimeError(f"TikTok menolak potongan video ({e.code}).") from None
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    if coba == 3:
                        raise
                time.sleep(2 ** coba)
            log(f"Terkirim {(i + 1) * 100 // jumlah}%")
    st = {}
    for _ in range(90):  # tunggu TikTok memproses (maks ±3 menit)
        st = _api(data, "/post/publish/status/fetch/", {"publish_id": publish_id})
        if st.get("status") in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX", "FAILED"):
            break
        time.sleep(2)
    if st.get("status") == "FAILED":
        raise RuntimeError(f"TikTok gagal memproses video: {st.get('fail_reason', 'tanpa alasan')}")
    id_post = (st.get("publicaly_available_post_id") or [None])[0]
    nama = baca_token(data).get("nama", "")
    return {"platform": "tiktok", "id": str(id_post or publish_id), "publish_id": publish_id,
            "url": f"https://www.tiktok.com/@{nama}/video/{id_post}" if id_post and nama else "",
            "privasi": meta.get("privasi", "draf") if mode == "langsung" else "draf",
            "status": st.get("status", "PROCESSING"), "judul": str(meta.get("teks", ""))[:100],
            "waktu": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def statistik(data: Path, id_video: list[str]) -> dict:
    """views per video (scope video.list). Hanya video publik yang sudah punya id posting."""
    ids = [i for i in id_video if i and i.isdigit()][:20]
    if not ids:
        return {}
    req = urllib.request.Request(f"{API}/video/query/?fields=id,view_count,like_count,comment_count,share_count",
                                 data=json.dumps({"filters": {"video_ids": ids}}).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token_segar(data)}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        vids = (json.loads(r.read()).get("data") or {}).get("videos") or []
    return {v["id"]: {"views": v.get("view_count", 0), "suka": v.get("like_count", 0), "komentar": v.get("comment_count", 0),
                      "bagikan": v.get("share_count", 0)} for v in vids}

