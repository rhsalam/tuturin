"""Baca-tulis cue SRT dan hitung data waveform untuk editor validasi."""
from __future__ import annotations

import array
import json
import re
import subprocess
from pathlib import Path

TIME_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})")
ARROW_RE = re.compile(r"-->")

# Laju cuplik rendah: cukup untuk gambar gelombang, murah untuk diproses.
PEAK_RATE = 1000
PEAK_BUCKETS = 4000


def parse_time(s: str) -> float:
    m = TIME_RE.search(s)
    if not m:
        raise ValueError(f"stempel waktu tidak dikenali: {s!r}")
    h, mnt, sec, ms = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + int(sec) + int(ms.ljust(3, "0")) / 1000


def format_time(t: float) -> str:
    if t < 0:
        t = 0.0
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def read_srt(path: Path) -> list[dict]:
    """Kembalikan [{start, end, text}] dari berkas SRT."""
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
    if not raw:
        return []
    cues: list[dict] = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [l for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        idx = next((i for i, l in enumerate(lines) if ARROW_RE.search(l)), None)
        if idx is None:
            continue
        try:
            left, right = lines[idx].split("-->")
            start, end = parse_time(left), parse_time(right)
        except ValueError:
            continue
        text = " ".join(l.strip() for l in lines[idx + 1:]).strip()
        cues.append({"start": start, "end": end, "text": text})
    return cues


def write_srt(path: Path, cues: list[dict]) -> None:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{format_time(c['start'])} --> {format_time(c['end'])}\n{c['text']}\n")
    path.write_text("\n".join(out), encoding="utf-8")


def write_txt(path: Path, cues: list[dict]) -> None:
    lines = [c["text"].strip() for c in cues if c["text"].strip()]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def write_vtt(path: Path, cues: list[dict]) -> None:
    out = ["WEBVTT\n"]
    for c in cues:
        a = format_time(c["start"]).replace(",", ".")
        b = format_time(c["end"]).replace(",", ".")
        out.append(f"{a} --> {b}\n{c['text']}\n")
    path.write_text("\n".join(out), encoding="utf-8")


def compute_peaks(src: Path, cache: Path, duration: float) -> dict:
    """Hitung amplitudo puncak per petak untuk gambar gelombang.

    Hasil disimpan agar tidak dihitung ulang setiap halaman dibuka.
    """
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass  # berkas cache rusak — hitung ulang

    proc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-vn", "-ac", "1", "-ar", str(PEAK_RATE), "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None
    samples = array.array("h")
    try:
        samples.frombytes(proc.stdout.read())
    except ValueError:
        pass  # jumlah byte ganjil di ujung aliran
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if proc.wait() != 0 and not samples:
        raise RuntimeError(f"ffmpeg gagal membaca audio: {err.strip()[:200]}")

    total = len(samples)
    if total == 0:
        data = {"peaks": [], "duration": duration}
    else:
        buckets = min(PEAK_BUCKETS, total)
        size = max(1, total // buckets)
        peaks = []
        for i in range(0, total, size):
            chunk = samples[i:i + size]
            hi = max(chunk); lo = min(chunk)
            peaks.append(round(max(abs(hi), abs(lo)) / 32768, 4))
        data = {"peaks": peaks, "duration": duration or total / PEAK_RATE}

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data
