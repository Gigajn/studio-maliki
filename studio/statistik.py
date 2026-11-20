# kredit-maliki: Template oleh Maliki Saputra (@malikisaputra) — https://malikisaputra.biz.id · Lisensi Nonkomersial Maliki Studio 1.0 (alat/LISENSI.md): dilarang dijual ulang.
"""Lacak views tiap klip yang sudah diunggah dan hitung estimasi pendapatan per kampanye.

Sumber angka: API resmi tiap platform (YouTube videos.list, TikTok video.query, Instagram insights).
Draf TikTok belum punya ID posting; setelah kamu memposting dari aplikasi TikTok, tautkan link videonya.
Platform atau kampanye yang menghitung views sendiri bisa diisi angka manual.

Estimasi = views ÷ 1.000 × tarif kampanye. Ini perkiraan: angka resmi tetap dari dasbor kampanye
(yang biasanya punya syarat minimum views, batas bayar, dan verifikasi sendiri).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from types import ModuleType

BATAS = {"youtube": 50, "tiktok": 20, "instagram": 50}  # ID per panggilan API
RIWAYAT_TITIK = 90  # titik riwayat views per unggahan (sekali per hari)


def _baca_json(p: Path, bawaan):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return bawaan


def _tulis_json(p: Path, isi) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(isi, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


class Pelacak:
    def __init__(self, data: Path, modul: dict[str, ModuleType], kampanye_fn):
        self.data = data
        self.modul = modul              # platform → modul dengan statistik(data, ids)
        self.kampanye_fn = kampanye_fn  # () → daftar kampanye
        self.berkas = data / "unggah" / "statistik.json"
        self.riwayat_f = data / "unggah" / "riwayat.jsonl"
        self.kunci = threading.Lock()

    def unggahan(self) -> list[dict]:
        out = []
        try:
            for b in self.riwayat_f.read_text(encoding="utf-8").splitlines():
                try:
                    x = json.loads(b)
                except ValueError:
                    continue
                if x.get("platform") and x.get("id"):
                    out.append(x)
        except OSError:
            pass
        return out

    def _st(self) -> dict:
        st = _baca_json(self.berkas, {})
        for k in ("angka", "tautan", "manual", "galat"):
            st.setdefault(k, {})
        return st

    @staticmethod
    def kunci_entri(x: dict) -> str:
        return f"{x['platform']}:{x['id']}"

    def _id_api(self, x: dict, st: dict) -> str:
        return st["tautan"].get(self.kunci_entri(x)) or str(x["id"])

    def segarkan(self) -> dict:
        """Ambil angka terbaru dari API semua platform. Galat per platform dicatat, tidak menghentikan yang lain."""
        with self.kunci:
            st = self._st()
            per: dict[str, dict[str, str]] = {}
            for x in self.unggahan():
                if x["platform"] in self.modul:
                    per.setdefault(x["platform"], {})[self._id_api(x, st)] = self.kunci_entri(x)
            sekarang, hari = time.time(), time.strftime("%Y-%m-%d")
            for platform, ids in per.items():
                daftar, n = list(ids), BATAS.get(platform, 20)
                try:
                    for i in range(0, len(daftar), n):
                        for id_api, nilai in self.modul[platform].statistik(self.data, daftar[i:i + n]).items():
                            kunci = ids.get(str(id_api))
                            if not kunci:
                                continue
                            lama = st["angka"].get(kunci, {})
                            titik = [t for t in lama.get("riwayat", []) if t[0] != hari][-(RIWAYAT_TITIK - 1):]
                            st["angka"][kunci] = {**{k: int(nilai.get(k) or 0) for k in ("views", "suka", "komentar", "bagikan")},
                                                  "waktu": sekarang, "riwayat": titik + [[hari, int(nilai.get("views") or 0)]]}
                    st["galat"].pop(platform, None)
                except Exception as e:  # noqa: BLE001 — satu platform gagal, yang lain tetap jalan
                    st["galat"][platform] = str(e)[:200]
            st["terakhir"] = sekarang
            _tulis_json(self.berkas, st)
            return self.ringkasan()

    def tautkan(self, platform: str, id_: str, url: str) -> None:
        """Draf TikTok → video yang sudah diposting. Terima link video atau ID angka."""
        m = re.search(r"/video/(\d{6,25})", url or "") or re.fullmatch(r"\s*(\d{6,25})\s*", url or "")
        if platform != "tiktok" or not m:
            raise ValueError("Tempel link video TikTok (…/video/123…) yang sudah kamu posting.")
        with self.kunci:
            st = self._st()
            st["tautan"][f"{platform}:{id_}"] = m.group(1)
            _tulis_json(self.berkas, st)

    def isi_manual(self, platform: str, id_: str, views) -> None:
        with self.kunci:
            st = self._st()
            kunci = f"{platform}:{id_}"
            if views in (None, ""):
                st["manual"].pop(kunci, None)
            else:
                v = int(float(views))
                if v < 0:
                    raise ValueError("Views tidak boleh negatif.")
                st["manual"][kunci] = v
            _tulis_json(self.berkas, st)

    def ringkasan(self) -> dict:
        st = self._st()
        kampanye = {k["id"]: k for k in self.kampanye_fn()}
        baris, per_kampanye, total = [], {}, {"views": 0, "unggahan": 0, "pendapatan": {}}
        for x in self.unggahan():
            kunci = self.kunci_entri(x)
            a = st["angka"].get(kunci, {})
            manual = st["manual"].get(kunci)
            views = manual if manual is not None else a.get("views", 0)
            km = kampanye.get(x.get("kampanye") or "")
            tarif = float(km.get("tarif_per_1000") or 0) if km else 0.0
            uang = (km.get("mata_uang") or "USD") if km else ""
            est = round(views / 1000 * tarif, 2) if tarif else 0.0
            perlu_tautan = x["platform"] == "tiktok" and not str(x["id"]).isdigit() and kunci not in st["tautan"]
            baris.append({"kunci": kunci, "platform": x["platform"], "id": x["id"], "judul": x.get("judul", ""), "url": x.get("url", ""),
                          "waktu": x.get("waktu", ""), "proyek": x.get("proyek", ""), "no": x.get("no"),
                          "kampanye": km["nama"] if km else "", "kampanye_id": km["id"] if km else "",
                          "views": views, "manual": manual is not None, "suka": a.get("suka", 0), "komentar": a.get("komentar", 0),
                          "bagikan": a.get("bagikan", 0), "riwayat": a.get("riwayat", []), "diperbarui": a.get("waktu", 0),
                          "estimasi": est, "mata_uang": uang, "perlu_tautan": perlu_tautan})
            total["views"] += views
            total["unggahan"] += 1
            if est:
                total["pendapatan"][uang] = round(total["pendapatan"].get(uang, 0) + est, 2)
            if km:
                p = per_kampanye.setdefault(km["id"], {"id": km["id"], "nama": km["nama"], "tarif_per_1000": tarif, "mata_uang": uang,
                                                       "views": 0, "unggahan": 0, "estimasi": 0.0})
                p["views"] += views
                p["unggahan"] += 1
                p["estimasi"] = round(p["estimasi"] + est, 2)
        baris.sort(key=lambda b: b["waktu"], reverse=True)
        return {"unggahan": baris, "kampanye": sorted(per_kampanye.values(), key=lambda p: -p["views"]), "total": total,
                "terakhir": st.get("terakhir", 0), "galat": st["galat"]}

    def mulai(self, selang: int = 6 * 3600):
        def putar():
            time.sleep(60)
            while True:
                try:
                    if self.unggahan():
                        self.segarkan()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(selang)
        threading.Thread(target=putar, daemon=True).start()
