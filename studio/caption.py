# kredit-maliki: Template oleh Maliki Saputra (@malikisaputra) — https://malikisaputra.biz.id · Lisensi Nonkomersial Maliki Studio 1.0 (alat/LISENSI.md): dilarang dijual ulang.
"""Caption per platform dan profil kampanye clipper.

Satu klip punya judul, caption, dan hashtag dari AI. Modul ini menyusunnya ulang sesuai aturan tiap platform
(panjang, jumlah hashtag, judul ada atau tidak) dan aturan kampanye (tag/mention wajib, teks wajib seperti
pengungkapan iklan, durasi). Hasilnya selalu ditampilkan di layar review, jadi pengguna tetap bisa mengubahnya.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path

# Batas resmi tiap platform (per Nov 2026). "judul" None = platform tidak punya kolom judul terpisah.
PLATFORM = {
    "youtube":   {"nama": "YouTube Shorts", "judul": 100, "teks": 5000, "hashtag": 15, "tambah": ["Shorts"]},
    "tiktok":    {"nama": "TikTok", "judul": None, "teks": 2200, "hashtag": 30, "tambah": []},
    "instagram": {"nama": "Instagram Reels", "judul": None, "teks": 2200, "hashtag": 30, "tambah": []},
    "facebook":  {"nama": "Facebook Reels", "judul": None, "teks": 2200, "hashtag": 30, "tambah": []},
    "threads":   {"nama": "Threads", "judul": None, "teks": 500, "hashtag": 1, "tambah": []},
    "x":         {"nama": "X", "judul": None, "teks": 280, "hashtag": 3, "tambah": []},
}
BIDANG_KAMPANYE = ("nama", "platform", "tag_wajib", "mention_wajib", "teks_wajib", "durasi_min", "durasi_maks",
                   "sumber_izin", "tarif_per_1000", "mata_uang", "catatan", "konten_bermerek")


def _tag(t: str) -> str:
    return re.sub(r"[^\w]", "", str(t).strip().lstrip("#"), flags=re.UNICODE)[:60]


def _mention(m: str) -> str:
    m = re.sub(r"[^\w.]", "", str(m).strip().lstrip("@"))[:40]
    return f"@{m}" if m else ""


def _daftar(nilai) -> list[str]:
    if isinstance(nilai, str):
        nilai = re.split(r"[,\n]", nilai)
    return [str(x).strip() for x in (nilai or []) if str(x).strip()]


def _potong(teks: str, batas: int) -> str:
    if len(teks) <= batas:
        return teks
    pendek = teks[: batas - 1]
    pendek = pendek[: pendek.rfind(" ")] if " " in pendek[batas // 2:] else pendek
    return pendek.rstrip() + "…"


def susun(klip: dict, info: dict, platform: str, kampanye: dict | None = None) -> dict:
    """Kembalikan {judul, teks, tag, peringatan} siap ditampilkan di layar review."""
    if platform not in PLATFORM:
        raise ValueError(f"Platform tidak dikenal: {platform}")
    b = PLATFORM[platform]
    kampanye = kampanye or {}
    peringatan: list[str] = []
    judul = " ".join(str(klip.get("judul_banner") or klip.get("judul") or "").split())
    isi = " ".join(str(klip.get("caption") or judul).split())

    wajib = [_tag(t) for t in _daftar(kampanye.get("tag_wajib"))]
    biasa = [_tag(t) for t in (_daftar(info.get("hashtag")) + _daftar(klip.get("kata_kunci"))[:3])]
    tag: list[str] = []
    for t in wajib + b["tambah"] + biasa:
        if t and t.lower() not in {x.lower() for x in tag}:
            tag.append(t)
    if len(tag) > b["hashtag"]:
        buang = [t for t in tag[b["hashtag"]:] if t in wajib]
        if buang:
            peringatan.append(f"{b['nama']} hanya mengizinkan {b['hashtag']} hashtag; tag wajib {', '.join(buang)} terpotong.")
        tag = tag[: b["hashtag"]]
    mention = " ".join(x for x in (_mention(m) for m in _daftar(kampanye.get("mention_wajib"))) if x)
    teks_wajib = str(kampanye.get("teks_wajib") or "").strip()

    # Bagian wajib (mention, teks wajib, hashtag) diutamakan; caption AI yang dipotong bila kepanjangan.
    ekor = "\n\n".join(x for x in (mention, teks_wajib, " ".join(f"#{t}" for t in tag)) if x)
    if b["judul"] is None and platform in ("tiktok", "instagram", "facebook", "threads", "x") and judul and judul.lower() not in isi.lower():
        isi = f"{judul}\n{isi}" if platform != "x" else judul
    ruang = b["teks"] - len(ekor) - (2 if ekor else 0)
    if ruang < 20:
        peringatan.append(f"Bagian wajib kampanye terlalu panjang untuk {b['nama']} ({b['teks']} karakter).")
        ruang = max(ruang, 0)
    if len(isi) > ruang:
        isi = _potong(isi, ruang) if ruang else ""
        peringatan.append(f"Caption dipendekkan agar muat {b['teks']} karakter {b['nama']}.")
    teks = "\n\n".join(x for x in (isi, ekor) if x)[: b["teks"]]

    durasi = float(klip.get("selesai", 0) or 0) - float(klip.get("mulai", 0) or 0)
    dmin, dmaks = float(kampanye.get("durasi_min") or 0), float(kampanye.get("durasi_maks") or 0)
    if dmin and durasi < dmin:
        peringatan.append(f"Durasi klip {durasi:.0f} dtk, kampanye minta minimal {dmin:.0f} dtk.")
    if dmaks and durasi > dmaks:
        peringatan.append(f"Durasi klip {durasi:.0f} dtk, kampanye minta maksimal {dmaks:.0f} dtk.")
    if platform == "youtube" and durasi > 180:
        peringatan.append("Lebih dari 3 menit: YouTube tidak akan menganggapnya Shorts.")
    plat_kampanye = _daftar(kampanye.get("platform"))
    if plat_kampanye and platform not in plat_kampanye:
        peringatan.append(f"Kampanye ini tidak mencantumkan {b['nama']} sebagai platform tujuan.")
    return {"platform": platform, "judul": judul[: b["judul"]] if b["judul"] else "", "teks": teks, "tag": tag,
            "peringatan": peringatan, "batas": b, "sumber_izin": str(kampanye.get("sumber_izin") or ""),
            "konten_bermerek": bool(kampanye.get("konten_bermerek"))}


# ── Profil kampanye ─────────────────────────────────────────────────────────
def _berkas(data: Path) -> Path:
    return data / "kampanye.json"


def daftar_kampanye(data: Path) -> list[dict]:
    try:
        return json.loads(_berkas(data).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def cari_kampanye(data: Path, id_: str) -> dict | None:
    return next((k for k in daftar_kampanye(data) if k["id"] == id_), None) if id_ else None


def simpan_kampanye(data: Path, d: dict) -> dict:
    nama = " ".join(str(d.get("nama", "")).split())[:80]
    if not nama:
        raise ValueError("Nama kampanye wajib diisi.")
    k = {f: d.get(f) for f in BIDANG_KAMPANYE}
    k["nama"] = nama
    k["platform"] = [p for p in _daftar(d.get("platform")) if p in PLATFORM]
    k["tag_wajib"] = [t for t in (_tag(x) for x in _daftar(d.get("tag_wajib"))) if t][:30]
    k["mention_wajib"] = [m for m in (_mention(x) for x in _daftar(d.get("mention_wajib"))) if m][:10]
    k["teks_wajib"] = str(d.get("teks_wajib") or "")[:300]
    k["sumber_izin"] = str(d.get("sumber_izin") or "")[:300]
    k["catatan"] = str(d.get("catatan") or "")[:1000]
    k["mata_uang"] = (str(d.get("mata_uang") or "USD").upper()[:3]) or "USD"
    k["konten_bermerek"] = bool(d.get("konten_bermerek"))
    for f in ("durasi_min", "durasi_maks", "tarif_per_1000"):
        try:
            k[f] = max(float(d.get(f) or 0), 0)
        except (TypeError, ValueError):
            raise ValueError(f"{f} harus angka.") from None
    if not k["sumber_izin"]:
        raise ValueError("Isi bukti izin kampanye (link kampanye atau izin tertulis pemilik konten).")
    semua = daftar_kampanye(data)
    lama = next((x for x in semua if x["id"] == d.get("id")), None)
    if lama:
        lama.update(k)
        k = lama
    else:
        k.update(id=secrets.token_hex(6), dibuat=time.time())
        semua.append(k)
    sementara = _berkas(data).with_suffix(".tmp")
    data.mkdir(parents=True, exist_ok=True)
    sementara.write_text(json.dumps(semua, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(sementara, _berkas(data))
    return k


def hapus_kampanye(data: Path, id_: str) -> None:
    semua = [k for k in daftar_kampanye(data) if k["id"] != id_]
    _berkas(data).write_text(json.dumps(semua, ensure_ascii=False, indent=2), encoding="utf-8")
