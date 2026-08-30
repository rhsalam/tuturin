"""Pembacaan pengaturan dari lingkungan dan berkas .env."""
from __future__ import annotations

import os
import secrets
from pathlib import Path


def baca_env(base: Path) -> dict[str, str]:
    nilai: dict[str, str] = {}
    env = base / ".env"
    if not env.exists():
        return nilai
    for baris in env.read_text(encoding="utf-8").splitlines():
        baris = baris.strip()
        if not baris or baris.startswith("#") or "=" not in baris:
            continue
        nama, _, isi = baris.partition("=")
        nilai[nama.strip()] = isi.strip().strip("'\"")
    return nilai


def ambil(base: Path, nama: str, bawaan: str = "") -> str:
    """Lingkungan menang atas .env, supaya bisa ditimpa sekali jalan."""
    dari_env = os.environ.get(nama, "").strip()
    if dari_env:
        return dari_env
    return baca_env(base).get(nama, bawaan)


def pastikan_rahasia(base: Path) -> str:
    """Kunci penanda cookie sesi. Dibuat sekali lalu disimpan di .env.

    Tanpa kunci tetap, setiap kali server dimulai ulang semua sesi terputus.
    """
    kunci = ambil(base, "SECRET_KEY")
    if kunci:
        return kunci
    kunci = secrets.token_hex(32)
    env = base / ".env"
    try:
        awal = env.read_text(encoding="utf-8") if env.exists() else ""
        if awal and not awal.endswith("\n"):
            awal += "\n"
        env.write_text(f"{awal}SECRET_KEY={kunci}\n", encoding="utf-8")
        env.chmod(0o600)
    except OSError:
        pass  # tidak bisa menyimpan — kunci tetap dipakai untuk sesi ini
    return kunci


def alamat_lan() -> list[str]:
    """IP mesin ini di jaringan lokal, untuk ditampilkan saat mulai.

    Hanya memakai trik "rute UDP": membuka soket datagram ke alamat luar dan
    membaca alamat lokal yang dipilih kernel. Tidak ada paket yang dikirim.
    Jalur socket.getaddrinfo(gethostname()) sengaja TIDAK dipakai — di macOS
    jalur itu menggantung 5 detik lalu gagal ketika hostname .local tidak
    dapat diselesaikan, sehingga menunda tampilnya alamat saat server mulai.
    """
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return [ip] if not ip.startswith("127.") else []
    except OSError:
        return []
