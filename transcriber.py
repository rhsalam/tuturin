"""Mesin transkripsi: antrean job, konversi audio, dan pemanggilan whisper.cpp."""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

MODEL_DIR = Path.home() / ".local/share/whisper-models"
MODEL_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

MODELS = ["large-v3-turbo", "medium", "small", "base", "tiny"]
FORMATS = {"txt": "-otxt", "srt": "-osrt", "vtt": "-ovtt", "json": "-oj"}

# Frasa yang rutin dihalusinasikan Whisper di ujung rekaman: kredit subtitle dan
# sapaan penutup video yang terserap dari data latih.
#
# Pola diikat ke awal DAN akhir baris: seluruh baris harus berupa frasa itu saja.
# Mencocokkan awalan saja berbahaya — "Selamat menikmati nonton bareng, terima
# kasih, assalamualaikum" adalah kalimat penutup yang sah dan tidak boleh dibuang.
HALLUCINATION_TAIL = re.compile(
    r"^\s*(?:"
    r"terima kasih (?:telah |sudah |)menonton"
    r"|selamat menikmati"
    r"|sampai jumpa di video[^\n]{0,20}"
    r"|jangan lupa (?:like|subscribe|komen)[^\n]{0,30}"
    r"|(?:like|share|subscribe)(?:[ ,]+(?:like|share|subscribe|dan))*"
    r"|(?:sub\s*indo|subtitle[s]?|takarir|translated|transcribed)\s+by\s+\S[^\n]{0,40}"
    r"|amara\.org[^\n]{0,30}"
    r")\s*[.!?…]*\s*$",
    re.IGNORECASE,
)

# Batas panjang: halusinasi selalu pendek. Baris panjang yang kebetulan diawali
# frasa serupa hampir pasti ucapan sungguhan.
HALLUCINATION_MAX_CHARS = 60

PROGRESS_RE = re.compile(r"progress\s*=\s*(\d+)%")
SEGMENT_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\.\d+\s*-->.*?\]\s*(.*)$")
DETECT_RE = re.compile(r"auto-detected language:\s*([a-z]{2,3})")


def out_path(prefix: Path, ext: str) -> Path:
    """Tempelkan ekstensi ke prefix apa adanya.

    Sengaja tidak memakai Path.with_suffix(): whisper-cli menempelkan ".txt" ke
    akhir prefix, sedangkan with_suffix() MENGGANTI segmen setelah titik
    terakhir. Untuk nama seperti "lagu_(mp3.pm)" segmen ".pm)" akan terpotong
    dan hasilnya nama yang tidak pernah ditulis whisper.
    """
    return prefix.parent / f"{prefix.name}.{ext}"


class TranscriberError(RuntimeError):
    pass


@dataclass
class Job:
    id: str
    filename: str
    source: Path
    lang: str
    model: str
    formats: list[str]
    status: str = "queued"          # queued|downloading|converting|detecting|transcribing|done|error
    progress: int = 0
    duration: float = 0.0
    detected_lang: str = ""
    error: str = ""
    outputs: dict[str, str] = field(default_factory=dict)
    preview: list[str] = field(default_factory=list)
    removed_tail: str = ""
    folder: str = ""            # label pengelompokan; bukan jalur berkas
    judul: str = ""             # nama tampilan; kosong = pakai nama berkas asli
    url: str = ""               # asal tautan; kosong = berkas unggahan biasa
    created: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    STATUS_LABEL = {
        "queued": "Menunggu antrean",
        "downloading": "Mengunduh video",
        "converting": "Mengonversi audio",
        "detecting": "Mendeteksi bahasa",
        "transcribing": "Transkripsi",
        "done": "Selesai",
        "error": "Gagal",
    }

    def to_dict(self) -> dict:
        with self._lock:
            elapsed = (self.finished or time.time()) - self.started if self.started else 0
            speed = (self.duration / elapsed) if elapsed > 0 and self.duration else 0
            return {
                "id": self.id,
                "filename": self.filename,
                "status": self.status,
                "status_label": self.STATUS_LABEL.get(self.status, self.status),
                "progress": self.progress,
                "duration": round(self.duration, 1),
                "lang": self.detected_lang or self.lang,
                "model": self.model,
                "formats": self.formats,
                "error": self.error,
                "outputs": list(self.outputs),
                "preview": self.preview[-6:],
                "removed_tail": self.removed_tail,
                "folder": self.folder,
                "judul": self.judul or self.filename,
                "url": self.url,
                "elapsed": round(elapsed, 1) if self.started else 0,
                "speed": round(speed, 1) if self.status == "done" else 0,
                "created": self.created,
            }

    def persist(self, outdir: Path) -> None:
        """Simpan metadata job agar bertahan setelah server dimulai ulang."""
        d = self.to_dict()
        d["source"] = str(self.source)
        d["outputs_map"] = dict(self.outputs)
        d["judul"] = self.judul
        d["url"] = self.url
        d["started"] = self.started
        d["finished"] = self.finished
        try:
            (outdir / self.id).mkdir(parents=True, exist_ok=True)
            (outdir / self.id / "job.json").write_text(
                json.dumps(d, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # penyimpanan riwayat bukan hal kritis — job tetap sah

    @classmethod
    def restore(cls, d: dict) -> "Job | None":
        try:
            job = cls(id=d["id"], filename=d["filename"], source=Path(d["source"]),
                      lang=d.get("lang", "auto"), model=d.get("model", ""),
                      formats=list(d.get("formats", [])))
        except (KeyError, TypeError):
            return None
        job.status = d.get("status", "done")
        job.progress = d.get("progress", 100)
        job.duration = d.get("duration", 0.0)
        job.detected_lang = d.get("lang", "")
        job.outputs = {k: v for k, v in d.get("outputs_map", {}).items()
                       if Path(v).exists()}
        job.removed_tail = d.get("removed_tail", "")
        job.folder = d.get("folder", "")
        job.judul = d.get("judul", "")
        job.url = d.get("url", "")
        job.created = d.get("created", time.time())
        job.started = d.get("started", 0.0)
        job.finished = d.get("finished", 0.0)
        return job if job.outputs else None

    def set(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def add_preview(self, line: str) -> None:
        with self._lock:
            self.preview.append(line)
            if len(self.preview) > 40:
                del self.preview[:-40]


def require_binaries() -> None:
    for exe, pkg in (("ffmpeg", "ffmpeg"), ("ffprobe", "ffmpeg"), ("whisper-cli", "whisper-cpp")):
        if not shutil.which(exe):
            raise TranscriberError(f"'{exe}' tidak ditemukan. Pasang dengan: brew install {pkg}")


def model_path(name: str) -> Path:
    return MODEL_DIR / f"ggml-{name}.bin"


def available_models() -> list[dict]:
    return [
        {"name": m, "installed": model_path(m).exists(),
         "size": _human(model_path(m).stat().st_size) if model_path(m).exists() else ""}
        for m in MODELS
    ]


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit != "GB" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def ensure_model(name: str, on_status=None) -> Path:
    path = model_path(name)
    if path.exists():
        return path
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    if on_status:
        on_status(f"Mengunduh model {name}")
    tmp = path.with_suffix(".part")
    try:
        subprocess.run(
            ["curl", "-fL", "--silent", "--show-error", "-o", str(tmp),
             f"{MODEL_BASE_URL}/ggml-{name}.bin"],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        tmp.unlink(missing_ok=True)
        raise TranscriberError(f"gagal mengunduh model '{name}': {exc.stderr.strip()}") from exc
    tmp.rename(path)
    return path


def probe_duration(src: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(src)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return _durasi_dari_paket(src)


def _durasi_dari_paket(src: Path) -> float:
    """Cadangan untuk berkas tanpa durasi di headernya.

    MediaRecorder menulis WebM secara mengalir: saat header dibuat, panjang
    rekaman belum diketahui, dan berkas yang sedang mengalir tidak bisa
    diputar balik untuk menambalnya. Jadi seluruh hasil rekaman langsung
    (mikrofon maupun audio tab) selalu ber-"duration: N/A" di header.

    Jalan keluarnya membaca stempel waktu paket terakhir. Dengan -c copy
    tidak ada penyandian ulang, jadi ongkosnya sepersepuluh detik walau
    berkasnya puluhan MB.
    """
    res = subprocess.run(
        ["ffmpeg", "-v", "error", "-stats", "-i", str(src), "-c", "copy",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    cocok = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
    if not cocok:
        return 0.0
    j, m, d = cocok[-1]
    return int(j) * 3600 + int(m) * 60 + float(d)


def to_wav(src: Path, dest: Path) -> None:
    """Whisper butuh WAV mono 16 kHz. -vn membuang trek video agar file MP4 ikut jalan."""
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dest)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise TranscriberError(f"konversi audio gagal: {res.stderr.strip()[:300]}")


def detect_language(wav: Path, model: Path, threads: int, duration: float) -> str:
    """Deteksi dari cuplikan di tengah rekaman, bukan 30 detik pertama.

    Rekaman seminar sering diawali musik intro dan sapaan, yang membuat
    deteksi dari awal file meleset.
    """
    offset = int(duration / 5) if duration > 60 else 0
    with tempfile.TemporaryDirectory() as td:
        sample = Path(td) / "sample.wav"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(offset), "-t", "40", "-i", str(wav), str(sample)],
            capture_output=True,
        )
        if not sample.exists():
            return "auto"
        res = subprocess.run(
            ["whisper-cli", "-m", str(model), "-f", str(sample),
             "-l", "auto", "-t", str(threads), "-nt"],
            capture_output=True, text=True,
        )
    m = DETECT_RE.search(res.stdout + res.stderr)
    return m.group(1) if m else "auto"


def strip_hallucinated_tail(prefix: Path, formats: list[str]) -> str:
    """Buang segmen terakhir bila cocok pola kredit subtitle. Mengembalikan teks yang dibuang."""
    txt = out_path(prefix, "txt")
    removed = ""
    if "txt" in formats and txt.exists():
        lines = txt.read_text(encoding="utf-8").rstrip("\n").split("\n")
        last = lines[-1].strip()
        if lines and len(last) <= HALLUCINATION_MAX_CHARS and HALLUCINATION_TAIL.match(last):
            removed = lines[-1].strip()
            txt.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    if not removed:
        return ""
    for ext in ("srt", "vtt"):
        if ext not in formats:
            continue
        f = out_path(prefix, ext)
        if not f.exists():
            continue
        blocks = f.read_text(encoding="utf-8").rstrip("\n").split("\n\n")
        if blocks and removed.lower() in blocks[-1].lower():
            f.write_text("\n\n".join(blocks[:-1]) + "\n", encoding="utf-8")
    return removed


class Engine:
    """Antrean satu-pekerja. Whisper memakai seluruh core, jadi job diproses berurutan."""

    def __init__(self, outdir: Path, threads: int | None = None):
        self.outdir = outdir
        self.threads = threads or self._perf_cores()
        self.jobs: dict[str, Job] = {}
        self._q: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._load_history()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _load_history(self) -> None:
        """Pulihkan job selesai dari disk supaya editor tetap bisa dibuka."""
        for meta in sorted(self.outdir.glob("*/job.json")):
            try:
                job = Job.restore(json.loads(meta.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
            if job is not None:
                self.jobs[job.id] = job

    @staticmethod
    def _perf_cores() -> int:
        for key in ("hw.perflevel0.physicalcpu", "hw.ncpu"):
            try:
                out = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True)
                if out.returncode == 0 and out.stdout.strip():
                    return int(out.stdout.strip())
            except Exception:
                pass
        return os.cpu_count() or 4

    def submit(self, source: Path, filename: str, lang: str, model: str,
               formats: list[str], folder: str = "") -> Job:
        job = Job(id=uuid.uuid4().hex[:12], filename=filename, source=source,
                  lang=lang, model=model, formats=formats, folder=folder)
        job.duration = probe_duration(source)
        with self._lock:
            self.jobs[job.id] = job
        self._q.put(job.id)
        return job

    def submit_url(self, url: str, filename: str, judul: str, detik: float,
                   lang: str, model: str, formats: list[str],
                   folder: str = "", tujuan: Path | None = None) -> Job:
        """Seperti submit(), tetapi berkasnya belum ada — baru diambil saat
        pekerja menjalankannya. `tujuan` adalah jalur tanpa ekstensi; ekstensi
        sebenarnya baru diketahui setelah yt-dlp memilih formatnya."""
        job = Job(id=uuid.uuid4().hex[:12], filename=filename,
                  source=tujuan or Path(filename), lang=lang, model=model,
                  formats=formats, folder=folder, judul=judul, url=url)
        job.duration = detik
        with self._lock:
            self.jobs[job.id] = job
        self._q.put(job.id)
        return job

    def list_jobs(self) -> list[dict]:
        with self._lock:
            jobs = list(self.jobs.values())
        return [j.to_dict() for j in sorted(jobs, key=lambda j: j.created, reverse=True)]

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self.jobs.get(job_id)

    def set_folder(self, job_id: str, folder: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        job.set(folder=folder)
        job.persist(self.outdir)      # tulis ulang agar bertahan setelah restart
        return True

    def set_judul(self, job_id: str, judul: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        job.set(judul=judul)
        job.persist(self.outdir)
        return True

    def set_folder_banyak(self, ids: list[str], folder: str) -> int:
        n = 0
        for jid in ids:
            if self.set_folder(jid, folder):
                n += 1
        return n

    def ganti_nama_folder(self, lama: str, baru: str) -> int:
        """Ubah label pada semua job yang memakainya. Tidak menyentuh disk."""
        n = 0
        with self._lock:
            kena = [j for j in self.jobs.values() if j.folder == lama]
        for j in kena:
            j.set(folder=baru)
            j.persist(self.outdir)
            n += 1
        return n

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None or job.status in ("queued", "downloading", "converting",
                                             "detecting", "transcribing"):
                return False
            del self.jobs[job_id]
        shutil.rmtree(self.outdir / job_id, ignore_errors=True)
        job.source.unlink(missing_ok=True)
        return True

    def _run(self) -> None:
        while True:
            job_id = self._q.get()
            job = self.get(job_id)
            if job is None:
                continue
            try:
                self._process(job)
            except TranscriberError as exc:
                job.set(status="error", error=str(exc), finished=time.time())
            except Exception as exc:  # pragma: no cover
                job.set(status="error", error=f"kesalahan tak terduga: {exc}", finished=time.time())
            finally:
                self._q.task_done()

    def _unduh_tautan(self, job: Job) -> None:
        """Ambil medianya lebih dulu. Dijalankan di dalam pekerja, bukan di
        dalam permintaan HTTP, supaya halaman tidak menggantung menunggu
        video satu jam dan supaya unduhan ikut antre seperti tugas lain."""
        import youtube                       # diimpor di sini agar aplikasi
                                             # tetap jalan bila yt-dlp absen
        job.set(status="downloading", progress=0)
        try:
            berkas = youtube.unduh(job.url, job.source,
                                   lapor=lambda n: job.set(progress=n))
        except youtube.YouTubeError as exc:
            # Galat unduhan sudah berupa kalimat untuk pengguna. Dialihkan ke
            # TranscriberError supaya ditangani jalur galat yang sama dengan
            # kegagalan ffmpeg dan whisper, bukan jadi "kesalahan tak terduga".
            raise TranscriberError(str(exc)) from exc
        job.set(source=berkas, progress=0)
        if not job.duration:                 # cadangan bila metadata tak punya
            job.set(duration=probe_duration(berkas))

    def _process(self, job: Job) -> None:
        job.set(started=time.time())
        if job.url and not job.source.exists():
            self._unduh_tautan(job)
        model = ensure_model(job.model, on_status=lambda s: job.set(status="converting"))

        dest = self.outdir / job.id
        dest.mkdir(parents=True, exist_ok=True)
        stem = Path(job.filename).stem
        prefix = dest / stem

        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "audio.wav"
            job.set(status="converting", progress=0)
            to_wav(job.source, wav)

            lang = job.lang
            if lang == "detect":
                job.set(status="detecting")
                lang = detect_language(wav, model, self.threads, job.duration)
                job.set(detected_lang=lang)

            job.set(status="transcribing", progress=0)
            cmd = ["whisper-cli", "-m", str(model), "-f", str(wav),
                   "-l", lang, "-t", str(self.threads), "-pp",
                   "-of", str(prefix)]
            for fmt in job.formats:
                cmd.append(FORMATS[fmt])

            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1)
            tail: list[str] = []
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip()
                tail.append(line)
                if len(tail) > 30:
                    del tail[:-30]
                if (m := PROGRESS_RE.search(line)):
                    job.set(progress=min(int(m.group(1)), 99))
                elif (m := SEGMENT_RE.match(line)):
                    text = m.group(2).strip()
                    if text:
                        job.add_preview(f"[{m.group(1)}] {text}")
            if proc.wait() != 0:
                raise TranscriberError("whisper-cli gagal:\n" + "\n".join(tail[-12:]))

        removed = strip_hallucinated_tail(prefix, job.formats)
        outputs = {fmt: str(out_path(prefix, fmt))
                   for fmt in job.formats if out_path(prefix, fmt).exists()}
        if not outputs:
            raise TranscriberError("whisper selesai tetapi tidak ada file output yang dihasilkan")
        job.set(status="done", progress=100, outputs=outputs,
                removed_tail=removed, finished=time.time())
        job.persist(self.outdir)
