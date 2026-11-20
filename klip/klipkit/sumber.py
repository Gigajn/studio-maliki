"""Sumber video (yt-dlp/file), info video, energi audio."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

import numpy as np
from .umum import *  # noqa: F401,F403
from .umum import AKAR

# ── 1. Sumber video ─────────────────────────────────────────────────────────
DIBLOKIR = ("not a bot", "sign in to confirm", "confirm your age", "login_required", "429")
PESAN_BLOKIR = ("YouTube meminta verifikasi (\"bukan bot\") dan menolak unduhan dari koneksi ini. Coba: matikan VPN/proxy, "
                "ganti jaringan (Wi-Fi/hotspot), atau unduh videonya sendiri dan seret file-nya ke Studio. "
                "Bila perlu, izinkan Studio meminjam login YouTube dari browsermu di Pengaturan AI → Unduhan YouTube.")


def _browser_cookies() -> list[str]:
    """Browser yang cookies YouTube-nya BOLEH dipinjam yt-dlp. Hanya bila pengguna mengaktifkannya sendiri di
    Pengaturan (KLIP_COOKIES_BROWSER), karena menyangkut privasi dan akun bisa ditandai bot oleh YouTube."""
    import os
    pilih = os.environ.get("KLIP_COOKIES_BROWSER", "")
    boleh = {"firefox", "chrome", "chromium", "edge", "brave", "safari", "opera", "vivaldi"}
    return [b.strip().lower() for b in pilih.split(",") if b.strip().lower() in boleh]


def _unduh(yt_dlp, url: str, opsi: dict) -> dict:
    """Unduh tanpa login; bila YouTube memblokir ("bukan bot"), coba ulang memakai cookies file (KLIP_COOKIES, untuk
    server) atau cookies browser pengguna. Cookies hanya dibaca lokal oleh yt-dlp, tidak disimpan/dikirim ke tempat lain."""
    import os
    from yt_dlp.utils import DownloadError
    percobaan = [{}]
    if os.environ.get("KLIP_COOKIES") and Path(os.environ["KLIP_COOKIES"]).exists():
        percobaan.insert(0, {"cookiefile": os.environ["KLIP_COOKIES"]})
    percobaan += [{"cookiesfrombrowser": (b,)} for b in _browser_cookies()]
    galat = ""
    for i, tambah in enumerate(percobaan):
        if i and not any(k in galat.lower() for k in DIBLOKIR):
            break  # gagal bukan karena blokir → cookies tidak membantu
        if "cookiesfrombrowser" in tambah:
            log(f"YouTube meminta verifikasi, mencoba memakai login YouTube di {tambah['cookiesfrombrowser'][0]}…")
        try:
            with yt_dlp.YoutubeDL({**opsi, **tambah}) as ydl:
                return ydl.extract_info(url, download=True)
        except DownloadError as e:
            if "cookiesfrombrowser" in tambah and not any(k in str(e).lower() for k in DIBLOKIR):
                continue  # browser tidak ada / cookies tak terbaca → browser berikutnya
            galat = str(e)
        except Exception as e:  # noqa: BLE001  (browser tidak terpasang, database terkunci, dll.)
            if "cookiesfrombrowser" not in tambah:
                raise RuntimeError(f"Gagal mengunduh video: {e}") from e
    if any(k in galat.lower() for k in DIBLOKIR):
        raise RuntimeError(PESAN_BLOKIR)
    raise RuntimeError("Gagal mengunduh video: " + re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)*", "", galat).strip()[:300])


def _runtime_js() -> dict:
    """Deno bawaan paket pip `deno` (ikut installer) → tidak bergantung Node.js di komputer pengguna.
    Tanpa runtime JS, YouTube menolak sebagian besar format (laporan: Windows 'gagal mengunduh')."""
    rt: dict = {}
    try:
        import deno
        rt["deno"] = {"path": deno.find_deno_bin()}
    except Exception:
        rt["deno"] = {}
    rt["node"] = {}
    return rt


def ambil_sumber(sumber: str, kerja: Path) -> tuple[Path, list[dict], str]:
    """Kembalikan (file video, heatmap YouTube atau [], judul)."""
    if re.match(r"^https?://", sumber):
        import yt_dlp
        log("Mengunduh video (maks 1080p) dengan yt-dlp…")
        opsi = {
            "outtmpl": str(kerja / "sumber.%(ext)s"),
            "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b",
            "merge_output_format": "mp4",
            "ffmpeg_location": ffmpeg(),
            "quiet": True, "no_warnings": True, "noprogress": True,
            "js_runtimes": _runtime_js(),  # yt-dlp butuh runtime JS untuk tantangan YouTube
        }
        info = _unduh(yt_dlp, sumber, opsi)
        file = next(kerja.glob("sumber.*"))
        return file, info.get("heatmap") or [], info.get("title") or "video"
    p = Path(sumber).expanduser().resolve()
    if not p.exists():
        sys.exit(f"File tidak ditemukan: {p}")
    return p, [], p.stem


def info_video(file: Path) -> tuple[float, int, int]:
    import cv2
    cap = cv2.VideoCapture(str(file))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return (n / fps if n else 0.0), w, h


# ── 2. Energi audio per detik ───────────────────────────────────────────────
def energi_audio(file: Path) -> np.ndarray:
    sr = 16000
    raw = subprocess.run(
        [ffmpeg(), "-v", "error", "-i", str(file), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
        capture_output=True, check=True).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    n = len(a) // sr
    if n == 0:
        return np.zeros(1)
    rms = np.sqrt((a[: n * sr].reshape(n, sr) ** 2).mean(axis=1))
    return (rms - rms.min()) / (np.ptp(rms) + 1e-9)
