# kredit-maliki: Template oleh Maliki Saputra (@malikisaputra) — https://malikisaputra.biz.id · Lisensi Nonkomersial Maliki Studio 1.0 (alat/LISENSI.md): dilarang dijual ulang.
"""Antrean kerja Studio: beberapa jalur berjalan bersamaan, tugas tersimpan di disk.

- Satu jalur = satu proses sekaligus (mis. "utama" untuk analisis/render/motion, "unggah" untuk posting).
  Jalur berbeda berjalan paralel, jadi render tidak perlu menunggu unggahan dan sebaliknya.
- Tugas yang masih menunggu tetap ada setelah Studio ditutup dan dilanjutkan saat dibuka lagi.
  Tugas yang sedang jalan saat Studio ditutup ditandai gagal ("terputus") supaya bisa diulang dengan sadar.
- Tugas bisa dijadwalkan (jadwal = waktu unix) dan diberi jeda minimum antar-posting per platform.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

STATUS_AKHIR = ("selesai", "gagal", "batal")


class Kerja:
    """Menjalankan langkah-langkah perintah satu per satu dan menampung log-nya (satu proses sekaligus)."""

    def __init__(self, env_fn: Callable[[], dict] | None = None):
        self.kunci = threading.Lock()
        self.env_fn = env_fn or (lambda: os.environ.copy())
        self.id = 0
        self.jenis = ""
        self.log: list[str] = []
        self.status = "siap"  # siap | jalan | selesai | gagal | batal
        self.folder: Path | None = None
        self.proc: subprocess.Popen | None = None
        self.hasil: dict = {}

    @property
    def sibuk(self) -> bool:
        return self.status == "jalan"

    def jalankan(self, jenis: str, langkah: list[tuple[list[str], Path]], folder: Path, setelah=None) -> int:
        with self.kunci:
            if self.status == "jalan":
                raise RuntimeError("Masih ada proses berjalan. Tunggu selesai atau batalkan.")
            self.id += 1
            self.jenis, self.log, self.status, self.folder, self.hasil = jenis, [], "jalan", folder, {}
            nomor = self.id
        threading.Thread(target=self._kerja, args=(langkah, setelah), daemon=True).start()
        return nomor

    def _kerja(self, langkah, setelah):
        try:
            env = self.env_fn()
            for cmd, cwd in langkah:
                if self.status == "batal":
                    return
                self.log.append("$ " + " ".join(Path(c).name if i < 2 else c for i, c in enumerate(cmd)))
                self.proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True, bufsize=1,
                                             encoding="utf-8", errors="replace",
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                for baris in self.proc.stdout:
                    baris = baris.rstrip()
                    if baris and not re.search(r"objc\[|AVFAudioReceiver|Warning|warn\(|^\s*$", baris):
                        self.log.append(baris)
                        self.log[:] = self.log[-400:]
                if self.proc.wait() != 0:
                    self.status = "batal" if self.status == "batal" else "gagal"
                    return
            if self.status == "batal":
                return
            if setelah:
                setelah(self)
            self.status = "selesai"
        except Exception as e:  # noqa: BLE001
            self.log.append(f"Galat: {e}")
            self.status = "batal" if self.status == "batal" else "gagal"
        finally:
            self.proc = None

    def batal(self):
        if self.status == "jalan":
            self.status = "batal"  # juga menghentikan langkah berikutnya bila dibatalkan di antara dua langkah
            if self.proc:
                self.proc.terminate()

    def ringkas(self) -> dict:
        return {"id": self.id, "jenis": self.jenis, "status": self.status, "log": self.log[-200:], "hasil": self.hasil}


class Antrean:
    def __init__(self, berkas: Path, pelaksana: dict[str, Kerja], setelah: dict[str, Callable] | None = None,
                 jeda_fn: Callable[[str], int] | None = None):
        self.berkas = berkas
        self.pelaksana = pelaksana
        self.setelah = setelah or {}  # nama → fungsi(kerja, **arg); nama disimpan di disk, bukan fungsinya
        self.jeda_fn = jeda_fn or (lambda platform: 0)
        self.kunci = threading.RLock()
        self.tugas: list[dict] = []
        self.aktif: dict[str, dict] = {}  # jalur → {"id": id tugas, "kerja": id Kerja}
        self.posting_terakhir: dict[str, float] = {}
        self._muat()

    # ── penyimpanan ──
    def _muat(self):
        try:
            d = json.loads(self.berkas.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        self.tugas = d.get("tugas", [])
        self.posting_terakhir = d.get("posting_terakhir", {})
        for t in self.tugas:
            if t["status"] == "jalan":  # Studio tertutup saat tugas jalan
                t.update(status="gagal", galat="Terputus karena Studio ditutup. Ulangi bila perlu.", selesai=time.time())

    def _simpan(self):
        self.berkas.parent.mkdir(parents=True, exist_ok=True)
        selesai = [t for t in self.tugas if t["status"] in STATUS_AKHIR]
        if len(selesai) > 200:  # buang riwayat tertua, tugas yang menunggu tidak pernah dibuang
            buang = {t["id"] for t in selesai[:-200]}
            self.tugas = [t for t in self.tugas if t["id"] not in buang]
        sementara = self.berkas.with_suffix(".tmp")
        sementara.write_text(json.dumps({"tugas": self.tugas, "posting_terakhir": self.posting_terakhir},
                                        ensure_ascii=False), encoding="utf-8")
        os.replace(sementara, self.berkas)

    # ── API ──
    def tambah(self, jalur: str, jenis: str, langkah: list[tuple[list[str], Path]], folder: Path,
               setelah: tuple[str, dict] | None = None, label: str = "", jadwal: float = 0, platform: str = "",
               info: dict | None = None) -> dict:
        if jalur not in self.pelaksana:
            raise ValueError(f"Jalur antrean tidak dikenal: {jalur}")
        with self.kunci:
            t = {"id": f"{int(time.time() * 1000):x}{len(self.tugas):03x}", "jalur": jalur, "jenis": jenis,
                 "label": label or jenis, "langkah": [[list(map(str, c)), str(w)] for c, w in langkah],
                 "folder": str(folder), "setelah": list(setelah) if setelah else None, "status": "menunggu",
                 "dibuat": time.time(), "jadwal": float(jadwal or 0), "platform": platform, "info": info or {},
                 "mulai": 0, "selesai": 0, "log": [], "galat": ""}
            self.tugas.append(t)
            self._simpan()
        self.detak()
        return {"id": t["id"], "posisi": self.posisi(t["id"]), "status": self.cari(t["id"])["status"]}

    def cari(self, id_: str) -> dict | None:
        return next((t for t in self.tugas if t["id"] == id_), None)

    def posisi(self, id_: str) -> int:
        """0 = sedang jalan / siap jalan, n = ada n tugas di depannya pada jalur yang sama."""
        t = self.cari(id_)
        if not t or t["status"] != "menunggu":
            return 0
        depan = [x for x in self.tugas if x["jalur"] == t["jalur"] and x["status"] in ("menunggu", "jalan")]
        return [x["id"] for x in depan].index(id_)

    def batal(self, id_: str) -> bool:
        with self.kunci:
            t = self.cari(id_)
            if not t or t["status"] in STATUS_AKHIR:
                return False
            if t["status"] == "menunggu":
                t.update(status="batal", selesai=time.time())
            elif self.aktif.get(t["jalur"], {}).get("id") == id_:
                self.pelaksana[t["jalur"]].batal()
            self._simpan()
            return True

    def ulangi(self, id_: str) -> dict:
        t = self.cari(id_)
        if not t or t["status"] not in ("gagal", "batal"):
            raise ValueError("Hanya tugas yang gagal atau dibatalkan yang bisa diulang.")
        return self.tambah(t["jalur"], t["jenis"], [(c, Path(w)) for c, w in t["langkah"]], Path(t["folder"]),
                           tuple(t["setelah"]) if t["setelah"] else None, t["label"], 0, t["platform"], t["info"])

    def daftar(self, batas: int = 60) -> dict:
        with self.kunci:
            self._perbarui_log()
            tugas = [{k: v for k, v in t.items() if k != "langkah"} for t in self.tugas[-batas:]][::-1]
            jalur = {j: k.status if k.sibuk else "siap" for j, k in self.pelaksana.items()}
            return {"tugas": tugas, "jalur": jalur, "sekarang": time.time()}

    # ── penjadwal ──
    def _perbarui_log(self):
        for jalur, a in self.aktif.items():
            t = self.cari(a["id"])
            if t:
                t["log"] = self.pelaksana[jalur].log[-30:]

    def _siap_jalan(self, t: dict, sekarang: float) -> bool:
        if t["jadwal"] and t["jadwal"] > sekarang:
            return False
        if t["platform"]:
            jeda = self.jeda_fn(t["platform"]) or 0
            if sekarang - self.posting_terakhir.get(t["platform"], 0) < jeda:
                return False
        return True

    def detak(self):
        with self.kunci:
            sekarang = time.time()
            berubah = False
            for jalur, kerja in self.pelaksana.items():
                a = self.aktif.get(jalur)
                if a and (kerja.id != a["kerja"] or not kerja.sibuk):
                    t = self.cari(a["id"])
                    if t:
                        st = kerja.status if kerja.id == a["kerja"] else "gagal"
                        t.update(status=st if st in STATUS_AKHIR else "gagal", selesai=sekarang, log=kerja.log[-30:])
                        if t["status"] == "gagal":
                            t["galat"] = next((b for b in reversed(kerja.log) if b.strip() and not b.startswith("$")), "Gagal.")[:300]
                        if t["status"] == "selesai" and t["platform"]:
                            self.posting_terakhir[t["platform"]] = sekarang
                    del self.aktif[jalur]
                    berubah = True
                if jalur in self.aktif or kerja.sibuk:
                    continue  # jalur ini sedang dipakai (bisa juga oleh proses di luar antrean, mis. pemasangan)
                t = next((x for x in self.tugas if x["jalur"] == jalur and x["status"] == "menunggu"
                          and self._siap_jalan(x, sekarang)), None)
                if not t:
                    continue
                fungsi = None
                if t["setelah"]:
                    nama, arg = t["setelah"]
                    f = self.setelah.get(nama)
                    fungsi = (lambda k, f=f, arg=arg: f(k, **arg)) if f else None
                try:
                    Path(t["folder"]).mkdir(parents=True, exist_ok=True)
                    nomor = kerja.jalankan(t["jenis"], [(c, Path(w)) for c, w in t["langkah"]], Path(t["folder"]), fungsi)
                except RuntimeError:
                    continue
                self.aktif[jalur] = {"id": t["id"], "kerja": nomor}
                t.update(status="jalan", mulai=sekarang)
                berubah = True
            if berubah:
                self._simpan()

    def mulai(self, selang: float = 1.0):
        def putar():
            while True:
                try:
                    self.detak()
                except Exception:  # noqa: BLE001 — penjadwal tidak boleh mati
                    pass
                time.sleep(selang)
        threading.Thread(target=putar, daemon=True).start()
