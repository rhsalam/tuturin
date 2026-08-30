"""Token "ingat perangkat ini": masuk sekali per perangkat, lalu tidak lagi.

Yang disimpan adalah HASH token, bukan tokennya. Berkas perangkat.json yang
bocor karena itu tidak bisa dipakai untuk masuk.

BATAS YANG PERLU DIINGAT: di HTTP token ini melintas tanpa enkripsi, sehingga
bisa disadap di jaringan yang sama. Halaman pencabutan adalah penyeimbangnya.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path

NAMA_COOKIE = "perangkat"
UMUR_DETIK = 90 * 24 * 3600
BERKAS = "perangkat.json"


def _jalur(base: Path) -> Path:
    return base / BERKAS


def _sidik(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _muat(base: Path) -> list[dict]:
    f = _jalur(base)
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []   # berkas rusak — perlakukan seperti belum ada perangkat


def _simpan(base: Path, data: list[dict]) -> None:
    f = _jalur(base)
    try:
        f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        f.chmod(0o600)
    except OSError:
        pass   # gagal menyimpan bukan alasan menolak login


def label_dari_ua(ua: str) -> str:
    """Nama perangkat yang bisa dikenali manusia, dari User-Agent."""
    ua = ua or ""
    if re.search(r"iPhone", ua):        alat = "iPhone"
    elif re.search(r"iPad", ua):        alat = "iPad"
    elif re.search(r"Android", ua):     alat = "Android"
    elif re.search(r"Macintosh|Mac OS", ua):  alat = "Mac"
    elif re.search(r"Windows", ua):     alat = "Windows"
    elif re.search(r"Linux", ua):       alat = "Linux"
    else:                               alat = "perangkat lain"

    # Urutan penting: Edge dan Chrome sama-sama menyebut "Safari" di UA-nya.
    if re.search(r"Edg/", ua):          jelajah = "Edge"
    elif re.search(r"OPR/|Opera", ua):  jelajah = "Opera"
    elif re.search(r"Firefox/", ua):    jelajah = "Firefox"
    elif re.search(r"Chrome/", ua):     jelajah = "Chrome"
    elif re.search(r"Safari/", ua):     jelajah = "Safari"
    else:                               jelajah = "peramban"
    return f"{jelajah} di {alat}"


def terbitkan(base: Path, ua: str) -> str:
    """Buat token baru, simpan sidiknya, kembalikan token mentahnya."""
    token = secrets.token_urlsafe(32)
    data = _muat(base)
    kini = time.time()
    data.append({
        "sidik": _sidik(token),
        "label": label_dari_ua(ua),
        "dibuat": kini,
        "terakhir": kini,
    })
    _simpan(base, data)
    return token


def periksa(base: Path, token: str) -> str | None:
    """Kembalikan sidik bila token sah, dan perbarui waktu terakhir dipakai."""
    if not token:
        return None
    sidik = _sidik(token)
    data = _muat(base)
    for d in data:
        if hmac.compare_digest(d.get("sidik", ""), sidik):
            # Hanya tulis ulang bila selisihnya berarti, agar tidak menulis
            # berkas pada setiap permintaan.
            if time.time() - d.get("terakhir", 0) > 300:
                d["terakhir"] = time.time()
                _simpan(base, data)
            return sidik
    return None


def daftar(base: Path, sidik_kini: str | None = None) -> list[dict]:
    keluar = []
    for d in sorted(_muat(base), key=lambda x: x.get("terakhir", 0), reverse=True):
        keluar.append({
            "sidik": d.get("sidik", ""),
            "label": d.get("label", "perangkat"),
            "dibuat": d.get("dibuat", 0),
            "terakhir": d.get("terakhir", 0),
            "ini": d.get("sidik") == sidik_kini,
        })
    return keluar


def cabut(base: Path, sidik: str) -> bool:
    data = _muat(base)
    sisa = [d for d in data if d.get("sidik") != sidik]
    if len(sisa) == len(data):
        return False
    _simpan(base, sisa)
    return True


def cabut_semua(base: Path, kecuali: str | None = None) -> int:
    data = _muat(base)
    sisa = [d for d in data if kecuali and d.get("sidik") == kecuali]
    _simpan(base, sisa)
    return len(data) - len(sisa)
