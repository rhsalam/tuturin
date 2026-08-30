"""Tanya jawab atas isi transkrip, dan saran pertanyaan pembuka.

CATATAN PRIVASI: seperti summarize.py dan tidy.py, modul ini mengirim isi
transkrip ke DeepSeek. Hanya berjalan saat pengguna menekan kirim.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from summarize import SummaryError, _panggil

# Batas konteks: cukup untuk ceramah 2 jam, masih jauh di bawah jendela model.
MAKS_KATA_KONTEKS = 14000
MAKS_RIWAYAT = 12          # pesan terakhir yang ikut dikirim
MAKS_TANYA = 2000          # batas panjang pertanyaan pengguna

SISTEM = """Anda asisten yang menjawab pertanyaan tentang SATU transkrip rekaman.

Aturan:
- Jawab HANYA dari isi transkrip. Bila jawabannya tidak ada di sana, katakan
  terus terang bahwa hal itu tidak dibahas — jangan menambah dari pengetahuan umum.
- Transkrip dihasilkan mesin, jadi ada salah dengar. Bila sebuah nama atau angka
  tampak janggal, sebutkan keraguannya.
- Sertakan penanda waktu dalam kurung siku, misalnya [00:12:34], saat merujuk
  bagian tertentu supaya pengguna bisa melompat ke sana.
- Jawab ringkas dalam Bahasa Indonesia. Pakai daftar berpoin bila membantu.
- Jangan mengarang kutipan. Kutip hanya kalimat yang benar-benar ada."""

SARAN = """Berikut transkrip sebuah rekaman. Buat TEPAT 3 pertanyaan yang
kemungkinan besar ingin ditanyakan pendengar setelah menyimaknya.

Syarat:
- Setiap pertanyaan HARUS bisa dijawab dari transkrip ini saja.
- Sebut istilah, angka, atau nama konkret yang benar-benar muncul di transkrip,
  supaya pertanyaannya terasa spesifik, bukan umum.
- Satu kalimat per pertanyaan, diakhiri tanda tanya.
- Bahasa Indonesia.
- Keluarkan JSON murni tanpa pembungkus apa pun:
  ["pertanyaan 1", "pertanyaan 2", "pertanyaan 3"]

Transkrip:
---
{teks}
---"""


def bangun_konteks(cues: list[dict], rapi: Path | None = None) -> str:
    """Susun konteks berpenanda waktu. Versi rapi dipakai bila sudah ada."""
    if rapi and rapi.exists():
        try:
            teks = rapi.read_text(encoding="utf-8")
            # buang kepala dokumen; sisakan isinya saja
            if "\n---\n" in teks:
                teks = teks.rsplit("\n---\n", 1)[1]
            if teks.strip():
                return _batasi(teks.strip())
        except OSError:
            pass

    bagian: list[str] = []
    berikutnya = 0.0
    for c in cues:
        t = (c.get("text") or "").strip()
        if not t:
            continue
        if c.get("start", 0.0) >= berikutnya:
            d = int(c["start"])
            bagian.append(f"\n[{d // 3600:02d}:{(d % 3600) // 60:02d}:{d % 60:02d}]")
            berikutnya = c["start"] + 30
        bagian.append(t)
    return _batasi(" ".join(bagian).strip())


def _batasi(teks: str) -> str:
    kata = teks.split()
    if len(kata) <= MAKS_KATA_KONTEKS:
        return teks
    return " ".join(kata[:MAKS_KATA_KONTEKS]) + "\n\n[transkrip dipotong di sini]"


def jawab(konteks: str, riwayat: list[dict], tanya: str, key: str) -> tuple[str, dict]:
    if not konteks.strip():
        raise SummaryError("Transkrip kosong, tidak ada yang bisa ditanyakan.")
    tanya = tanya.strip()[:MAKS_TANYA]
    if not tanya:
        raise SummaryError("Pertanyaan kosong.")

    pesan = [
        {"role": "system", "content": SISTEM},
        {"role": "user", "content": f"Transkrip:\n---\n{konteks}\n---"},
        {"role": "assistant", "content": "Baik, saya sudah membaca transkripnya. Silakan bertanya."},
    ]
    for m in riwayat[-MAKS_RIWAYAT:]:
        peran = "assistant" if m.get("role") == "assistant" else "user"
        isi = str(m.get("content", "")).strip()[:4000]
        if isi:
            pesan.append({"role": peran, "content": isi})
    pesan.append({"role": "user", "content": tanya})

    return _panggil(key, pesan, suhu=0.3, max_tokens=1500)


def saran(konteks: str, key: str) -> list[str]:
    if not konteks.strip():
        return []
    hasil, _ = _panggil(key, [
        {"role": "system", "content": "Anda membuat pertanyaan pembuka dari sebuah transkrip."},
        {"role": "user", "content": SARAN.format(teks=konteks)},
    ], suhu=0.5, max_tokens=500)

    hasil = re.sub(r"^```(?:json)?\s*|\s*```$", "", hasil.strip())
    try:
        data = json.loads(hasil)
        if isinstance(data, list):
            bersih = [str(x).strip() for x in data if str(x).strip()]
            return bersih[:3]
    except json.JSONDecodeError:
        pass
    # Model kadang membalas sebagai daftar biasa; ambil barisnya.
    baris = [re.sub(r'^\s*(?:[-*•]|\d+[.)])\s*|^\s*"|"\s*,?\s*$', "", b).strip()
             for b in hasil.splitlines()]
    return [b for b in baris if b.endswith("?")][:3]
