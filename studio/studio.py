# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["pywebview>=5.0"]
# ///
# kredit-maliki: Template oleh Maliki Saputra (@malikisaputra) — https://malikisaputra.biz.id · Lisensi Nonkomersial Maliki Studio 1.0 (alat/LISENSI.md): dilarang dijual ulang.
"""Studio Maliki — server lokal untuk tampilan Klip & Motion.

Hanya mendengar di 127.0.0.1 (tidak bisa dibuka dari perangkat lain).
Jalankan: uv run studio.py            → jendela aplikasi (native)
          uv run studio.py --browser  → buka di browser saja
"""
from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

AKAR = Path(__file__).resolve().parent
sys.path.insert(0, str(AKAR))
import unggah as UNGGAH  # noqa: E402  (pustaka standar saja)
from antrean import Antrean, Kerja  # noqa: E402
import caption as CAPTION  # noqa: E402
import live as LIVE  # noqa: E402
import statistik as STATISTIK  # noqa: E402
import unggah_instagram as IG  # noqa: E402
import unggah_tiktok as TT  # noqa: E402

ALAT = AKAR.parent
KLIP = ALAT / "klip"
if not os.environ.get("STUDIO_PY") and (ALAT / "BUNDEL").exists():
    # Versi installer Windows: <app>\python\python.exe dan <app>\bin\uv.exe di samping folder alat
    _py = ALAT.parent / "python" / ("python.exe" if os.name == "nt" else "bin/python3.12")
    if _py.exists():
        os.environ["STUDIO_PY"] = str(_py)
        os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    _uv = ALAT.parent / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    if _uv.exists():
        os.environ.setdefault("STUDIO_UV", str(_uv))
MOTION = ALAT / "motion"
SUARA = ALAT / "suara"
if os.environ.get("STUDIO_PY"):
    # Versi installer: folder aplikasi hanya-baca → Motion (npm install, naskah, hasil) dijalankan dari salinan di folder data.
    _m = Path(os.environ.get("STUDIO_DATA", Path.home() / "Studio Maliki")) / ".mesin" / "motion"
    try:
        shutil.copytree(MOTION, _m, dirs_exist_ok=True, ignore=shutil.ignore_patterns("node_modules", "naskah", "hasil"))
        # src/Akar.tsx mengimpor naskah/contoh.json (props bawaan) → wajib ada, walau folder naskah lain tidak disalin
        (_m / "naskah").mkdir(parents=True, exist_ok=True)
        # buat.mjs memanggil ../suara/suara.py (VoxCPM) → folder suara harus ikut di sebelah motion
        if SUARA.exists():
            shutil.copytree(SUARA, _m.parent / "suara", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", ".venv"))
        MOTION = _m
    except OSError:
        pass
DATA = Path(os.environ.get("STUDIO_DATA", Path.home() / "Studio Maliki"))
ENV = DATA / "pengaturan.env"
PORT = int(os.environ.get("STUDIO_PORT", "8787"))
KUNCI = ["SUARA", "VOXCPM_MODE", "VOXCPM_DESAIN", "AI", "LANGGANAN", "OPENCODE_MODEL", "CLAUDE_CODE_MODEL", "CODEX_MODEL", "GEMINI_API_KEY", "GEMINI_MODEL",
         "OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL", "SUARA_MAC", "TEMA", "PIXABAY_API_KEY",
         "JEDA_POSTING_MENIT", "KLIP_COOKIES_BROWSER"]
BAWAAN = {"SUARA": "bawaan", "VOXCPM_MODE": "bawaan", "AI": "lokal", "LANGGANAN": "claude-code", "GEMINI_MODEL": "gemini-3.8-flash",
          "OPENAI_BASE_URL": "http://localhost:11434/v1", "OPENAI_MODEL": "gemma3:4b", "SUARA_MAC": "Damayanti"}


def baca_pengaturan() -> dict:
    p = dict(BAWAAN)
    if ENV.exists():
        for baris in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in baris and not baris.startswith("#"):
                k, v = baris.split("=", 1)
                if k in KUNCI:
                    p[k] = v
    return p


def url_ai_aman(url: str) -> bool:
    """Kunci API hanya boleh dikirim lewat https, kecuali ke server di laptop/jaringan rumah sendiri (Ollama, LM Studio)."""
    import ipaddress
    u = urlparse(url)
    if u.scheme == "https" and u.hostname:
        return True
    host = u.hostname or ""
    if u.scheme != "http" or not host:
        return False
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def simpan_pengaturan(baru: dict) -> None:
    p = baca_pengaturan()
    for k in KUNCI:
        if k in baru and baru[k] is not None:
            v = str(baru[k]).strip()
            if re.search(r"[\r\n\x00]", v):  # satu baris per kunci: cegah menyisipkan kunci lain ke file .env
                raise ValueError(f"Isian {k} tidak boleh berisi baris baru.")
            p[k] = v
    if baru.get("OPENAI_BASE_URL") and not url_ai_aman(p["OPENAI_BASE_URL"]):
        raise ValueError("Alamat server AI harus https:// (http hanya untuk localhost) agar kunci API tidak terkirim tanpa enkripsi.")
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = ENV.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("# Pengaturan Studio Maliki (berisi kunci AI — jangan dibagikan)\n" +
                 "\n".join(f"{k}={p.get(k, '')}" for k in KUNCI) + "\n")
    os.replace(tmp, ENV)
    UNGGAH.kunci_pemilik(ENV)


def samarkan(p: dict) -> dict:
    """Kunci tidak pernah dikirim balik utuh ke halaman."""
    out = dict(p)
    for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "PIXABAY_API_KEY"):
        v = out.get(k, "")
        out[k] = ("••••" + v[-4:]) if v else ""
    return out


# ── Pekerjaan: antrean beberapa jalur (lihat antrean.py) ───────────────────
def lingkungan_kerja() -> dict:
    env = os.environ.copy()
    p = baca_pengaturan()
    env.update({k: v for k, v in p.items() if v})
    env["UV"] = cari_uv()
    if p.get("VOXCPM_MODE") == "clone" and ref_suara():
        env["VOXCPM_REFERENSI"] = str(ref_suara())
    if p.get("VOXCPM_MODE") != "desain":
        env.pop("VOXCPM_DESAIN", None)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"  # Windows: cegah galat cp1252 saat mencetak “ ” dan emoji
    return env


KERJA = Kerja(lingkungan_kerja)         # jalur "utama": analisis, render, motion, pemasangan
KERJA_UNGGAH = Kerja(lingkungan_kerja)  # jalur "unggah": posting ke media sosial, berjalan paralel dengan render


# Versi installer (.dmg/.exe): Python + semua paket sudah dibundel, peluncur mengisi STUDIO_PY.
PY_BUNDEL = os.environ.get("STUDIO_PY") if os.environ.get("STUDIO_PY") and Path(os.environ["STUDIO_PY"]).exists() else None


def jalankan_skrip(skrip: Path) -> list[str]:
    """Perintah untuk menjalankan skrip alat: Python bawaan installer, atau `uv run` (versi folder)."""
    return [PY_BUNDEL, str(skrip)] if PY_BUNDEL else [cari_uv(), "run", "-q", str(skrip)]


def cari_uv() -> str:
    for c in (os.environ.get("STUDIO_UV"), shutil.which("uv"), str(Path.home() / ".local/bin/uv"), str(Path.home() / ".local/bin/uv.exe"),
              "/opt/homebrew/bin/uv"):
        if c and Path(c).exists():
            return c
    raise RuntimeError("uv belum terpasang. Jalankan installer lagi.")


def cari_node() -> str:
    for c in (shutil.which("node"), "/usr/local/bin/node", "/opt/homebrew/bin/node",
              r"C:\Program Files\nodejs\node.exe"):
        if c and Path(c).exists():
            return c
    raise RuntimeError("Node.js belum terpasang (nodejs.org) — dibutuhkan untuk Motion.")


def proyek_baru(jenis: str) -> Path:
    f = DATA / jenis / time.strftime("%Y%m%d-%H%M%S")
    n = 2
    while f.exists():  # dua tugas dalam detik yang sama
        f = DATA / jenis / (time.strftime("%Y%m%d-%H%M%S") + f"-{n}")
        n += 1
    f.mkdir(parents=True, exist_ok=True)
    return f


def penyedia_ai(nilai: str | None) -> str:
    """'langganan' → CLI pilihan di Pengaturan (claude-code / codex / opencode)."""
    p = baca_pengaturan()
    v = (nilai or p["AI"] or "lokal").lower()
    if v == "claude":  # Claude API dihapus (14 Nov 2026) → pakai Lokal
        v = "lokal"
    return p.get("LANGGANAN") or "claude-code" if v == "langganan" else v


CLI_DIR = Path.home() / ".studio-maliki" / "cli"  # CLI dipasang di sini (tanpa sudo)
PAKET_CLI = {"codex": "@openai/codex", "opencode": "opencode-ai", "claude": "@anthropic-ai/claude-code"}
PERINTAH_LOGIN = {"claude": "claude auth login", "codex": "codex login", "opencode": "opencode auth login"}
DOMAIN_BOLEH = ("aistudio.google.com", "ollama.com", "nodejs.org", "platform.openai.com",
                "openrouter.ai", "console.groq.com", "chatgpt.com", "claude.ai", "opencode.ai", "docs.astral.sh",
                "youtube.com", "www.youtube.com", "studio.youtube.com", "console.cloud.google.com",
                "developers.tiktok.com", "www.tiktok.com", "developers.facebook.com", "www.instagram.com",
                "dev.twitch.tv", "www.twitch.tv", "kick.com", "docs.kick.com")


def cari_cli(nama: str) -> str | None:
    for c in (str(CLI_DIR / "bin" / nama), str(CLI_DIR / f"{nama}.cmd"), shutil.which(nama), str(Path.home() / ".local/bin" / nama), f"/opt/homebrew/bin/{nama}",
              f"/usr/local/bin/{nama}", str(Path.home() / ".npm-global/bin" / nama)):
        if c and Path(c).exists():
            return c
    return None


def voxcpm_terpasang() -> bool:
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub" / "models--openbmb--VoxCPM2"
    return any(hub.glob("snapshots/*/model.safetensors"))


def ref_suara() -> Path | None:
    for f in sorted(DATA.glob("suara-referensi.*")):
        return f
    return None


def pasang_suara() -> dict:
    cmd = [cari_uv(), "run", "-q", str(SUARA / "suara.py"), "--cek"]
    KERJA.jalankan("suara", [(cmd, SUARA)], DATA)
    return {"ok": True}


def status_ai() -> dict:
    """Status nyata tiap jalur AI, untuk panduan bertahap di Pengaturan AI."""
    p = baca_pengaturan()
    out = {"gemini": {"kunci": bool(p.get("GEMINI_API_KEY"))}}
    # Ollama: terpasang? server menyala? model apa saja?
    import urllib.request
    ollama = shutil.which("ollama") or ("/Applications/Ollama.app" if Path("/Applications/Ollama.app").exists() else None)
    model, hidup = [], False
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1.5) as r:
            model = [m["name"] for m in json.loads(r.read()).get("models", [])]
            hidup = True
    except Exception:  # noqa: BLE001
        pass
    out["openai"] = {"ollama": bool(ollama), "hidup": hidup, "model": model}
    # CLI langganan: terpasang? sudah login?
    cli = {}
    for nama in ("claude", "codex", "opencode"):
        jalur = cari_cli(nama)
        login = False
        if jalur:
            try:
                if nama == "claude":
                    r = subprocess.run([jalur, "auth", "status"], capture_output=True, text=True, timeout=20)
                    login = '"loggedIn": true' in r.stdout
                elif nama == "codex":
                    r = subprocess.run([jalur, "login", "status"], capture_output=True, text=True, timeout=20)
                    login = r.returncode == 0 and "not logged" not in (r.stdout + r.stderr).lower()
                else:
                    r = subprocess.run([jalur, "auth", "list"], capture_output=True, text=True, timeout=20)
                    m = re.search(r"(\d+)\s+credentials?", re.sub(r"\x1b\[[0-9;]*m", "", r.stdout))
                    login = r.returncode == 0 and bool(m) and int(m.group(1)) > 0
            except Exception:  # noqa: BLE001
                login = False
        lewat_gemini = nama == "opencode" and bool(jalur) and not login and bool(p.get("GEMINI_API_KEY"))
        cli[nama] = {"terpasang": bool(jalur), "login": login or lewat_gemini, "lewat_gemini": lewat_gemini}
    out["cli"] = cli
    out["node"] = bool(shutil.which("npm") or Path("/usr/local/bin/npm").exists())
    return out


def sistem_detail() -> dict:
    """Spesifikasi laptop + status komponen untuk wizard pemasangan."""
    import platform
    info = {"os": platform.system(), "versi": platform.mac_ver()[0] or platform.release(), "arsitektur": platform.machine(),
            "prosesor": platform.processor() or platform.machine(), "ram_gb": 0.0, "disk_gb": 0.0}
    try:
        if sys.platform == "darwin":
            info["ram_gb"] = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 2 ** 30
            info["prosesor"] = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip() or info["prosesor"]
        elif os.name == "nt":
            import ctypes

            class Mem(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = Mem(); m.dwLength = ctypes.sizeof(Mem)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))  # type: ignore[attr-defined]
            info["ram_gb"] = m.ullTotalPhys / 2 ** 30
    except Exception:  # noqa: BLE001
        pass
    info["disk_gb"] = shutil.disk_usage(Path.home()).free / 2 ** 30
    npm = shutil.which("node") or ("/usr/local/bin/node" if Path("/usr/local/bin/node").exists() else None)
    info["node"] = subprocess.run([npm, "--version"], capture_output=True, text=True).stdout.strip() if npm else ""
    info["komponen"] = {
        "klip": (DATA / ".siap-klip").exists(),
        "motion": (MOTION / "node_modules" / "remotion").exists(),
        "voxcpm": voxcpm_terpasang(),
    }
    info["wizard_selesai"] = (DATA / ".wizard-selesai").exists()
    return info


def siapkan(komponen: str) -> dict:
    """Pasang/siapkan komponen dari wizard (progres tampil di layar)."""
    if komponen == "klip":
        langkah = [] if PY_BUNDEL else [([cari_uv(), "sync", "--script", str(KLIP / "klip.py")], KLIP)]
        langkah.append((jalankan_skrip(KLIP / "klip.py") + ["--uji-ai", "lokal"], KLIP))

        def setelah(k: Kerja):
            (DATA / ".siap-klip").write_text("ok")
        KERJA.jalankan("pasang", langkah, DATA, setelah)
    elif komponen == "motion":
        npm = shutil.which("npm") or "/usr/local/bin/npm"
        if not Path(npm).exists():
            raise RuntimeError("Node.js belum terpasang. Pasang dari nodejs.org, lalu buka ulang Studio.")
        KERJA.jalankan("pasang", [([npm, "install"], MOTION)], MOTION)
    elif komponen == "voxcpm":
        KERJA.jalankan("pasang", [([cari_uv(), "run", "-q", str(SUARA / "suara.py"), "--cek"], SUARA)], DATA)
    else:
        raise ValueError("Komponen tidak dikenal.")
    return {"ok": True}


def pasang_cli(nama: str) -> dict:
    if nama not in PAKET_CLI:
        raise ValueError("CLI tidak dikenal.")
    npm = shutil.which("npm") or "/usr/local/bin/npm"
    if not Path(npm).exists() and not shutil.which("npm"):
        raise RuntimeError("Node.js belum terpasang. Pasang dari nodejs.org dulu.")
    CLI_DIR.mkdir(parents=True, exist_ok=True)
    KERJA.jalankan("pasang", [([npm, "install", "-g", "--prefix", str(CLI_DIR), PAKET_CLI[nama]], CLI_DIR)], CLI_DIR)
    return {"ok": True}


def buka_login(nama: str) -> dict:
    """Buka Terminal berisi perintah login resmi — pengguna sendiri yang masuk ke akunnya."""
    jalur = cari_cli(nama)
    if not jalur:
        raise ValueError(f"{nama} belum terpasang.")
    perintah = PERINTAH_LOGIN[nama].replace(nama, f'"{jalur}"', 1)
    if nama == "codex":
        # `codex login` membuka browser untuk "Sign in with ChatGPT" dan menunggu izin di server lokalnya
        subprocess.Popen([jalur, "login"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return {"ok": True, "browser": True}
    if sys.platform == "darwin":
        skrip = f'tell application "Terminal" to do script "{perintah.replace(chr(34), chr(92) + chr(34))}"'
        subprocess.run(["osascript", "-e", skrip, "-e", 'tell application "Terminal" to activate'], check=False)
    elif os.name == "nt":
        subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", perintah])
    else:
        subprocess.Popen(["x-terminal-emulator", "-e", perintah])
    return {"ok": True}


def uji_ai(penyedia: str) -> dict:
    p = baca_pengaturan()
    env = os.environ.copy()
    env.update({k: v for k, v in p.items() if v})
    env["PYTHONIOENCODING"] = "utf-8"
    nyata = penyedia_ai(penyedia)
    try:
        r = subprocess.run([*jalankan_skrip(KLIP / "klip.py"), "--uji-ai", nyata], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=240, cwd=KLIP,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "pesan": "Tidak ada jawaban dalam 4 menit."}
    bersih = lambda t: re.sub(r"\x1b\[[0-9;]*m", "", t or "")  # noqa: E731
    r.stdout, r.stderr = bersih(r.stdout), bersih(r.stderr)
    keluaran = (r.stdout or "").strip().splitlines()
    galat = [b for b in (r.stderr or "").splitlines() if b.strip() and not re.search(r"warn|objc\[|AVFAudio|ctranslate2", b, re.I)]
    if r.returncode == 0 and keluaran:
        return {"ok": True, "pesan": keluaran[-1][:200], "penyedia": nyata}
    return {"ok": False, "pesan": (galat[-1] if galat else (keluaran[-1] if keluaran else "Gagal tanpa pesan."))[:400], "penyedia": nyata}


def mulai_klip(d: dict) -> dict:
    sumber = (d.get("sumber") or "").strip()
    if not sumber:
        raise ValueError("Isi link YouTube atau pilih file video dulu.")
    if sumber.startswith("-"):  # cegah dibaca sebagai opsi baris perintah
        raise ValueError("Sumber tidak valid. Tempel link https:// atau pilih file video.")
    folder = proyek_baru("klip")
    cmd = [*jalankan_skrip(KLIP / "klip.py"), "--keluar", str(folder),
           "--jumlah", str(int(d.get("jumlah", 3))), "--durasi", str(int(d.get("durasi", 30))),
           "--ai", penyedia_ai(d.get("ai")), "--model", d.get("model", "small"),
           "--warna", d.get("warna", "#FFB020")]
    cmd.append("--hanya-analisis")
    if d.get("arahan"):
        cmd += ["--arahan", d["arahan"]]
    if d.get("rentang"):
        cmd += ["--rentang", d["rentang"]]
    if d.get("subtitle_file"):
        cmd += ["--subtitle-file", d["subtitle_file"]]
    cmd += ["--", sumber]  # argumen posisi terakhir, setelah "--"
    (folder / "permintaan.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    t = ANTREAN.tambah("utama", "klip", [(cmd, KLIP)], folder, ("muat_proyek", {"jenis": "klip", "nama": folder.name}),
                       label=f"Analisis: {Path(sumber).name if not sumber.startswith('http') else sumber}"[:120])
    return {"proyek": folder.name, **t}


def _folder_klip(nama: str) -> Path:
    f = DATA / "klip" / re.sub(r"[^\w-]", "", nama or "")
    if not (f / "transkrip.json").exists():
        raise ValueError("Proyek tidak ditemukan. Analisis videonya dulu.")
    return f


def kalimat_klip(d: dict) -> dict:
    """Kalimat transkrip yang masuk rentang satu klip (untuk disunting sebelum render)."""
    f = _folder_klip(d.get("nama"))
    klip = next((x for x in json.loads((f / "klip.json").read_text(encoding="utf-8")) if x["no"] == int(d.get("no", 0))), None)
    if not klip:
        raise ValueError("Klip tidak ditemukan.")
    tr = json.loads((f / "transkrip.json").read_text(encoding="utf-8"))
    out = [{"i": i, "mulai": k["mulai"], "teks": " ".join(w["teks"] for w in k.get("kata", [])) or k["teks"]}
           for i, k in enumerate(tr) if k["selesai"] > klip["mulai"] and k["mulai"] < klip["selesai"]]
    return {"kalimat": out}


def simpan_subtitle(d: dict) -> dict:
    """Ganti teks kalimat. Jumlah kata sama -> waktu tiap kata tetap; berbeda -> waktu dibagi rata sepanjang kalimat."""
    f = _folder_klip(d.get("nama"))
    tr = json.loads((f / "transkrip.json").read_text(encoding="utf-8"))
    n = 0
    for x in d.get("kalimat") or []:
        i = int(x.get("i", -1))
        if not 0 <= i < len(tr):
            continue
        kata_baru = str(x.get("teks", "")).split()
        k = tr[i]
        lama = k.get("kata") or []
        if [w["teks"] for w in lama] == kata_baru:
            continue
        if kata_baru and len(kata_baru) == len(lama):
            for w, t in zip(lama, kata_baru):
                w["teks"] = t
        elif kata_baru:
            a, b = (lama[0]["mulai"], lama[-1]["selesai"]) if lama else (k["mulai"], k["selesai"])
            dur = max(b - a, 0.05) / len(kata_baru)
            k["kata"] = [{"mulai": round(a + j * dur, 3), "selesai": round(a + (j + 1) * dur, 3), "teks": t}
                         for j, t in enumerate(kata_baru)]
        else:
            k["kata"] = []
        k["teks"] = " ".join(kata_baru)
        n += 1
    if n:
        (f / "transkrip.json").write_text(json.dumps(tr, ensure_ascii=False), encoding="utf-8")
    return {"diubah": n}


def mulai_render_klip(d: dict) -> dict:
    nama = re.sub(r"[^\w-]", "", d.get("nama", ""))
    folder = DATA / "klip" / nama
    if not (folder / "klip.json").exists():
        raise ValueError("Proyek tidak ditemukan. Analisis videonya dulu.")
    pilih = [int(x) for x in d.get("pilih", [])]
    if not pilih:
        raise ValueError("Centang minimal satu klip untuk dirender.")
    gaya = d.get("gaya") or {}
    for k in ("musik", "sfx"):  # hanya berkas di folder aset Studio
        if gaya.get(k):
            p = (DATA / "aset" / Path(gaya[k]).name)
            gaya[k] = str(p) if p.exists() else ""
    gaya["broll_file"] = [str(DATA / "aset" / Path(x).name) for x in (gaya.get("broll_file") or [])
                          if (DATA / "aset" / Path(x).name).exists()]
    # judul banner hasil suntingan pengguna
    if d.get("judul"):
        daftar = json.loads((folder / "klip.json").read_text(encoding="utf-8"))
        for x in daftar:
            if str(x["no"]) in d["judul"]:
                x["judul_banner"] = d["judul"][str(x["no"])]
        (folder / "klip.json").write_text(json.dumps(daftar, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "gaya-permintaan.json").write_text(json.dumps(gaya, ensure_ascii=False), encoding="utf-8")
    cmd = [*jalankan_skrip(KLIP / "klip.py"), "--render", "--keluar", str(folder),
           "--pilih", ",".join(map(str, pilih)), "--gaya", str(folder / "gaya-permintaan.json")]

    t = ANTREAN.tambah("utama", "render", [(cmd, KLIP)], folder, ("muat_proyek", {"jenis": "klip", "nama": nama}),
                       label=f"Render {len(pilih)} klip · {nama}")
    return {"proyek": nama, **t}


def mulai_motion(d: dict) -> dict:
    topik = (d.get("topik") or "").strip()
    naskah_teks = (d.get("naskah") or "").strip()
    if not topik and not naskah_teks:
        raise ValueError("Isi topik, atau tempel naskah JSON.")
    ai = penyedia_ai(d.get("ai"))
    if not naskah_teks and ai == "lokal":
        raise ValueError("Membuat naskah dari topik butuh AI (Gemini/Ollama/Claude) — atur di Pengaturan, "
                         "atau tempel naskah JSON dari Claude/Gemini gratis.")
    node = cari_node()
    folder = proyek_baru("motion")
    nama = folder.name
    if not (MOTION / "node_modules").exists():
        langkah = [([shutil.which("npm") or "npm", "install", "--silent"], MOTION)]
    else:
        langkah = []
    gaya = d.get("gaya", "garis")
    if naskah_teks:
        data = json.loads(naskah_teks)
        data["gaya"] = gaya
        (MOTION / "naskah").mkdir(exist_ok=True)
        (MOTION / "naskah" / f"{nama}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        langkah.append(([node, "naskah.mjs", topik, f"--ai={ai}", f"--gaya={gaya}", f"--nama={nama}"], MOTION))
    if d.get("gambar") and ai != "lokal":
        # AI menggambar objek SVG per adegan (gagal → video tetap jadi dengan emoji)
        langkah.append(([node, "gambar.mjs", f"naskah/{nama}.json", f"--ai={ai}"], MOTION))
    p = baca_pengaturan()
    perintah = [node, "buat.mjs", f"naskah/{nama}.json", f"--gaya={gaya}", f"--suara-mac={p.get('SUARA_MAC', 'Damayanti')}"]
    if p.get("SUARA") == "voxcpm":
        if not voxcpm_terpasang():
            raise ValueError("Suara AI (VoxCPM) belum dipasang. Buka Pengaturan AI → Suara narator → Pasang suara AI.")
        if p.get("VOXCPM_MODE") == "clone" and not ref_suara():
            # tidak ada suara bawaan siapa pun: mode clone WAJIB memakai rekaman pengguna sendiri (dengan izin)
            raise ValueError("Mode \"Clone suaraku\" butuh rekaman suaramu sendiri. Buka Pengaturan AI → Suara narator, "
                             "centang pernyataan izin, lalu pilih rekaman 10–20 detik. Atau pilih warna suara Netral.")
        perintah.append("--suara=voxcpm")
    langkah.append((perintah, MOTION))

    t = ANTREAN.tambah("utama", "motion", langkah, folder, ("motion_selesai", {"nama": nama}),
                       label=f"Motion: {topik or 'naskah sendiri'}"[:120])
    return {"proyek": nama, **t}


def muat_proyek(jenis: str, nama: str) -> dict:
    f = DATA / jenis / nama
    out = {"jenis": jenis, "nama": nama}
    for berkas in ("klip.json", "info.json", "permintaan.json", "naskah.json"):
        p = f / berkas
        if p.exists():
            out[berkas.split(".")[0]] = json.loads(p.read_text(encoding="utf-8"))
    if (f / "video.mp4").exists():
        out["video"] = f"/berkas/{jenis}/{nama}/video.mp4"
    if (f / "gambar.jpg").exists():
        out["gambar"] = f"/berkas/{jenis}/{nama}/gambar.jpg"
    if (f / "gaya.json").exists():
        out["gaya"] = json.loads((f / "gaya.json").read_text(encoding="utf-8"))
    pr = (out.get("info") or {}).get("pratinjau")
    if pr and (f / pr).exists():
        out["sumber"] = f"/berkas/{jenis}/{nama}/{pr}"
    return out


def jejak_klip(nama: str, no: int) -> dict:
    """Jejak wajah satu klip (detik relatif, posisi-x 0..1) untuk pratinjau Studio — sama persis dengan yang dipakai render."""
    folder = DATA / "klip" / nama
    daftar = json.loads((folder / "klip.json").read_text(encoding="utf-8"))
    d = next((x for x in daftar if x.get("no") == no), None)
    if not d:
        return {"jejak": []}
    if d.get("jejak"):
        return {"jejak": d["jejak"]}
    r = subprocess.run([*jalankan_skrip(KLIP / "klip.py"), "--keluar", str(folder), "--jejak", str(no)],
                       capture_output=True, text=True, cwd=KLIP, timeout=300,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        return {"jejak": json.loads(r.stdout.strip().splitlines()[-1])}
    except Exception:  # noqa: BLE001
        return {"jejak": [], "galat": (r.stderr or "")[-300:]}


def daftar_proyek() -> list[dict]:
    out = []
    for jenis in ("klip", "motion"):
        for f in sorted((DATA / jenis).glob("*"), reverse=True) if (DATA / jenis).exists() else []:
            p = muat_proyek(jenis, f.name)
            if (jenis == "klip" and "info" not in p) or (jenis == "motion" and "video" not in p):
                continue  # proyek gagal/terputus tidak ditampilkan (berkasnya tetap ada di disk)
            judul = (p.get("info") or {}).get("judul") or (p.get("naskah") or {}).get("judul") or \
                (p.get("permintaan") or {}).get("sumber") or (p.get("permintaan") or {}).get("topik") or f.name
            jumlah = len(p.get("klip") or []) or (1 if p.get("video") else 0)
            out.append({"jenis": jenis, "nama": f.name, "judul": judul, "jumlah": jumlah,
                        "gambar": p.get("gambar", ""), "durasi": (p.get("info") or {}).get("durasi"),
                        "ai": (p.get("info") or {}).get("ai", "")})
    return sorted(out, key=lambda x: x["nama"], reverse=True)[:50]


def cek_sistem() -> dict:
    return {"uv": bool(PY_BUNDEL or os.environ.get("STUDIO_UV") or shutil.which("uv") or (Path.home() / ".local/bin/uv").exists()),
            "node": bool(shutil.which("node") or Path("/usr/local/bin/node").exists()),
            "mac": sys.platform == "darwin", "win": os.name == "nt", "data": str(DATA),
            "cli": {n: bool(cari_cli(n)) for n in ("claude", "codex", "opencode")},
            "voxcpm": voxcpm_terpasang(), "referensi": ref_suara().name if ref_suara() else ""}


# ── Unggah ke media sosial (API resmi, selalu lewat layar review) ───────────
OAUTH_SESI: dict = {}
RIWAYAT_UNGGAH = DATA / "unggah" / "riwayat.jsonl"
BATAS_UNGGAH = 20 * 2**30  # 20 GB per file yang diseret ke Studio


MODUL_PLATFORM = {"youtube": UNGGAH, "tiktok": TT, "instagram": IG}
NAMA_PLATFORM = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}


def status_akun() -> dict:
    return {p: m.status(DATA) for p, m in MODUL_PLATFORM.items()}


def oauth_masuk(platform: str) -> dict:
    """Buka halaman izin resmi platform di browser bawaan (Instagram memakai token tempel, bukan OAuth lokal)."""
    if platform not in ("youtube", "tiktok"):
        raise ValueError("Platform ini tidak memakai login OAuth lokal.")
    url, sesi = MODUL_PLATFORM[platform].mulai_izin(DATA, f"http://127.0.0.1:{PORT}/oauth/{platform}")
    sesi["platform"] = platform
    for k in [k for k, v in OAUTH_SESI.items() if v.get("platform") == platform]:
        OAUTH_SESI.pop(k)
    OAUTH_SESI[sesi["state"]] = sesi
    webbrowser.open(url)
    return {"ok": True, "url": url}


def oauth_kembali(platform: str, q: dict) -> str:
    def halaman(judul: str, isi: str) -> str:
        return (f"<!doctype html><meta charset=utf-8><title>Studio Maliki</title><body style='font-family:system-ui;"
                f"background:#07090F;color:#e2e8f0;display:grid;place-items:center;height:100vh;margin:0'>"
                f"<div style='max-width:420px;text-align:center'><h2>{html.escape(judul)}</h2><p>{html.escape(isi)}</p></div>")
    nama = NAMA_PLATFORM.get(platform, platform)
    state = (q.get("state") or [""])[0]
    sesi = OAUTH_SESI.get(state)
    if not sesi or sesi.get("platform") != platform or time.time() - sesi["waktu"] > 600:
        return halaman("Tautan izin tidak berlaku", f"Ulangi dari Studio: Pengaturan → Akun unggah → Sambungkan {nama}.")
    OAUTH_SESI.pop(state, None)
    if q.get("error"):
        return halaman("Izin dibatalkan", f"{nama}: {(q.get('error_description') or q.get('error') or [''])[0]}")
    try:
        MODUL_PLATFORM[platform].tukar_kode(DATA, sesi, (q.get("code") or [""])[0])
    except Exception as e:  # noqa: BLE001
        return halaman(f"Gagal menyambungkan {nama}", str(e))
    return halaman(f"{nama} tersambung ✓", "Tutup tab ini lalu kembali ke Studio Maliki.")


def _klip_proyek(nama: str, no: int) -> tuple[Path, dict, dict]:
    folder = DATA / "klip" / re.sub(r"[^\w-]", "", str(nama or ""))
    if not (folder / "klip.json").exists():
        raise ValueError("Proyek tidak ditemukan.")
    klip = next((x for x in json.loads((folder / "klip.json").read_text(encoding="utf-8")) if x.get("no") == int(no)), None)
    if not klip:
        raise ValueError("Klip tidak ditemukan.")
    info = json.loads((folder / "info.json").read_text(encoding="utf-8")) if (folder / "info.json").exists() else {}
    return folder, klip, info


def susun_caption(d: dict) -> dict:
    """Judul/caption/hashtag per platform, sudah memuat aturan kampanye yang dipilih."""
    _, klip, info = _klip_proyek(d.get("nama"), d.get("no", 0))
    if d.get("judul"):
        klip = {**klip, "judul_banner": str(d["judul"])[:200]}
    kamp = CAPTION.cari_kampanye(DATA, str(d.get("kampanye") or ""))
    return CAPTION.susun(klip, info, str(d.get("platform", "youtube")), kamp)


def _jadwal(nilai) -> float:
    try:
        t = float(nilai or 0)
    except (TypeError, ValueError):
        raise ValueError("Jadwal tidak valid.") from None
    if t and t < time.time() - 60:
        raise ValueError("Jadwal sudah lewat. Pilih waktu yang akan datang.")
    if t > time.time() + 90 * 86400:
        raise ValueError("Jadwal maksimal 90 hari ke depan.")
    return t


def mulai_unggah(d: dict) -> dict:
    """Unggah satu klip yang SUDAH direview pengguna ke satu platform. Tanpa centang izin, permintaan ditolak."""
    platform = str(d.get("platform", "youtube"))
    if platform not in MODUL_PLATFORM:
        raise ValueError("Platform belum didukung.")
    if d.get("izin") is not True:
        raise ValueError("Centang pernyataan bahwa kamu berhak mengunggah konten ini.")
    if not MODUL_PLATFORM[platform].status(DATA)["terhubung"]:
        raise ValueError(f"Sambungkan akun {NAMA_PLATFORM[platform]} dulu di Pengaturan → Akun unggah.")
    folder, klip, _ = _klip_proyek(d.get("nama"), d.get("no", 0))
    nama, no = folder.name, int(d.get("no", 0))
    if not klip.get("file") or not (folder / Path(klip["file"]).name).is_file():
        raise ValueError("Klip belum dirender. Render dulu, lalu unggah.")
    kamp = CAPTION.cari_kampanye(DATA, str(d.get("kampanye") or ""))
    batas = CAPTION.PLATFORM[platform]
    if platform == "youtube":
        meta = UNGGAH.rapikan_meta(d)
        judul_label = meta["judul"]
    else:
        teks = str(d.get("teks", "")).replace("\r", "")
        if len(teks) > batas["teks"]:
            raise ValueError(f"Caption {NAMA_PLATFORM[platform]} maksimal {batas['teks']} karakter.")
        meta = {"teks": teks, "konten_bermerek": bool(d.get("konten_bermerek")), "promosi_sendiri": bool(d.get("promosi_sendiri"))}
        if platform == "tiktok":
            meta.update(mode="langsung" if d.get("mode") == "langsung" else "draf", privasi=str(d.get("privasi") or ""),
                        tanpa_komentar=bool(d.get("tanpa_komentar")), tanpa_duet=bool(d.get("tanpa_duet")),
                        tanpa_stitch=bool(d.get("tanpa_stitch")), durasi=float(klip.get("selesai", 0)) - float(klip.get("mulai", 0)))
            if meta["mode"] == "langsung" and not meta["privasi"]:
                raise ValueError("Pilih privasi TikTok dulu (tidak ada pilihan bawaan sesuai aturan TikTok).")
        else:
            meta["ke_feed"] = d.get("ke_feed", True) is not False
        judul_label = (teks.splitlines() or [""])[0]
    if d.get("cover_ms") not in (None, "") and platform != "youtube":  # Shorts tidak menerima sampul lewat API
        durasi_ms = int((float(klip.get("selesai", 0)) - float(klip.get("mulai", 0))) * 1000)
        meta["cover_ms"] = min(max(int(float(d["cover_ms"])), 0), max(durasi_ms - 100, 0))
    meta["sumber_izin"] = str(d.get("sumber_izin", "") or (kamp or {}).get("sumber_izin", ""))[:300]
    meta["kampanye"] = kamp["id"] if kamp else ""
    jadwal = _jadwal(d.get("jadwal"))
    berkas = folder / Path(klip["file"]).name
    kode = f"{no:02d}-{platform}-{int(time.time() * 1000) % 10**8}"
    f_meta, f_hasil = folder / f"unggah-{kode}.json", folder / f"unggah-{kode}-hasil.json"
    f_meta.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    cmd = [*jalankan_skrip(AKAR / "unggah.py"), "--platform", platform, "--data", str(DATA), "--berkas", str(berkas),
           "--meta", str(f_meta), "--hasil", str(f_hasil)]
    t = ANTREAN.tambah("unggah", "unggah", [(cmd, AKAR)], folder,
                       ("unggah_selesai", {"nama": nama, "no": no, "f_hasil": str(f_hasil), "sumber_izin": meta["sumber_izin"],
                                          "kampanye": meta["kampanye"]}),
                       label=f"{NAMA_PLATFORM[platform]}: {judul_label}"[:120], platform=platform, jadwal=jadwal,
                       info={"proyek": nama, "no": no, "platform": platform})
    return {"proyek": nama, **t}


def _setelah_muat(k: Kerja, jenis: str, nama: str):
    k.hasil = muat_proyek(jenis, nama)


def _setelah_motion(k: Kerja, nama: str):
    folder = DATA / "motion" / nama
    for f in (MOTION / "hasil").glob(f"{nama}-*.mp4"):
        shutil.move(str(f), folder / "video.mp4")
    n = MOTION / "naskah" / f"{nama}.json"
    if n.exists():
        shutil.copy(n, folder / "naskah.json")
    k.hasil = muat_proyek("motion", nama)


def _setelah_unggah(k: Kerja, nama: str, no: int, f_hasil: str, sumber_izin: str = "", kampanye: str = ""):
    f_hasil = Path(f_hasil)
    folder = DATA / "klip" / nama
    if not f_hasil.exists():
        return
    hasil = json.loads(f_hasil.read_text(encoding="utf-8"))
    hasil.update(proyek=nama, no=no, sumber_izin=sumber_izin, kampanye=kampanye)
    semua = json.loads((folder / "klip.json").read_text(encoding="utf-8"))
    for x in semua:
        if x.get("no") == no:
            x.setdefault("unggahan", []).append(hasil)
    (folder / "klip.json").write_text(json.dumps(semua, ensure_ascii=False, indent=2), encoding="utf-8")
    RIWAYAT_UNGGAH.parent.mkdir(parents=True, exist_ok=True)
    with open(RIWAYAT_UNGGAH, "a", encoding="utf-8") as fh:  # log jejak: apa, ke mana, kapan, atas izin apa
        fh.write(json.dumps(hasil, ensure_ascii=False) + "\n")
    k.hasil = {"unggah": hasil}


def jeda_platform(platform: str) -> int:
    """Jeda minimum antar-posting per platform (detik), dari Pengaturan. Bawaan 30 menit."""
    try:
        return int(float(baca_pengaturan().get("JEDA_POSTING_MENIT") or 30) * 60)
    except ValueError:
        return 1800


ANTREAN = Antrean(DATA / "antrean.json", {"utama": KERJA, "unggah": KERJA_UNGGAH},
                  {"muat_proyek": _setelah_muat, "motion_selesai": _setelah_motion, "unggah_selesai": _setelah_unggah},
                  jeda_platform)


# ── Live: pantau kanal yang memberi izin, rekam, dan potong kandidat momen (lihat live.py) ──
def jalankan_rekam(k: dict, folder: Path) -> subprocess.Popen:
    if LIVE.ruang_kosong_gb(DATA) < 5:
        raise RuntimeError("Ruang disk kurang dari 5 GB. Kosongkan dulu sebelum merekam live.")
    cmd = [*jalankan_skrip(KLIP / "rekam_live.py"), "--url", k["url_live"], "--folder", str(folder),
           "--platform", k["platform"], "--nama", k["nama"], "--batas-jam", str(k.get("batas_jam", 4))]
    log = open(folder / "rekam.log", "ab")
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}  # tanpa jendela konsol di Windows
    return subprocess.Popen(cmd, cwd=str(KLIP), env=lingkungan_kerja(), stdin=subprocess.DEVNULL, stdout=log,
                            stderr=subprocess.STDOUT, **kw)


PELACAK = STATISTIK.Pelacak(DATA, {"youtube": UNGGAH, "tiktok": TT, "instagram": IG}, lambda: CAPTION.daftar_kampanye(DATA))
PEMANTAU = LIVE.Pemantau(DATA, jalankan_rekam, token_youtube=lambda: UNGGAH.token_segar(DATA))


def rekam_live(d: dict) -> dict:
    k = next((x for x in PEMANTAU.kanal() if x["id"] == d.get("id")), None)
    if not k:
        raise ValueError("Kanal tidak ditemukan.")
    return PEMANTAU.mulai_rekam(k)


def proses_kandidat(d: dict) -> dict:
    """Kandidat momen → analisis klip biasa (transkrip, judul, caption), lalu review seperti klip lain."""
    p = PEMANTAU.berkas_kandidat(str(d.get("kanal", "")), str(d.get("sesi", "")), str(d.get("file", "")))
    k = next((x for x in PEMANTAU.kanal() if x["id"] == d.get("kanal")), {})
    durasi = int(min(max(float(d.get("durasi") or 40), 15), 60))
    out = mulai_klip({"sumber": str(p), "jumlah": 1, "durasi": durasi, "ai": d.get("ai"),
                      "arahan": f"Potongan live {k.get('label', '')}. Sumber izin: {k.get('izin', '')}"[:300]})
    (DATA / "klip" / out["proyek"] / "live.json").write_text(json.dumps(
        {"kanal": k.get("label"), "platform": k.get("platform"), "izin": k.get("izin"), "kampanye": k.get("kampanye"),
         "url": k.get("url_live"), "kandidat": p.name}, ensure_ascii=False), encoding="utf-8")
    return out


# ── HTTP ────────────────────────────────────────────────────────────────────
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, kode=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(kode)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _file(self, p: Path):
        if not p.is_file():
            return self._json({"galat": "tidak ditemukan"}, 404)
        ukuran = p.stat().st_size
        tipe = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        mulai, akhir = 0, ukuran - 1
        if rng and (m := re.match(r"bytes=(\d*)-(\d*)", rng)):
            mulai = int(m.group(1) or 0)
            akhir = int(m.group(2)) if m.group(2) else ukuran - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {mulai}-{akhir}/{ukuran}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", tipe)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(akhir - mulai + 1))
        self.end_headers()
        with open(p, "rb") as fh:
            fh.seek(mulai)
            sisa = akhir - mulai + 1
            while sisa > 0 and (blok := fh.read(min(1 << 16, sisa))):
                self.wfile.write(blok)
                sisa -= len(blok)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _asal_aman(self) -> bool:
        # tolak permintaan dari situs lain (CSRF) — hanya halaman Studio sendiri
        asal = self.headers.get("Origin")
        return asal is None or asal in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")

    def _host_aman(self) -> bool:
        # cegah DNS rebinding: situs lain yang namanya diarahkan ke 127.0.0.1 membawa Host miliknya sendiri
        return self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _bukan_lintas_situs(self) -> bool:
        # cegah situs lain memicu endpoint GET (mis. lewat <img>); peramban lama tanpa Sec-Fetch-Site tetap boleh
        return self.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")

    def _html(self, isi: str, kode=200):
        b = isi.encode()
        self.send_response(kode)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        jalur = unquote(u.path)
        if not self._host_aman():
            return self._json({"galat": "host tidak diizinkan"}, 403)
        if jalur in ("/oauth/youtube", "/oauth/tiktok"):  # kembali dari halaman izin (lintas situs, dijaga state + PKCE)
            return self._html(oauth_kembali(jalur.rsplit("/", 1)[1], parse_qs(u.query)))
        if jalur.startswith(("/api/", "/berkas/")) and not self._bukan_lintas_situs():
            return self._json({"galat": "asal tidak diizinkan"}, 403)
        try:
            if jalur in ("/", "/index.html"):
                return self._file(AKAR / "index.html")
            if jalur == "/api/status":
                return self._json(KERJA.ringkas())
            if jalur == "/api/pengaturan":
                return self._json({"pengaturan": samarkan(baca_pengaturan()), "sistem": cek_sistem()})
            if jalur == "/api/model-opencode":
                # daftar model dari OpenCode milik pengguna (`opencode models`)
                cli = cari_cli("opencode")
                if not cli:
                    return self._json({"model": []})
                try:
                    r = subprocess.run([cli, "models"], capture_output=True, text=True, timeout=30)
                    daftar = [b.strip() for b in r.stdout.splitlines() if re.match(r"^[\w.-]+/[\w.:@/-]+$", b.strip())]
                except Exception:  # noqa: BLE001
                    daftar = []
                return self._json({"model": daftar[:300]})
            if jalur == "/api/sistem-detail":
                return self._json(sistem_detail())
            if jalur == "/api/status-ai":
                return self._json(status_ai())
            if jalur == "/api/proyek":
                return self._json(daftar_proyek())
            if jalur == "/api/kampanye":
                return self._json({"kampanye": CAPTION.daftar_kampanye(DATA), "platform": CAPTION.PLATFORM})
            if jalur == "/api/antrean":
                return self._json(ANTREAN.daftar())
            if jalur == "/api/akun-unggah":
                return self._json(status_akun())
            if jalur == "/api/statistik":
                return self._json(PELACAK.ringkasan())
            if jalur == "/api/live":
                return self._json(PEMANTAU.ringkas())
            if m := re.match(r"^/berkas/live/([\w-]+)/([\w-]+)/(kandidat-\d+\.mp4)$", jalur):
                try:
                    return self._file(PEMANTAU.berkas_kandidat(*m.groups()))
                except ValueError:
                    return self._json({"galat": "tidak ditemukan"}, 404)
            if m := re.match(r"^/api/jejak/([\w-]+)/(\d+)$", jalur):
                return self._json(jejak_klip(m.group(1), int(m.group(2))))
            if m := re.match(r"^/api/proyek/(klip|motion)/([\w-]+)$", jalur):
                return self._json(muat_proyek(m.group(1), m.group(2)))
            if m := re.match(r"^/berkas/(klip|motion)/([\w-]+)/([\w.-]+)$", jalur):
                return self._file(DATA / m.group(1) / m.group(2) / m.group(3))
            if m := re.match(r"^/font/([\w.-]+\.ttf)$", jalur):  # font preset klip (OFL) untuk pratinjau
                return self._file(KLIP / "font" / m.group(1))
            if jalur.startswith("/aset/"):
                p = (AKAR / jalur.lstrip("/")).resolve()
                if AKAR in p.parents:
                    return self._file(p)
            self._json({"galat": "tidak ditemukan"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        if not self._host_aman() or not self._asal_aman():
            return self._json({"galat": "asal tidak diizinkan"}, 403)
        jalur = urlparse(self.path).path
        try:
            if jalur == "/api/pasang-suara":
                return self._json(pasang_suara())
            if jalur == "/api/referensi-suara":
                # rekaman suara untuk clone — hanya suara sendiri / yang sudah memberi izin (dicentang di UI)
                if self.headers.get("X-Izin") != "ya":
                    return self._json({"galat": "Centang dulu pernyataan izin suara."}, 400)
                eks = re.sub(r"[^a-z0-9]", "", Path(self.headers.get("X-Nama-File", "a.wav")).suffix.lower())[:5] or "wav"
                if eks not in ("wav", "mp3", "m4a", "flac", "ogg"):
                    return self._json({"galat": "Format rekaman harus wav/mp3/m4a/flac/ogg."}, 400)
                for lama in DATA.glob("suara-referensi.*"):
                    lama.unlink()
                n = int(self.headers.get("Content-Length") or 0)
                if n > 50 * 1024 * 1024:
                    return self._json({"galat": "Rekaman terlalu besar (maks 50 MB, cukup 10–20 detik)."}, 400)
                (DATA / f"suara-referensi.{eks}").write_bytes(self.rfile.read(n))
                return self._json({"ok": True})
            if jalur == "/api/unggah":
                nama = re.sub(r"[^\w.-]", "_", Path(self.headers.get("X-Nama-File", "video.mp4")).name).lstrip(".")[:120] or "video.mp4"
                tujuan = DATA / ("aset" if self.headers.get("X-Jenis") == "aset" else "unggahan")
                tujuan.mkdir(parents=True, exist_ok=True)
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0 or n > BATAS_UNGGAH:
                    return self._json({"galat": f"Ukuran file harus di bawah {BATAS_UNGGAH // 2**30} GB."}, 413)
                if shutil.disk_usage(tujuan).free - n < 2 * 2**30:
                    return self._json({"galat": "Ruang disk tidak cukup (sisakan minimal 2 GB)."}, 507)
                p = tujuan / nama
                i = 1
                while p.exists():  # jangan menimpa file lain yang namanya sama
                    p = tujuan / f"{Path(nama).stem}-{i}{Path(nama).suffix}"
                    i += 1
                with open(p, "wb") as fh:
                    while n > 0 and (blok := self.rfile.read(min(1 << 20, n))):
                        fh.write(blok)
                        n -= len(blok)
                if n > 0:  # koneksi putus di tengah → buang file setengah jadi
                    p.unlink(missing_ok=True)
                    return self._json({"galat": "Unggahan terputus."}, 400)
                return self._json({"jalur": str(p)})
            d = self._body()
            if jalur == "/api/pengaturan":
                simpan_pengaturan(d)
                return self._json({"ok": True, "pengaturan": samarkan(baca_pengaturan())})
            if jalur == "/api/siapkan":
                return self._json(siapkan(d.get("komponen", "")))
            if jalur == "/api/wizard-selesai":
                (DATA / ".wizard-selesai").write_text("ok")
                return self._json({"ok": True})
            if jalur == "/api/uji-ai":
                return self._json(uji_ai(d.get("penyedia", "")))
            if jalur == "/api/pasang-cli":
                return self._json(pasang_cli(d.get("nama", "")))
            if jalur == "/api/login-cli":
                return self._json(buka_login(d.get("nama", "")))
            if jalur == "/api/ollama-tarik":
                model = re.sub(r"[^\w.:/-]", "", d.get("model", "gemma3:4b"))
                ollama = shutil.which("ollama") or next((c for c in ("/usr/local/bin/ollama", "/Applications/Ollama.app/Contents/Resources/ollama",
                    str(Path.home() / "AppData/Local/Programs/Ollama/ollama.exe")) if Path(c).exists()), "ollama")
                KERJA.jalankan("pasang", [([ollama, "pull", model], DATA)], DATA)
                return self._json({"ok": True})
            if jalur == "/api/buka-url":
                from urllib.parse import urlparse as _u
                url = d.get("url", "")
                if _u(url).scheme == "https" and _u(url).hostname in DOMAIN_BOLEH:
                    webbrowser.open(url)
                    return self._json({"ok": True})
                return self._json({"galat": "Alamat tidak diizinkan."}, 400)
            if jalur == "/api/klip-kalimat":
                return self._json(kalimat_klip(d))
            if jalur == "/api/klip-subtitle":
                return self._json(simpan_subtitle(d))
            if jalur == "/api/klip-render":
                return self._json(mulai_render_klip(d))
            if jalur == "/api/youtube-klien":
                cid, rahasia = d.get("client_id", ""), d.get("client_secret", "")
                if d.get("json"):
                    cid, rahasia = UNGGAH.klien_dari_json(d["json"])
                UNGGAH.simpan_klien(DATA, cid, rahasia)
                return self._json(status_akun())
            if jalur == "/api/tiktok-kreator":
                return self._json(TT.info_kreator(DATA))
            if jalur == "/api/tiktok-klien":
                TT.simpan_klien(DATA, str(d.get("client_key", "")), str(d.get("client_secret", "")))
                return self._json(status_akun())
            if jalur == "/api/instagram-token":
                IG.simpan_token(DATA, str(d.get("token", "")))
                return self._json(status_akun())
            if m := re.match(r"^/api/(youtube|tiktok)-masuk$", jalur):
                return self._json(oauth_masuk(m.group(1)))
            if m := re.match(r"^/api/(youtube|tiktok|instagram)-putus$", jalur):
                MODUL_PLATFORM[m.group(1)].hapus_token(DATA)
                return self._json(status_akun())
            if jalur == "/api/kampanye-simpan":
                return self._json({"kampanye": CAPTION.simpan_kampanye(DATA, d)})
            if jalur == "/api/kampanye-hapus":
                CAPTION.hapus_kampanye(DATA, str(d.get("id", "")))
                return self._json({"ok": True})
            if jalur == "/api/caption":
                return self._json(susun_caption(d))
            if jalur == "/api/antrean-batal":
                return self._json({"ok": ANTREAN.batal(str(d.get("id", "")))})
            if jalur == "/api/antrean-ulang":
                return self._json(ANTREAN.ulangi(str(d.get("id", ""))))
            if jalur == "/api/unggah-klip":
                return self._json(mulai_unggah(d))
            if jalur == "/api/statistik-segarkan":
                return self._json(PELACAK.segarkan())
            if jalur == "/api/statistik-tautkan":
                PELACAK.tautkan(str(d.get("platform", "")), str(d.get("id", "")), str(d.get("url", "")))
                return self._json(PELACAK.ringkasan())
            if jalur == "/api/statistik-manual":
                PELACAK.isi_manual(str(d.get("platform", "")), str(d.get("id", "")), d.get("views"))
                return self._json(PELACAK.ringkasan())
            if jalur == "/api/live-kunci":
                return self._json(PEMANTAU.simpan_kunci(d))
            if jalur == "/api/live-kanal":
                return self._json({"kanal": PEMANTAU.simpan_kanal(d)})
            if jalur == "/api/live-hapus":
                PEMANTAU.hapus_kanal(str(d.get("id", "")))
                return self._json({"ok": True})
            if jalur == "/api/live-cek":
                PEMANTAU.cek()
                return self._json(PEMANTAU.ringkas())
            if jalur == "/api/live-rekam":
                return self._json(rekam_live(d))
            if jalur == "/api/live-henti":
                return self._json({"ok": PEMANTAU.hentikan(str(d.get("id", "")))})
            if jalur == "/api/live-buang":
                PEMANTAU.buang_kandidat(str(d.get("kanal", "")), str(d.get("sesi", "")), str(d.get("file", "")))
                return self._json({"ok": True})
            if jalur == "/api/live-proses":
                return self._json(proses_kandidat(d))
            if jalur == "/api/hapus-proyek":
                f = (DATA / d.get("jenis", "") / re.sub(r"[^\w-]", "", d.get("nama", ""))).resolve()
                if f.is_dir() and DATA.resolve() in f.parents and f.parent.name in ("klip", "motion"):
                    shutil.rmtree(f)
                return self._json({"ok": True})
            if jalur == "/api/klip":
                return self._json(mulai_klip(d))
            if jalur == "/api/motion":
                return self._json(mulai_motion(d))
            if jalur == "/api/unduh":
                f = (DATA / d.get("jenis", "") / d.get("nama", "") / d.get("file", "")).resolve()
                if not f.is_file() or DATA.resolve() not in f.parents:
                    return self._json({"galat": "berkas tidak ditemukan"}, 404)
                awal = re.sub(r"[^\w-]", "", str(d.get("nama", "")))[:60] or "studio"
                tujuan = Path.home() / "Downloads" / f"{awal}-{f.name}"
                shutil.copy(f, tujuan)
                return self._json({"jalur": str(tujuan)})
            if jalur == "/api/matikan":
                PEMANTAU.hentikan_semua()
                KERJA.batal()
                KERJA_UNGGAH.batal()
                threading.Timer(0.3, lambda: os._exit(0)).start()
                return self._json({"ok": True})
            if jalur == "/api/batal":
                KERJA.batal()
                return self._json({"ok": True})
            if jalur == "/api/buka-folder":
                f = DATA / d.get("jenis", "") / d.get("nama", "")
                if f.exists() and DATA in f.resolve().parents:
                    buka_folder(f)
                return self._json({"ok": True})
            self._json({"galat": "tidak ditemukan"}, 404)
        except (ValueError, RuntimeError, json.JSONDecodeError) as e:
            self._json({"galat": str(e)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001 — selalu jawab, jangan biarkan halaman menggantung
            self._json({"galat": f"Galat tak terduga: {type(e).__name__}: {e}"}, 500)


def buka_folder(f: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(f)])
    elif os.name == "nt":
        os.startfile(str(f))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(f)])


class ServerStudio(ThreadingHTTPServer):
    """Di Windows, SO_REUSEADDR membuat dua program bisa memakai port yang sama (Studio mengira port kosong,
    padahal permintaan masuk ke aplikasi lain). Pakai SO_EXCLUSIVEADDRUSE agar port yang terpakai benar-benar ditolak."""
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        import socket
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def server_hidup(url: str) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(url + "/api/status", timeout=1) as r:  # hanya Studio Maliki yang dianggap
            d = json.loads(r.read().decode("utf-8", "replace") or "null")
            return isinstance(d, dict) and {"jenis", "status", "log"} <= d.keys()
    except Exception:  # noqa: BLE001
        return False


def main():
    global PORT
    DATA.mkdir(parents=True, exist_ok=True)
    ANTREAN.mulai()
    PEMANTAU.mulai()
    PELACAK.mulai()
    # Port 8787 bisa dipakai aplikasi lain (mis. server pengembangan). Cari Studio yang sudah jalan,
    # kalau tidak ada, pakai port kosong pertama dari 8787–8806.
    sudah, srv = False, None
    for p in range(PORT, PORT + 20):
        url = f"http://127.0.0.1:{p}"
        if server_hidup(url):
            PORT, sudah = p, True
            break
        try:
            srv = ServerStudio(("127.0.0.1", p), H)
            PORT = p
            break
        except OSError:
            print(f"Port {p} sedang dipakai aplikasi lain, coba port berikutnya.")
    url = f"http://127.0.0.1:{PORT}"
    if not sudah:
        if srv is None:
            raise SystemExit("Tidak ada port kosong di 8787–8806. Tutup aplikasi lain lalu buka Studio lagi.")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"Studio Maliki jalan di {url}")
    if "--tanpa-buka" in sys.argv:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return
    if "--browser" in sys.argv:
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return
    # Jendela aplikasi native (WebKit di macOS, WebView2 di Windows). Tutup jendela = mesin berhenti.
    try:
        import webview
    except Exception as e:  # noqa: BLE001
        print(f"Jendela native tidak tersedia ({e}); membuka browser.")
        webbrowser.open(url)
        while True:
            time.sleep(3600)

    class Jembatan:
        def unduh(self, nama: str, isi: str) -> str:
            # hanya nama file (tanpa folder) → tidak bisa menulis di luar Downloads
            nama = re.sub(r"[^\w. ()-]", "_", Path(str(nama)).name).lstrip(".")[:120] or "unduhan.txt"
            if Path(nama).suffix.lower() not in (".json", ".srt", ".txt", ".md", ".csv", ".vtt"):
                nama += ".txt"
            tujuan = Path.home() / "Downloads" / nama
            tujuan.write_text(isi, encoding="utf-8")
            return str(tujuan)

        def pilih_video(self) -> str:
            """Pemilih file bawaan OS → jalur asli (tanpa menyalin lewat WebView; video besar membuat WebView2
            di Windows kehabisan memori dan layar jadi hitam)."""
            jenis = ("Video (*.mp4;*.mov;*.mkv;*.webm;*.avi;*.m4v;*.MP4;*.MOV)", "Semua file (*.*)")
            tipe = getattr(getattr(webview, "FileDialog", None), "OPEN", None) or webview.OPEN_DIALOG
            hasil = webview.windows[0].create_file_dialog(tipe, allow_multiple=False, file_types=jenis)
            return str(hasil[0]) if hasil else ""

    if sys.platform == "darwin":
        # Nama & ikon di menu bar/Dock: "Studio Maliki", bukan "python"
        try:
            from AppKit import NSApplication, NSImage
            from Foundation import NSBundle
            info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
            info["CFBundleName"] = "Studio Maliki"
        except Exception:  # noqa: BLE001
            pass

    def setelah_mulai():
        # Daftarkan penangan "drop" di sisi Python → file yang diseret membawa jalur aslinya (pywebviewFullPath),
        # jadi tidak perlu disalin lewat WebView.
        try:
            from webview.dom import DOMEventHandler
            webview.windows[0].dom.document.events.drop += DOMEventHandler(lambda e: None, prevent_default=False)
        except Exception:  # noqa: BLE001
            pass
        # ikon Dock dipasang setelah aplikasi Cocoa berjalan (kalau terlalu awal, ditimpa ikon python)
        if sys.platform != "darwin":
            return
        try:
            from AppKit import NSApplication, NSImage
            from PyObjCTools import AppHelper
            gambar = NSImage.alloc().initWithContentsOfFile_(str(AKAR / "aset" / "ikon.png"))
            AppHelper.callAfter(lambda: NSApplication.sharedApplication().setApplicationIconImage_(gambar))
        except Exception:  # noqa: BLE001
            pass

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.create_window("Studio Maliki", url, width=1360, height=880, min_size=(900, 640),
                          background_color="#07090F", js_api=Jembatan())
    webview.start(setelah_mulai)
    PEMANTAU.hentikan_semua()
    KERJA.batal()
    KERJA_UNGGAH.batal()
    os._exit(0)


if __name__ == "__main__":
    main()
