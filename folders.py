"""Daftar folder yang berdiri sendiri.

Sebelumnya folder hanya DITURUNKAN dari label pada job, sehingga folder kosong
mustahil ada — begitu isinya dipindahkan, foldernya lenyap. Daftar terpisah ini
membuat "Buat Folder" bermakna.

Folder tetap sekadar label: tidak ada direktori yang dibuat di disk.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

BERKAS = "folders.json"
MAKS_NAMA = 40


def _jalur(base: Path) -> Path:
    return base / BERKAS


def rapikan(nama: str) -> str:
    return re.sub(r"\s+", " ", str(nama or "")).strip()[:MAKS_NAMA]


def _muat(base: Path) -> list[dict]:
    f = _jalur(base)
    if not f.exists():
        return []
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _simpan(base: Path, data: list[dict]) -> None:
    try:
        _jalur(base).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    except OSError:
        pass


def daftar(base: Path) -> list[str]:
    return [d["nama"] for d in _muat(base) if d.get("nama")]


def buat(base: Path, nama: str) -> str | None:
    """Kembalikan nama yang dibuat, atau None bila kosong / sudah ada."""
    nama = rapikan(nama)
    if not nama:
        return None
    data = _muat(base)
    if any(d.get("nama", "").lower() == nama.lower() for d in data):
        return None
    data.append({"nama": nama, "dibuat": time.time()})
    _simpan(base, data)
    return nama


def ganti_nama(base: Path, lama: str, baru: str) -> bool:
    baru = rapikan(baru)
    if not baru:
        return False
    data = _muat(base)
    ubah = False
    for d in data:
        if d.get("nama") == lama:
            d["nama"] = baru
            ubah = True
    if not ubah:                      # belum terdaftar — daftarkan saja
        data.append({"nama": baru, "dibuat": time.time()})
    _simpan(base, data)
    return True


def hapus(base: Path, nama: str) -> bool:
    data = _muat(base)
    sisa = [d for d in data if d.get("nama") != nama]
    _simpan(base, sisa)
    return len(sisa) != len(data)


def pastikan_ada(base: Path, nama: str) -> None:
    """Daftarkan folder yang dipakai job tetapi belum tercatat.

    Menjaga folder lama (dibuat sebelum daftar ini ada) tetap muncul.
    """
    nama = rapikan(nama)
    if nama and nama not in daftar(base):
        buat(base, nama)
