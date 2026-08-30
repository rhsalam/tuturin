"""Ringkasan transkrip lewat DeepSeek.

CATATAN PRIVASI: modul ini satu-satunya bagian aplikasi yang mengirim data
keluar dari mesin. Isi transkrip dikirim ke api.deepseek.com. Bagian lain
(transkripsi, gelombang, penyuntingan) tetap sepenuhnya lokal.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
TIMEOUT = 180

# Potong per ~5000 kata: aman jauh di bawah batas konteks, dan menjaga tiap
# permintaan tetap cepat sehingga kegagalan satu potongan tidak mahal.
CHUNK_WORDS = 5000
CHUNK_OVERLAP = 200

SISTEM = (
    "Anda asisten yang meringkas transkrip rekaman berbahasa Indonesia. "
    "Transkrip dihasilkan mesin, jadi mungkin mengandung salah dengar, "
    "pengulangan, dan kalimat terpotong — simpulkan maksudnya dari konteks "
    "dan jangan mengarang fakta yang tidak ada di transkrip. "
    "Jawab dalam Bahasa Indonesia dengan Markdown."
)

TEMPLATE = """Ringkas transkrip berikut dengan struktur persis seperti ini:

## Hasil Utama

### Topik Utama
Satu paragraf padat: apa yang dibahas dan untuk siapa.

### Argumen dan Poin Penting
Daftar berpoin. Setiap poin memuat klaim beserta alasan atau contoh yang
dipakai pembicara. Sertakan angka, nama, dan istilah yang disebut.

### Struktur Presentasi
Urutan pembahasan dari awal ke akhir, ringkas per tahap.

Aturan:
- Hanya gunakan isi transkrip. Jika sesuatu tidak jelas, tulis apa adanya.
- Jangan menyalin kalimat panjang mentah-mentah; rumuskan ulang.
- Nama diri yang tampak salah tulis boleh Anda tandai dengan "(?)".

Transkrip:
---
{teks}
---"""

GABUNG = """Berikut ringkasan beberapa bagian berurutan dari satu rekaman.
Gabungkan menjadi SATU ringkasan utuh dengan struktur persis:

## Hasil Utama

### Topik Utama
### Argumen dan Poin Penting
### Struktur Presentasi

Hilangkan pengulangan antar bagian, pertahankan angka dan nama.

Ringkasan bagian:
---
{teks}
---"""


class SummaryError(RuntimeError):
    pass


def load_api_key(base: Path) -> str:
    """Ambil kunci dari lingkungan, lalu dari .env. Kunci tidak pernah dicatat."""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    env = base / ".env"
    if env.exists():
        for baris in env.read_text(encoding="utf-8").splitlines():
            baris = baris.strip()
            if baris.startswith("#") or "=" not in baris:
                continue
            nama, _, nilai = baris.partition("=")
            if nama.strip() == "DEEPSEEK_API_KEY":
                return nilai.strip().strip("'\"")
    raise SummaryError(
        "Kunci DeepSeek belum diatur. Isi DEEPSEEK_API_KEY di berkas .env "
        "atau di variabel lingkungan.")


def _panggil(key: str, pesan: list[dict], suhu: float = 0.3,
             max_tokens: int = 3000) -> tuple[str, dict]:
    body = json.dumps({
        "model": MODEL, "messages": pesan,
        "temperature": suhu, "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
    )
    galat_terakhir = ""
    for percobaan in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                d = json.loads(resp.read().decode("utf-8"))
            return d["choices"][0]["message"]["content"].strip(), d.get("usage", {})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code in (401, 403):
                raise SummaryError("Kunci DeepSeek ditolak (401/403). Periksa kunci Anda.")
            if exc.code == 402:
                raise SummaryError("Saldo DeepSeek tidak cukup (402).")
            galat_terakhir = f"HTTP {exc.code}: {detail}"
            if exc.code not in (429, 500, 502, 503, 504):
                raise SummaryError(galat_terakhir)
        except (urllib.error.URLError, TimeoutError) as exc:
            galat_terakhir = f"koneksi gagal: {exc}"
        except (KeyError, json.JSONDecodeError) as exc:
            raise SummaryError(f"balasan DeepSeek tidak dikenali: {exc}")
        time.sleep(2 * (percobaan + 1))
    raise SummaryError(f"gagal setelah 3 percobaan — {galat_terakhir}")


def potong(teks: str) -> list[str]:
    kata = teks.split()
    if len(kata) <= CHUNK_WORDS:
        return [teks]
    bagian, i = [], 0
    while i < len(kata):
        bagian.append(" ".join(kata[i:i + CHUNK_WORDS]))
        i += CHUNK_WORDS - CHUNK_OVERLAP
    return bagian


def ringkas(teks: str, key: str, lapor=None) -> tuple[str, dict]:
    """Kembalikan (markdown, statistik). Rekaman panjang diringkas bertahap."""
    teks = re.sub(r"\n{3,}", "\n\n", teks).strip()
    if not teks:
        raise SummaryError("Transkrip kosong, tidak ada yang bisa diringkas.")

    bagian = potong(teks)
    pakai = {"prompt_tokens": 0, "completion_tokens": 0}

    def catat(u):
        pakai["prompt_tokens"] += u.get("prompt_tokens", 0)
        pakai["completion_tokens"] += u.get("completion_tokens", 0)

    if len(bagian) == 1:
        if lapor: lapor("meringkas")
        hasil, u = _panggil(key, [
            {"role": "system", "content": SISTEM},
            {"role": "user", "content": TEMPLATE.format(teks=bagian[0])}])
        catat(u)
    else:
        antara = []
        for i, b in enumerate(bagian, 1):
            if lapor: lapor(f"bagian {i}/{len(bagian)}")
            h, u = _panggil(key, [
                {"role": "system", "content": SISTEM},
                {"role": "user", "content": TEMPLATE.format(teks=b)}])
            catat(u)
            antara.append(f"### Bagian {i}\n{h}")
        if lapor: lapor("menggabungkan")
        hasil, u = _panggil(key, [
            {"role": "system", "content": SISTEM},
            {"role": "user", "content": GABUNG.format(teks="\n\n".join(antara))}])
        catat(u)

    pakai["chunks"] = len(bagian)
    pakai["words"] = len(teks.split())
    return hasil, pakai
