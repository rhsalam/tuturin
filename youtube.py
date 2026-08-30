"""Mengambil audio dari tautan video (YouTube dan situs lain yang didukung yt-dlp).

Modul ini sengaja memakai yt-dlp sebagai pustaka Python, bukan lewat subprocess
seperti ffmpeg dan whisper-cli. Alasannya: yt-dlp memang terpasang sebagai paket
di venv ini, dan lewat pustaka kita mendapat metadata terstruktur serta kait
kemajuan unduhan tanpa harus mengurai keluaran baris perintah.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp


class YouTubeError(RuntimeError):
    pass


# Wadah yang paling mungkin keluar dari "bestaudio". Dipakai untuk menemukan
# kembali berkas hasil unduhan bila yt-dlp tidak melaporkan namanya.
EKSTENSI = (".m4a", ".webm", ".opus", ".mp3", ".ogg", ".aac", ".mp4", ".mkv")

# YouTube melayani tiap "klien pemutar" secara berbeda, dan sebagian video
# ditolak oleh klien bawaan sambil tetap terbuka bagi klien lain — gejalanya
# pesan generik "This video is not available" walau videonya publik dan utuh.
# Daftar ini dicoba berurutan sampai ada yang memberi format audio.
KLIEN = ["default", "android", "tv", "web_safari", "mweb"]

OPSI_DASAR = {
    "quiet": True, "no_warnings": True, "noplaylist": True,
    "extractor_args": {"youtube": {"player_client": KLIEN}},
}


def _diam(*_a, **_k) -> None:
    pass


def periksa_url(url: str) -> str:
    """Tolak yang bukan http(s) dan yang mengarah ke jaringan dalam.

    Aplikasi ini bisa dibuka dari perangkat lain, jadi kolom URL adalah pintu
    tempat orang lain menyuruh server mengambil alamat pilihan mereka. Tanpa
    saringan ini, alamat seperti 127.0.0.1 atau 192.168.x.x bisa dipakai untuk
    mengintip layanan yang hanya terlihat dari dalam mesin.
    """
    url = (url or "").strip()
    if not url:
        raise YouTubeError("Tautan kosong.")
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        raise YouTubeError("Tautan harus diawali http:// atau https://")
    if not u.hostname:
        raise YouTubeError("Tautan tidak punya nama host.")
    try:
        infos = socket.getaddrinfo(u.hostname, None)
    except socket.gaierror:
        raise YouTubeError(f"Nama host tidak dikenal: {u.hostname}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise YouTubeError("Alamat jaringan dalam tidak boleh diunduh.")
    return url


def _nama_aman(judul: str) -> str:
    """Judul video jadi nama berkas: buang pemisah path dan aksara kendali."""
    judul = unicodedata.normalize("NFC", judul or "")
    judul = re.sub(r"[\x00-\x1f/\\:*?\"<>|]", " ", judul)
    judul = re.sub(r"\s+", " ", judul).strip(" .")
    return judul[:150] or "video"


def metadata(url: str) -> dict:
    """Ambil judul dan durasi tanpa mengunduh medianya. Ongkosnya ±1-2 detik.

    Dipanggil saat permintaan HTTP supaya pustaka langsung menampilkan judul
    dan durasi yang benar, alih-alih "video tanpa nama" sampai unduhan selesai.
    """
    opsi = {**OPSI_DASAR, "skip_download": True,
            "logger": type("L", (), {"debug": _diam, "warning": _diam,
                                     "error": _diam})()}
    try:
        with yt_dlp.YoutubeDL(opsi) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise YouTubeError(_pesan_ramah(str(exc)))

    if info.get("_type") == "playlist":            # noplaylist tidak selalu jalan
        entri = [e for e in (info.get("entries") or []) if e]
        if not entri:
            raise YouTubeError("Daftar putar ini kosong.")
        info = entri[0]

    if info.get("is_live"):
        raise YouTubeError("Siaran langsung tidak bisa ditranskrip.")

    return {
        "judul": _nama_aman(info.get("title") or ""),
        "detik": float(info.get("duration") or 0),
        "penulis": (info.get("uploader") or "").strip(),
        "situs": (info.get("extractor_key") or "").strip(),
        "url": info.get("webpage_url") or url,
    }


def unduh(url: str, tujuan: Path, lapor=None) -> Path:
    """Unduh trek audio terbaik ke `tujuan` (tanpa ekstensi) dan kembalikan
    jalur berkas sebenarnya.

    Tidak ada penyandian ulang di sini: pipeline sudah mengubah apa pun menjadi
    WAV 16 kHz untuk Whisper, jadi mengubah ke MP3 dulu hanya membuang waktu
    dan menurunkan mutu dua kali.
    """
    tujuan.parent.mkdir(parents=True, exist_ok=True)

    def kait(d):
        if not lapor or d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        if total:
            lapor(min(99, int(d.get("downloaded_bytes", 0) * 100 / total)))

    opsi = {
        **OPSI_DASAR,
        "format": "bestaudio/best",
        "outtmpl": str(tujuan) + ".%(ext)s",
        "progress_hooks": [kait],
        "retries": 3, "fragment_retries": 3,
        "logger": type("L", (), {"debug": _diam, "warning": _diam,
                                 "error": _diam})(),
    }
    try:
        with yt_dlp.YoutubeDL(opsi) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise YouTubeError(_pesan_ramah(str(exc)))

    if info.get("_type") == "playlist":
        info = [e for e in (info.get("entries") or []) if e][0]

    jalur = info.get("requested_downloads", [{}])[0].get("filepath")
    if jalur and Path(jalur).exists():
        return Path(jalur)

    for ext in EKSTENSI:                            # cadangan bila tak dilaporkan
        p = tujuan.with_name(tujuan.name + ext)
        if p.exists():
            return p
    cocok = sorted(tujuan.parent.glob(tujuan.name + ".*"))
    if cocok:
        return cocok[0]
    raise YouTubeError("Unduhan selesai tetapi berkasnya tidak ditemukan.")


def _pesan_ramah(mentah: str) -> str:
    """yt-dlp menulis galat untuk pengembang; ubah yang sering muncul jadi
    kalimat yang bisa ditindaklanjuti pengguna."""
    t = mentah.lower()
    if "sign in to confirm" in t or "not a bot" in t:
        return ("YouTube meminta pembuktian bukan bot untuk video ini. "
                "Biasanya terjadi bila server diakses dari jaringan yang sama "
                "berulang kali; coba lagi nanti.")
    if "private video" in t:
        return "Video ini privat."
    if "members-only" in t or "members only" in t:
        return "Video ini khusus anggota kanal."
    if "age" in t and "confirm" in t:
        return "Video ini dibatasi usia dan tidak bisa diambil tanpa masuk akun."
    if "removed" in t or "has been terminated" in t:
        return "Video ini sudah dihapus."
    if "unavailable in your country" in t or "geo" in t and "block" in t:
        return "Video ini dibatasi untuk wilayah ini."
    if "unavailable" in t or "not available" in t:
        # Pesan generik YouTube. Sudah dicoba dengan semua klien di KLIEN,
        # jadi jangan menebak sebabnya — tebakan "dihapus" pernah salah untuk
        # video yang sebenarnya publik dan utuh.
        return ("YouTube menolak memberikan video ini ke server "
                "(sudah dicoba dengan beberapa jenis klien). Videonya sendiri "
                "mungkin tetap bisa dibuka lewat peramban.")
    if "unsupported url" in t:
        return "Tautan ini tidak dikenali sebagai halaman video."
    # Sisanya tidak dikenali. Buang awalan teknis yt-dlp ("ERROR:", "[youtube]",
    # "[generic] id:") supaya yang tersisa setidaknya satu kalimat, bukan log.
    bersih = mentah.strip().splitlines()[0]
    bersih = re.sub(r"^ERROR:\s*", "", bersih)
    bersih = re.sub(r"^\[[^\]]+\]\s*", "", bersih)
    bersih = re.sub(r"^[\w-]{6,}:\s*", "", bersih)
    bersih = re.sub(r"[;.]\s*(Please report|Confirm you|Set --).*$", "", bersih,
                    flags=re.I)
    return bersih[:200] or "Tautan tidak bisa diambil."
