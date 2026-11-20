# kredit-maliki: Template oleh Maliki Saputra (@malikisaputra) — https://malikisaputra.biz.id · Lisensi Nonkomersial Maliki Studio 1.0 (alat/LISENSI.md): dilarang dijual ulang.
"""Pemantau live: deteksi kanal yang sedang live lewat API resmi, lalu (bila diizinkan) rekam dan cari momen.

- Twitch: Helix GET /streams (token aplikasi client-credentials).
- YouTube: RSS kanal (tanpa kuota) + Data API videos.list liveStreamingDetails (1 unit per cek).
- Kick: Public API GET /public/v1/channels (token aplikasi client-credentials).

Hanya kanal yang punya **bukti izin** (program clipper resmi, kampanye, atau izin tertulis) yang boleh direkam
otomatis. Kandidat klip hasil rekaman tidak pernah diunggah otomatis: selalu lewat analisis, render, dan review.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

from unggah import _baca, _tulis_rahasia, folder_rahasia

PLATFORM = ("twitch", "youtube", "kick")
SELANG_CEK = 120  # detik antar-pengecekan semua kanal
TWITCH_TOKEN, TWITCH_API = "https://id.twitch.tv/oauth2/token", "https://api.twitch.tv/helix"
KICK_TOKEN, KICK_API = "https://id.kick.com/oauth/token", "https://api.kick.com/public/v1"
YT_API = "https://www.googleapis.com/youtube/v3"
YT_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id="


def _json(req: urllib.request.Request | str, timeout: int = 20) -> dict:
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


class Pemantau:
    def __init__(self, data: Path, jalankan_rekam: Callable[[dict, Path], subprocess.Popen],
                 token_youtube: Callable[[], str] | None = None):
        self.data = data
        self.folder = data / "live"
        self.berkas = self.folder / "kanal.json"
        self.jalankan_rekam = jalankan_rekam  # fungsi(kanal, folder sesi) → Popen perekam
        self.token_youtube = token_youtube
        self.kunci = threading.RLock()
        self.rekaman: dict[str, dict] = {}  # id kanal → {"proc", "folder", "mulai"}
        self._token: dict[str, tuple[str, float]] = {}
        self.galat: dict[str, str] = {}

    # ── penyimpanan ──
    def kanal(self) -> list[dict]:
        try:
            return json.loads(self.berkas.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _simpan(self, daftar: list[dict]):
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.berkas.with_suffix(".tmp")
        tmp.write_text(json.dumps(daftar, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.berkas)

    def kunci_api(self) -> dict:
        return _baca(folder_rahasia(self.data) / "live-kunci.json")

    def simpan_kunci(self, d: dict) -> dict:
        k = self.kunci_api()
        for f in ("twitch_id", "twitch_rahasia", "kick_id", "kick_rahasia", "youtube_kunci"):
            if f in d and d[f] is not None:
                v = str(d[f]).strip()[:200]
                if v:
                    k[f] = v
                else:
                    k.pop(f, None)
        _tulis_rahasia(folder_rahasia(self.data) / "live-kunci.json", k)
        self._token.clear()
        return self.status_kunci()

    def status_kunci(self) -> dict:
        k = self.kunci_api()
        return {"twitch": bool(k.get("twitch_id") and k.get("twitch_rahasia")), "kick": bool(k.get("kick_id") and k.get("kick_rahasia")),
                "youtube": bool(k.get("youtube_kunci")) or (bool(self.token_youtube) and (folder_rahasia(self.data) / "youtube-token.json").exists())}

    def simpan_kanal(self, d: dict) -> dict:
        platform = str(d.get("platform", ""))
        if platform not in PLATFORM:
            raise ValueError("Platform live harus twitch, youtube, atau kick.")
        nama = str(d.get("nama", "")).strip()
        if platform == "twitch":
            nama = nama.lower().lstrip("@").split("/")[-1]
            if not re.fullmatch(r"[a-z0-9_]{3,25}", nama):
                raise ValueError("Nama login Twitch tidak valid.")
        elif platform == "kick":
            nama = nama.lower().lstrip("@").split("/")[-1]
            if not re.fullmatch(r"[a-z0-9_-]{2,40}", nama):
                raise ValueError("Slug kanal Kick tidak valid.")
        else:
            nama = nama.split("/")[-1]
            if not re.fullmatch(r"UC[\w-]{22}|@[\w.-]{3,40}", nama):
                raise ValueError("Isi ID kanal YouTube (UC…) atau handle (@nama).")
            if nama.startswith("@"):
                nama = self._yt_handle(nama)
        izin = str(d.get("izin", "")).strip()[:300]
        auto = bool(d.get("auto_rekam"))
        if auto and not izin:
            raise ValueError("Rekam otomatis hanya untuk kanal yang memberi izin. Isi bukti izinnya dulu.")
        with self.kunci:
            daftar = self.kanal()
            k = next((x for x in daftar if x["id"] == d.get("id")), None) or \
                next((x for x in daftar if x["platform"] == platform and x["nama"] == nama), None)
            if not k:
                k = {"id": secrets.token_hex(5), "dibuat": time.time(), "live": False, "judul_live": "", "terakhir_cek": 0}
                daftar.append(k)
            k.update(platform=platform, nama=nama, label=str(d.get("label") or nama)[:60], izin=izin, auto_rekam=auto,
                     kampanye=str(d.get("kampanye") or ""), batas_jam=min(max(float(d.get("batas_jam") or 4), 0.25), 12))
            self._simpan(daftar)
            return k

    def hapus_kanal(self, id_: str):
        self.hentikan(id_)
        with self.kunci:
            self._simpan([k for k in self.kanal() if k["id"] != id_])

    # ── token aplikasi ──
    def _token_aplikasi(self, platform: str) -> str:
        t = self._token.get(platform)
        if t and t[1] > time.time():
            return t[0]
        k = self.kunci_api()
        url, cid, rahasia = (TWITCH_TOKEN, k.get("twitch_id"), k.get("twitch_rahasia")) if platform == "twitch" else \
            (KICK_TOKEN, k.get("kick_id"), k.get("kick_rahasia"))
        if not cid or not rahasia:
            raise RuntimeError(f"Isi Client ID dan secret {platform.title()} di halaman Live.")
        badan = urllib.parse.urlencode({"client_id": cid, "client_secret": rahasia, "grant_type": "client_credentials"}).encode()
        d = _json(urllib.request.Request(url, data=badan, headers={"Content-Type": "application/x-www-form-urlencoded"}))
        self._token[platform] = (d["access_token"], time.time() + int(d.get("expires_in", 3600)) - 120)
        return d["access_token"]

    # ── cek per platform ──
    def _cek_twitch(self, nama: list[str]) -> dict[str, dict]:
        out = {}
        tok, cid = self._token_aplikasi("twitch"), self.kunci_api()["twitch_id"]
        for i in range(0, len(nama), 100):
            q = "&".join("user_login=" + urllib.parse.quote(n) for n in nama[i:i + 100])
            d = _json(urllib.request.Request(f"{TWITCH_API}/streams?{q}", headers={"Client-Id": cid, "Authorization": f"Bearer {tok}"}))
            for s in d.get("data", []):
                if s.get("type") == "live":
                    out[s["user_login"].lower()] = {"judul": s.get("title", ""), "mulai": s.get("started_at", ""),
                                                    "url": f"https://www.twitch.tv/{s['user_login']}", "penonton": s.get("viewer_count", 0)}
        return out

    def _cek_kick(self, nama: list[str]) -> dict[str, dict]:
        out = {}
        tok = self._token_aplikasi("kick")
        for i in range(0, len(nama), 50):
            q = "&".join("slug=" + urllib.parse.quote(n) for n in nama[i:i + 50])
            d = _json(urllib.request.Request(f"{KICK_API}/channels?{q}", headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"}))
            for c in d.get("data", []):
                st = c.get("stream") or {}
                if st.get("is_live"):
                    out[c.get("slug", "").lower()] = {"judul": c.get("stream_title", ""), "mulai": st.get("start_time", ""),
                                                      "url": f"https://kick.com/{c.get('slug')}", "penonton": st.get("viewer_count", 0)}
        return out

    def _yt_auth(self) -> tuple[dict, str]:
        k = self.kunci_api()
        if k.get("youtube_kunci"):
            return {}, "&key=" + urllib.parse.quote(k["youtube_kunci"])
        if self.token_youtube and (folder_rahasia(self.data) / "youtube-token.json").exists():
            return {"Authorization": f"Bearer {self.token_youtube()}"}, ""
        raise RuntimeError("Isi API key YouTube di halaman Live, atau sambungkan akun YouTube.")

    def _yt_handle(self, handle: str) -> str:
        h, q = self._yt_auth()
        d = _json(urllib.request.Request(f"{YT_API}/channels?part=id&forHandle={urllib.parse.quote(handle)}{q}", headers=h))
        if not d.get("items"):
            raise ValueError(f"Kanal YouTube {handle} tidak ditemukan.")
        return d["items"][0]["id"]

    def _cek_youtube(self, kanal: list[str]) -> dict[str, dict]:
        out, calon = {}, {}
        for cid in kanal:  # RSS tidak memakai kuota API
            try:
                with urllib.request.urlopen(YT_RSS + urllib.parse.quote(cid), timeout=20) as r:
                    akar = ET.fromstring(r.read())
            except Exception:  # noqa: BLE001
                continue
            ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
            for e in akar.findall("a:entry", ns)[:5]:
                vid = e.findtext("yt:videoId", namespaces=ns)
                if vid:
                    calon[vid] = cid
        if not calon:
            return out
        h, q = self._yt_auth()
        ids = list(calon)
        for i in range(0, len(ids), 50):
            d = _json(urllib.request.Request(f"{YT_API}/videos?part=snippet,liveStreamingDetails&id={','.join(ids[i:i + 50])}{q}", headers=h))
            for v in d.get("items", []):
                ls = v.get("liveStreamingDetails") or {}
                if ls.get("actualStartTime") and not ls.get("actualEndTime"):
                    out[calon[v["id"]]] = {"judul": v["snippet"].get("title", ""), "mulai": ls["actualStartTime"],
                                           "url": f"https://www.youtube.com/watch?v={v['id']}", "penonton": int(ls.get("concurrentViewers", 0) or 0)}
        return out

    def cek(self) -> list[dict]:
        """Cek semua kanal sekali. Mengembalikan daftar kanal terbaru."""
        with self.kunci:
            daftar = self.kanal()
        per = {p: [k["nama"] for k in daftar if k["platform"] == p] for p in PLATFORM}
        hasil: dict[str, dict] = {}
        for p, nama in per.items():
            if not nama:
                continue
            try:
                hasil[p] = {"twitch": self._cek_twitch, "kick": self._cek_kick, "youtube": self._cek_youtube}[p](nama)
                self.galat.pop(p, None)
            except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, KeyError, ValueError) as e:
                self.galat[p] = str(e)[:200]
        sekarang = time.time()
        with self.kunci:
            daftar = self.kanal()
            for k in daftar:
                if k["platform"] not in hasil:
                    continue
                info = hasil[k["platform"]].get(k["nama"].lower() if k["platform"] != "youtube" else k["nama"])
                k.update(live=bool(info), terakhir_cek=sekarang, judul_live=(info or {}).get("judul", ""),
                         url_live=(info or {}).get("url", ""), penonton=(info or {}).get("penonton", 0))
                if info and k.get("auto_rekam") and k.get("izin") and k["id"] not in self.rekaman:
                    self.mulai_rekam(k)
            self._simpan(daftar)
        return daftar

    # ── rekaman ──
    def mulai_rekam(self, k: dict) -> dict:
        if not k.get("izin"):
            raise ValueError("Kanal ini belum punya bukti izin. Rekam hanya untuk kanal yang mengizinkan.")
        if not k.get("live") or not k.get("url_live"):
            raise ValueError("Kanal sedang tidak live.")
        with self.kunci:
            r = self.rekaman.get(k["id"])
            if r and r["proc"].poll() is None:
                return {"folder": str(r["folder"])}
            folder = self.folder / k["id"] / time.strftime("%Y%m%d-%H%M%S")
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "sesi.json").write_text(json.dumps({"kanal": k, "mulai": time.time()}, ensure_ascii=False), encoding="utf-8")
            proc = self.jalankan_rekam(k, folder)
            self.rekaman[k["id"]] = {"proc": proc, "folder": folder, "mulai": time.time()}
            return {"folder": str(folder)}

    def hentikan(self, id_: str) -> bool:
        r = self.rekaman.get(id_)
        if not r or r["proc"].poll() is not None:
            return False
        (r["folder"] / "BERHENTI").write_text("1")  # perekam berhenti rapi dan memotong kandidat terakhir
        try:
            r["proc"].wait(timeout=60)
        except subprocess.TimeoutExpired:
            r["proc"].terminate()
        return True

    def hentikan_semua(self):
        for id_ in list(self.rekaman):
            self.hentikan(id_)

    def ringkas(self) -> dict:
        with self.kunci:
            daftar = self.kanal()
            for k in daftar:
                r = self.rekaman.get(k["id"])
                k["merekam"] = bool(r and r["proc"].poll() is None)
            return {"kanal": daftar, "kunci": self.status_kunci(), "galat": self.galat, "kandidat": self.kandidat()}

    def kandidat(self) -> list[dict]:
        out = []
        for f in sorted(self.folder.glob("*/*/kandidat.json"), reverse=True)[:30]:
            try:
                for c in json.loads(f.read_text(encoding="utf-8")):
                    if (f.parent / c["file"]).exists() and not c.get("dibuang"):
                        out.append({**c, "kanal": f.parent.parent.name, "sesi": f.parent.name})
            except (OSError, ValueError, KeyError):
                continue
        return sorted(out, key=lambda c: c.get("waktu", 0), reverse=True)[:100]

    def berkas_kandidat(self, kanal: str, sesi: str, file: str) -> Path:
        if not (re.fullmatch(r"[0-9a-f]{10}", kanal) and re.fullmatch(r"\d{8}-\d{6}", sesi) and re.fullmatch(r"kandidat-\d{3,}\.mp4", file)):
            raise ValueError("Kandidat tidak ditemukan.")
        p = (self.folder / kanal / sesi / file).resolve()
        if self.folder.resolve() not in p.parents or not p.is_file():
            raise ValueError("Kandidat tidak ditemukan.")
        return p

    def buang_kandidat(self, kanal: str, sesi: str, file: str):
        p = self.berkas_kandidat(kanal, sesi, file)
        f = p.parent / "kandidat.json"
        daftar = json.loads(f.read_text(encoding="utf-8"))
        for c in daftar:
            if c["file"] == p.name:
                c["dibuang"] = True
        f.write_text(json.dumps(daftar, ensure_ascii=False), encoding="utf-8")
        p.unlink(missing_ok=True)

    def mulai(self, selang: int = SELANG_CEK):
        def putar():
            while True:
                try:
                    if self.kanal():
                        self.cek()
                except Exception as e:  # noqa: BLE001 — pemantau tidak boleh mati
                    self.galat["umum"] = str(e)[:200]
                time.sleep(selang)
        threading.Thread(target=putar, daemon=True).start()


def ruang_kosong_gb(p: Path) -> float:
    try:
        return shutil.disk_usage(p).free / 2 ** 30
    except OSError:
        return 0.0
