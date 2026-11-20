# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = [
#   "yt-dlp[default]>=2026.10.26",
#   "imageio-ffmpeg>=0.6.0",
# ]
# ///
# kredit-maliki: Template oleh Maliki Saputra (@malikisaputra) — https://malikisaputra.biz.id · Lisensi Nonkomersial Maliki Studio 1.0 (alat/LISENSI.md): dilarang dijual ulang.
"""Rekam live yang sudah diizinkan dan potong kandidat momen secara otomatis.

Dipanggil oleh Studio (halaman Live). Cara kerja:
1. Ambil alamat siaran lewat yt-dlp, rekam dengan ffmpeg tanpa encode ulang, per potongan 60 detik.
2. Hitung energi suara per detik dari tiap potongan yang selesai. Untuk Twitch, baca chat publik secara
   anonim (read-only) dan hitung pesan per 5 detik.
3. Momen = lonjakan chat atau lonjakan suara yang jauh di atas biasanya. Jeda minimal 90 detik antar-momen.
4. Tiap momen dipotong jadi kandidat-XXX.mp4 (30 detik sebelum puncak sampai 15 detik sesudahnya)
   dan dicatat di kandidat.json. Kandidat TIDAK diunggah: pengguna memprosesnya jadi klip dan me-review dulu.
5. Potongan lama (>15 menit) dihapus supaya disk tidak penuh.
Berhenti saat file BERHENTI muncul, siaran selesai, atau batas jam tercapai.

  uv run rekam_live.py --url https://www.twitch.tv/nama --folder /jalur/sesi --platform twitch --nama nama
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

SEGMEN = int(os.environ.get("REKAM_SEGMEN") or 60)  # detik per potongan rekaman (bisa dikecilkan untuk uji)
SEBELUM, SESUDAH = 30, 15
JEDA_MOMEN = 90      # detik minimal antar-kandidat
SIMPAN_MENIT = 15    # potongan lebih tua dari ini dihapus
LAJUR_CHAT = 5       # detik per keranjang chat


def log(msg: str) -> None:
    print(f"• {msg}", flush=True)


def ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        import shutil
        return shutil.which("ffmpeg") or "ffmpeg"


# ── deteksi momen (murni, tanpa dependensi supaya mudah diuji) ──
def lonjakan(nilai: list[float], kali: float = 3.0, jendela: int = 120, minimum: float = 0.0, dari: int = 0) -> list[tuple[int, float]]:
    """Indeks (≥ dari) yang nilainya > kali × median jendela sebelumnya (dan > minimum). Mengembalikan (indeks, rasio)."""
    out = []
    for i in range(max(0, dari), len(nilai)):
        v = nilai[i]
        lalu = nilai[max(0, i - jendela):i]
        if len(lalu) < 6 or v <= minimum:
            continue
        med = statistics.median(lalu) or 1e-9
        if v > kali * med:
            out.append((i, v / med))
    return out


def cari_momen(energi: list[float], chat: list[int], sudah: list[float], jeda: float = JEDA_MOMEN, dari: float = 0) -> list[dict]:
    """energi: RMS per detik sejak awal rekaman. chat: jumlah pesan per LAJUR_CHAT detik.
    sudah: waktu momen yang sudah diambil. Mengembalikan momen baru {waktu, alasan, skor}."""
    calon = [{"waktu": float(i), "alasan": "suara", "skor": round(min(r / 6, 1.0), 3)} for i, r in lonjakan(energi, 3.0, 300, dari=int(dari))]
    calon += [{"waktu": float(i * LAJUR_CHAT + LAJUR_CHAT / 2), "alasan": "chat", "skor": round(min(r / 5, 1.0), 3)}
              for i, r in lonjakan([float(c) for c in chat], 3.0, 60, minimum=4, dari=int(dari // LAJUR_CHAT))]
    calon.sort(key=lambda m: -m["skor"])
    ambil, baru = list(sudah), []
    for m in calon:
        if all(abs(m["waktu"] - t) >= jeda for t in ambil):
            ambil.append(m["waktu"])
            baru.append(m)
    return sorted(baru, key=lambda m: m["waktu"])


# ── chat Twitch anonim ──
class ChatTwitch(threading.Thread):
    def __init__(self, kanal: str, t0: float):
        super().__init__(daemon=True)
        self.kanal, self.t0 = kanal.lower(), t0
        self.hitung: dict[int, int] = {}
        self.berhenti = False

    def run(self):
        while not self.berhenti:
            try:
                s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=30)
                s.sendall(f"NICK justinfan{int(time.time()) % 100000}\r\nJOIN #{self.kanal}\r\n".encode())
                sisa = b""
                while not self.berhenti:
                    data = s.recv(4096)
                    if not data:
                        break
                    sisa += data
                    *baris, sisa = sisa.split(b"\r\n")
                    for b in baris:
                        if b.startswith(b"PING"):
                            s.sendall(b"PONG" + b[4:] + b"\r\n")
                        elif b" PRIVMSG " in b:
                            k = int((time.time() - self.t0) // LAJUR_CHAT)
                            self.hitung[k] = self.hitung.get(k, 0) + 1
            except OSError:
                time.sleep(10)

    def deret(self, sampai_detik: float) -> list[int]:
        n = int(sampai_detik // LAJUR_CHAT)
        return [self.hitung.get(i, 0) for i in range(n)]


# ── rekaman ──
def alamat_siaran(url: str) -> str:
    if Path(url).is_file():  # file lokal: untuk uji, diputar dengan kecepatan asli seolah siaran
        return url
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "format": "best[height<=1080]/best"}) as y:
        info = y.extract_info(url, download=False)
    if not info.get("is_live", True) and info.get("live_status") not in (None, "is_live"):
        raise RuntimeError("Siaran sudah tidak live.")
    return info["url"]


def energi_segmen(ff: str, berkas: Path) -> list[float]:
    """RMS suara per detik (PCM 8 kHz mono)."""
    r = subprocess.run([ff, "-v", "error", "-i", str(berkas), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                       capture_output=True, timeout=120)
    raw, out = r.stdout, []
    for i in range(0, len(raw) - 1, 16000):
        blok = memoryview(raw[i:i + 16000]).cast("h")
        out.append((sum(x * x for x in blok[::4]) / max(len(blok[::4]), 1)) ** 0.5)
    return out


def potong(ff: str, folder: Path, segmen: list[Path], mulai: float, durasi: float, keluar: Path) -> bool:
    daftar = folder / "potong.txt"
    daftar.write_text("".join(f"file '{p.name}'\n" for p in segmen), encoding="utf-8")
    r = subprocess.run([ff, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(daftar), "-ss", f"{mulai:.2f}",
                        "-t", f"{durasi:.2f}", "-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", str(keluar)],
                       capture_output=True, timeout=300)
    daftar.unlink(missing_ok=True)
    return r.returncode == 0 and keluar.exists() and keluar.stat().st_size > 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--folder", required=True)
    ap.add_argument("--platform", default="")
    ap.add_argument("--nama", default="")
    ap.add_argument("--batas-jam", type=float, default=4)
    a = ap.parse_args()
    folder = Path(a.folder)
    folder.mkdir(parents=True, exist_ok=True)
    ff = ffmpeg()
    log(f"Mencari alamat siaran {a.url}")
    sumber = alamat_siaran(a.url)
    t0 = time.time()
    lokal = ["-re"] if Path(sumber).is_file() else []
    rek = subprocess.Popen([ff, "-v", "error", *lokal, "-i", sumber, "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
                            "-f", "segment", "-segment_time", str(SEGMEN), "-segment_format", "mpegts",
                            "-reset_timestamps", "1", str(folder / "seg-%05d.ts")], stdin=subprocess.PIPE)
    chat = None
    if a.platform == "twitch" and a.nama:
        chat = ChatTwitch(a.nama, t0)
        chat.start()
    log("Merekam. Kandidat momen akan muncul di halaman Live.")
    energi: list[float] = []
    diproses = 0
    momen: list[dict] = []
    kandidat_f = folder / "kandidat.json"
    kandidat = json.loads(kandidat_f.read_text(encoding="utf-8")) if kandidat_f.exists() else []

    def segmen_selesai(akhir: bool) -> list[Path]:
        s = sorted(folder.glob("seg-*.ts"))
        return s if akhir else s[:-1]

    def langkah(akhir: bool = False):
        nonlocal diproses
        selesai = segmen_selesai(akhir)
        for p in selesai:
            no = int(p.stem.split("-")[1])
            if no < diproses:
                continue
            e = energi_segmen(ff, p)
            energi.extend(e + [0.0] * max(0, SEGMEN - len(e)) if not akhir or p != selesai[-1] else e)
            diproses = no + 1
        sampai = diproses * SEGMEN
        deret_chat = chat.deret(sampai) if chat else []
        for m in cari_momen(energi[:sampai], deret_chat, [x["waktu"] for x in momen], dari=sampai - 3 * SEGMEN):
            momen.append({**m, "dipotong": False})
        semua = {int(p.stem.split("-")[1]): p for p in selesai}
        for m in momen:
            if m["dipotong"]:
                continue
            a_, b_ = max(0.0, m["waktu"] - SEBELUM), m["waktu"] + SESUDAH
            i0, i1 = int(a_ // SEGMEN), int(b_ // SEGMEN)
            if i1 >= diproses and not akhir:
                continue  # tunggu potongan sesudahnya selesai
            bagian = [semua[i] for i in range(i0, min(i1, diproses - 1) + 1) if i in semua]
            m["dipotong"] = True
            if not bagian:
                continue
            nama = f"kandidat-{len(kandidat) + 1:03d}.mp4"
            if potong(ff, folder, bagian, a_ - i0 * SEGMEN, b_ - a_, folder / nama):
                kandidat.append({"file": nama, "waktu": time.time() - (sampai - m["waktu"]), "detik": m["waktu"],
                                 "alasan": m["alasan"], "skor": m["skor"], "durasi": round(b_ - a_, 1)})
                tmp = kandidat_f.with_suffix(".tmp")
                tmp.write_text(json.dumps(kandidat, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, kandidat_f)
                log(f"Kandidat baru: {nama} ({m['alasan']}, menit {m['waktu'] / 60:.1f})")
        # buang potongan lama yang tidak dibutuhkan kandidat tertunda
        perlu = min([int(max(0, m["waktu"] - SEBELUM) // SEGMEN) for m in momen if not m["dipotong"]] or [diproses])
        batas = min(perlu, diproses - SIMPAN_MENIT * 60 // SEGMEN)
        for no, p in semua.items():
            if no < batas:
                p.unlink(missing_ok=True)

    alasan = "selesai"
    while True:
        time.sleep(5)
        if (folder / "BERHENTI").exists():
            alasan = "dihentikan"
            break
        if rek.poll() is not None:
            alasan = "siaran berakhir"
            break
        if time.time() - t0 > a.batas_jam * 3600:
            alasan = "batas jam tercapai"
            break
        try:
            langkah()
        except Exception as e:  # noqa: BLE001 — rekaman jalan terus walau satu langkah gagal
            log(f"Peringatan: {e}")
    if rek.poll() is None:
        try:
            rek.stdin.write(b"q")
            rek.stdin.flush()
            rek.wait(timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            rek.terminate()
    if chat:
        chat.berhenti = True
    langkah(akhir=True)
    for p in folder.glob("seg-*.ts"):
        p.unlink(missing_ok=True)
    log(f"Rekaman berhenti ({alasan}). {len(kandidat)} kandidat.")


if __name__ == "__main__":
    sys.exit(main())
