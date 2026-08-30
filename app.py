"""Web UI transkripsi audio/video — lokal, offline, di atas whisper.cpp."""
from __future__ import annotations

import hmac
import io
import json
import os
import re
import threading
import time
import unicodedata
import zipfile
from datetime import timedelta
from pathlib import Path

from flask import (Flask, abort, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

import chat as chatlib
import konfig
import perangkat
import cues as cuelib
import folders as folderlib
import summarize
import tidy as tidylib
from transcriber import (FORMATS, Engine, TranscriberError, available_models,
                         require_binaries)

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"
UPLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)

# Ekstensi yang bisa dibaca ffmpeg. Video ikut diterima — trek videonya dibuang.
ALLOWED = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
           ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".mpeg", ".mpg"}
MAX_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES
app.secret_key = konfig.pastikan_rahasia(BASE)

# Di belakang Caddy, tanpa ini setiap permintaan tampak datang dari 127.0.0.1
# sehingga pembatas percobaan mengunci semua orang sekaligus, dan url_for
# menghasilkan http:// meski peramban memakai https://.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Cookie bertanda Secure tidak pernah dikirim lewat HTTP polos, jadi ini hanya
# dinyalakan saat memang dilayani lewat HTTPS.
COOKIE_AMAN = konfig.ambil(BASE, "SECURE_COOKIE") == "1"

app.config.update(
    # Tanpa ini Jinja menyimpan templat di memori saat debug mati, sehingga
    # perubahan pada berkas .html baru terlihat setelah server dimulai ulang —
    # sumber kebingungan yang sudah dua kali terjadi. Biayanya sekadar satu
    # pemeriksaan berkas per render.
    TEMPLATES_AUTO_RELOAD=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=COOKIE_AMAN,
    # Eksplisit, agar tidak bergantung pada bawaan Flask yang implisit (31 hari).
    # Sesi pendek tidak menyusahkan karena token perangkat yang menanggung
    # kenyamanan jangka panjang.
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)

# Kata sandi opsional. Kosong = tanpa gerbang (hanya aman untuk 127.0.0.1).
SANDI = konfig.ambil(BASE, "APP_PASSWORD")

@app.template_filter("tanggal")
def _fmt_tanggal(ts) -> str:
    """Waktu Unix -> tanggal singkat berbahasa Indonesia."""
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return "-"
    if ts <= 0:
        return "-"
    BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
             "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    t = time.localtime(ts)
    return f"{t.tm_mday} {BULAN[t.tm_mon - 1]} {t.tm_year}, {t.tm_hour:02d}.{t.tm_min:02d}"


# ---------------------------------------------------- pembatasan percobaan
# Dalam memori saja: cukup untuk satu proses, dan lupa sendiri saat restart.
GAGAL_MAKS = 5
KUNCI_AWAL = 300          # 5 menit
KUNCI_MAKS = 3600         # 1 jam
_percobaan: dict[str, list] = {}      # ip -> [jumlah_gagal, terkunci_sampai]
_percobaan_lock = threading.Lock()


def _sisa_kunci(ip: str) -> int:
    with _percobaan_lock:
        d = _percobaan.get(ip)
        if not d:
            return 0
        return max(0, int(d[1] - time.time()))


def _catat_gagal(ip: str) -> None:
    with _percobaan_lock:
        d = _percobaan.setdefault(ip, [0, 0.0])
        d[0] += 1
        if d[0] >= GAGAL_MAKS:
            # Tiap kegagalan sesudah ambang menggandakan durasi kunci.
            lipat = d[0] - GAGAL_MAKS
            d[1] = time.time() + min(KUNCI_AWAL * (2 ** lipat), KUNCI_MAKS)


def _bersihkan_gagal(ip: str) -> None:
    with _percobaan_lock:
        _percobaan.pop(ip, None)


def _tujuan_aman(lanjut: str) -> str:
    """Cegah redirect terbuka.

    Memeriksa hanya awalan "/" tidak cukup: "//situs-lain.com" juga diawali "/"
    tetapi diperlakukan peramban sebagai URL protocol-relative ke host lain.
    """
    if not lanjut.startswith("/"):
        return url_for("index")
    if lanjut.startswith("//") or lanjut.startswith("/\\"):
        return url_for("index")
    return lanjut


@app.before_request
def _gerbang():
    """Tutup seluruh rute bila APP_PASSWORD diatur.

    Diterima bila ada sesi valid ATAU token perangkat valid. Token perangkat
    itulah yang membuat sandi cukup diketik sekali per perangkat.
    """
    if not SANDI or session.get("masuk"):
        return None
    if request.endpoint in ("login", "static"):
        return None

    token = request.cookies.get(perangkat.NAMA_COOKIE, "")
    sidik = perangkat.periksa(BASE, token) if token else None
    if sidik:
        # Isi sesi agar permintaan berikutnya tidak perlu membaca berkas.
        session["masuk"] = True
        session["perangkat"] = sidik
        session.permanent = True
        return None

    if request.path.startswith("/api/"):
        return jsonify(error="Perlu masuk."), 401
    return redirect(url_for("login", next=request.full_path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not SANDI:
        return redirect(url_for("index"))

    ip = request.remote_addr or "?"
    galat = ""
    sisa = _sisa_kunci(ip)

    if request.method == "POST":
        if sisa:
            return render_template("login.html", galat="", kunci=sisa), 429
        if hmac.compare_digest(request.form.get("sandi", ""), SANDI):
            _bersihkan_gagal(ip)
            session["masuk"] = True
            session.permanent = True
            resp = redirect(_tujuan_aman(request.args.get("next", "")))
            if request.form.get("ingat"):
                token = perangkat.terbitkan(BASE, request.headers.get("User-Agent", ""))
                session["perangkat"] = perangkat.periksa(BASE, token)
                resp.set_cookie(
                    perangkat.NAMA_COOKIE, token,
                    max_age=perangkat.UMUR_DETIK, httponly=True,
                    samesite="Lax", secure=COOKIE_AMAN,
                )
            return resp
        _catat_gagal(ip)
        sisa = _sisa_kunci(ip)
        galat = "" if sisa else "Kata sandi salah."

    kode = 429 if sisa else (401 if galat else 200)
    return render_template("login.html", galat=galat, kunci=sisa), kode


@app.post("/logout")
def logout():
    sidik = session.get("perangkat")
    if sidik:
        perangkat.cabut(BASE, sidik)     # keluar berarti lupakan perangkat ini
    session.clear()
    resp = redirect(url_for("login"))
    resp.delete_cookie(perangkat.NAMA_COOKIE)
    return resp


@app.get("/perangkat")
def halaman_perangkat():
    if not SANDI:
        return redirect(url_for("index"))
    kini = session.get("perangkat")
    return render_template("perangkat.html",
                           daftar=perangkat.daftar(BASE, kini),
                           ada_kini=bool(kini))


@app.post("/perangkat/cabut")
def cabut_perangkat():
    if not SANDI:
        abort(404)
    sidik = request.form.get("sidik", "")
    sendiri = sidik and sidik == session.get("perangkat")
    perangkat.cabut(BASE, sidik)
    if sendiri:
        session.clear()
        resp = redirect(url_for("login"))
        resp.delete_cookie(perangkat.NAMA_COOKIE)
        return resp
    return redirect(url_for("halaman_perangkat"))


@app.post("/perangkat/cabut-lain")
def cabut_perangkat_lain():
    if not SANDI:
        abort(404)
    perangkat.cabut_semua(BASE, kecuali=session.get("perangkat"))
    return redirect(url_for("halaman_perangkat"))


engine = Engine(OUTPUTS)


MAKS_FOLDER = 40


def rapikan_folder(nama: str) -> str:
    """Folder di sini hanya LABEL, bukan jalur berkas — tidak pernah dipakai
    untuk membuat direktori. Jadi cukup dirapikan agar seragam."""
    nama = re.sub(r"\s+", " ", str(nama or "")).strip()
    return nama[:MAKS_FOLDER]


def safe_name(name: str) -> str:
    """Pertahankan huruf non-ASCII (nama file Indonesia) tapi buang komponen path."""
    name = Path(name).name
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"[\x00-\x1f/\\]", "", name).strip(" .")
    return name[:180] or "audio"


@app.get("/")
def index():
    return render_template("index.html", models=available_models(),
                           formats=list(FORMATS), threads=engine.threads)


@app.get("/api/jobs")
def list_jobs():
    """Sertakan status keluaran AI agar kartu tidak perlu memanggil satu per satu."""
    keluar = []
    for j in engine.list_jobs():
        d = OUTPUTS / j["id"]
        j["ai"] = {
            "ringkasan": (d / "summary.md").exists(),
            "rapi": (d / "rapi.md").exists(),
            "rapi_docx": (d / "rapi.docx").exists(),
        }
        keluar.append(j)
    return jsonify(keluar)


def _pekerjaan_dari_tautan(tautan: str, lang: str, model: str,
                           formats: list[str], folder: str) -> tuple[dict | None, str | None]:
    """Buat satu pekerjaan dari tautan video; kembalikan (hasil, galat).

    Metadata diambil di sini (±1-2 detik) supaya pustaka langsung menampilkan
    judul dan durasi yang benar. Medianya sendiri baru diunduh oleh pekerja,
    karena video satu jam tidak boleh menahan permintaan HTTP.
    """
    try:
        import youtube
    except ImportError:
        return None, "yt-dlp belum terpasang di server ini."

    try:
        bersih = youtube.periksa_url(tautan)
        info = youtube.metadata(bersih)
    except youtube.YouTubeError as exc:
        return None, str(exc)

    nama = safe_name(info["judul"])
    dasar = UPLOADS / f"yt_{nama}"
    i = 0
    while sorted(dasar.parent.glob(dasar.name + ".*")):
        i += 1
        dasar = UPLOADS / f"yt_{i}_{nama}"

    job = engine.submit_url(
        url=info["url"], filename=nama, judul=nama, detik=info["detik"],
        lang=lang, model=model, formats=formats, folder=folder, tujuan=dasar)
    return job.to_dict(), None


@app.post("/api/jobs")
def create_job():
    files = [f for f in request.files.getlist("file") if f and f.filename]
    tautan = (request.form.get("url") or "").strip()
    if not files and not tautan:
        return jsonify(error="Tidak ada berkas maupun tautan."), 400

    lang = (request.form.get("lang") or "detect").strip()
    model = request.form.get("model") or "large-v3-turbo"
    formats = [f for f in request.form.getlist("formats") if f in FORMATS] or ["txt", "srt"]
    folder = rapikan_folder(request.form.get("folder", ""))

    if model not in {m["name"] for m in available_models()}:
        return jsonify(error=f"Model tidak dikenal: {model}"), 400
    if lang != "detect" and not re.fullmatch(r"[a-z]{2,3}", lang):
        return jsonify(error="Kode bahasa harus 2-3 huruf kecil, misal 'id' atau 'en'."), 400

    created = []
    if tautan:
        hasil, galat = _pekerjaan_dari_tautan(tautan, lang, model, formats, folder)
        if galat:
            return jsonify(error=galat), 400
        created.append(hasil)

    for n, storage in enumerate(files):
        name = safe_name(storage.filename)
        if Path(name).suffix.lower() not in ALLOWED:
            return jsonify(error=f"Format tidak didukung: {name}"), 400
        dest = UPLOADS / f"{n}_{name}"
        i = 0
        while dest.exists():
            i += 1
            dest = UPLOADS / f"{n}_{i}_{name}"
        storage.save(dest)
        if dest.stat().st_size == 0:
            dest.unlink(missing_ok=True)
            return jsonify(error=f"File kosong: {name}"), 400
        created.append(engine.submit(dest, name, lang, model, formats, folder).to_dict())
    return jsonify(created), 201


@app.get("/api/jobs/<job_id>")
def job_status(job_id: str):
    job = engine.get(job_id)
    if job is None:
        abort(404)
    return jsonify(job.to_dict())


@app.get("/api/jobs/<job_id>/text/<fmt>")
def job_text(job_id: str, fmt: str):
    job = engine.get(job_id)
    if job is None or fmt not in job.outputs:
        abort(404)
    return Path(job.outputs[fmt]).read_text(encoding="utf-8"), 200, \
        {"Content-Type": "text/plain; charset=utf-8"}


@app.get("/api/jobs/<job_id>/download/<fmt>")
def download(job_id: str, fmt: str):
    job = engine.get(job_id)
    if job is None or fmt not in job.outputs:
        abort(404)
    path = Path(job.outputs[fmt])
    return send_file(path, as_attachment=True, download_name=path.name)


@app.get("/api/jobs/unduh-zip")
def unduh_banyak():
    """Beberapa transkrip sekaligus, dibungkus jadi satu ZIP.

    Sebelumnya klien memanggil window.open() sekali per berkas. Peramban hanya
    memperlakukan pop-up pertama sebagai hasil klik pengguna dan memblokir
    sisanya, sehingga dari tujuh pilihan hanya satu yang benar-benar terunduh.
    Satu permintaan yang mengembalikan satu berkas menghindari pemblokir
    pop-up sekaligus dialog "unduh banyak berkas".
    """
    ids = [i for i in (request.args.get("ids") or "").split(",") if i]
    if not ids:
        return jsonify(error="Tidak ada transkrip yang dipilih."), 400
    if len(ids) > 200:
        return jsonify(error="Maksimal 200 transkrip sekali unduh."), 400

    pilih = (request.args.get("fmt") or "").strip()
    buf, dipakai, jumlah = io.BytesIO(), set(), 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for job_id in ids:
            job = engine.get(job_id)
            if job is None or not job.outputs:
                continue
            fmt = pilih if pilih in job.outputs else (
                "txt" if "txt" in job.outputs else next(iter(job.outputs)))
            sumber = Path(job.outputs[fmt])
            if not sumber.exists():
                continue
            # Nama di dalam ZIP memakai judul tampilan, bukan nama berkas asal,
            # supaya hasil unduhan cocok dengan yang terlihat di pustaka.
            dasar = safe_name(job.judul or job.filename) or job_id
            dasar = Path(dasar).stem
            nama = f"{dasar}.{fmt}"
            n = 1
            while nama.lower() in dipakai:      # judul boleh kembar; nama di
                n += 1                          # dalam ZIP tidak boleh
                nama = f"{dasar} ({n}).{fmt}"
            dipakai.add(nama.lower())
            zf.write(sumber, nama)
            jumlah += 1

    if not jumlah:
        return jsonify(error="Tidak ada berkas yang bisa diunduh."), 404
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"Tuturin {jumlah} transkrip.zip")


@app.get("/api/folders")
def daftar_folder():
    """Folder tercatat DAN folder yang dipakai job, beserta jumlah isinya.

    Digabung supaya folder kosong tetap tampil, sekaligus folder lama yang
    dibuat sebelum daftar ini ada tidak hilang.
    """
    hitung: dict[str, int] = {}
    for j in engine.list_jobs():
        f = j.get("folder") or ""
        if f:
            hitung[f] = hitung.get(f, 0) + 1
            folderlib.pastikan_ada(BASE, f)
    nama = set(folderlib.daftar(BASE)) | set(hitung)
    return jsonify([{"nama": n, "jumlah": hitung.get(n, 0)}
                    for n in sorted(nama, key=str.lower)])


@app.post("/api/folders")
def buat_folder():
    nama = folderlib.buat(BASE, (request.get_json(silent=True) or {}).get("nama", ""))
    if nama is None:
        return jsonify(error="Nama kosong atau folder itu sudah ada."), 400
    return jsonify(nama=nama), 201


@app.put("/api/jobs/<job_id>/nama")
def ubah_nama(job_id: str):
    """Ganti nama tampilan. Nama berkas asli sengaja TIDAK diubah — itu yang
    mengaitkan job ke berkas hasil di disk."""
    data = request.get_json(silent=True) or {}
    judul = re.sub(r"\s+", " ", str(data.get("judul", ""))).strip()[:180]
    if not engine.set_judul(job_id, judul):
        abort(404)
    return jsonify(judul=judul)


@app.put("/api/jobs/folder")
def ubah_folder_banyak():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids")
    if not isinstance(ids, list) or not ids:
        return jsonify(error="Tidak ada transkrip yang dipilih."), 400
    folder = rapikan_folder(data.get("folder", ""))
    if folder:
        folderlib.pastikan_ada(BASE, folder)
    return jsonify(dipindah=engine.set_folder_banyak([str(i) for i in ids], folder),
                   folder=folder)


@app.put("/api/folders/<path:lama>")
def ganti_nama_folder(lama: str):
    data = request.get_json(silent=True) or {}
    baru = rapikan_folder(data.get("folder", ""))
    if not baru:
        return jsonify(error="Nama folder tidak boleh kosong."), 400
    folderlib.ganti_nama(BASE, lama, baru)
    return jsonify(diubah=engine.ganti_nama_folder(lama, baru), folder=baru)


@app.delete("/api/folders/<path:nama>")
def bubarkan_folder(nama: str):
    """Bubarkan folder: isinya dikeluarkan, transkripnya sendiri tetap ada."""
    folderlib.hapus(BASE, nama)
    return jsonify(dikeluarkan=engine.ganti_nama_folder(nama, ""))


@app.put("/api/jobs/<job_id>/folder")
def ubah_folder(job_id: str):
    data = request.get_json(silent=True) or {}
    folder = rapikan_folder(data.get("folder", ""))
    if folder:
        folderlib.pastikan_ada(BASE, folder)
    if not engine.set_folder(job_id, folder):
        abort(404)
    return jsonify(folder=folder)


@app.delete("/api/jobs/<job_id>")
def delete_job(job_id: str):
    if not engine.delete(job_id):
        return jsonify(error="Job masih berjalan atau tidak ditemukan."), 409
    return "", 204


# ---------------------------------------------------------------- editor cue

@app.get("/jobs/<job_id>/editor")
def editor(job_id: str):
    job = engine.get(job_id)
    if job is None or "srt" not in job.outputs:
        abort(404)
    return render_template("editor.html", job=job.to_dict())


@app.get("/api/jobs/<job_id>/audio")
def audio(job_id: str):
    """Sajikan berkas sumber. conditional=True mengaktifkan Range request,
    yang dibutuhkan pemutar untuk melompat tanpa mengunduh seluruh berkas."""
    job = engine.get(job_id)
    if job is None or not job.source.exists():
        abort(404)
    return send_file(job.source, conditional=True)


@app.get("/api/jobs/<job_id>/peaks")
def peaks(job_id: str):
    job = engine.get(job_id)
    if job is None or not job.source.exists():
        abort(404)
    try:
        data = cuelib.compute_peaks(
            job.source, OUTPUTS / job_id / "peaks.json", job.duration)
    except (RuntimeError, OSError) as exc:
        return jsonify(error=str(exc)), 500
    return jsonify(data)


@app.get("/api/jobs/<job_id>/cues")
def get_cues(job_id: str):
    job = engine.get(job_id)
    if job is None or "srt" not in job.outputs:
        abort(404)
    try:
        return jsonify(cuelib.read_srt(Path(job.outputs["srt"])))
    except OSError:
        return jsonify(error="Berkas SRT tidak terbaca."), 500


@app.put("/api/jobs/<job_id>/cues")
def put_cues(job_id: str):
    job = engine.get(job_id)
    if job is None or "srt" not in job.outputs:
        abort(404)
    payload = request.get_json(silent=True)
    if not isinstance(payload, list):
        return jsonify(error="Butuh senarai cue."), 400

    bersih = []
    for i, c in enumerate(payload):
        if not isinstance(c, dict):
            return jsonify(error=f"Cue ke-{i + 1} bukan objek."), 400
        try:
            start, end = float(c.get("start", 0)), float(c.get("end", 0))
        except (TypeError, ValueError):
            return jsonify(error=f"Waktu cue ke-{i + 1} tidak sah."), 400
        if end < start:
            return jsonify(error=f"Cue ke-{i + 1}: akhir mendahului awal."), 400
        teks = str(c.get("text", "")).replace("\n", " ").strip()
        bersih.append({"start": max(0.0, start), "end": max(0.0, end), "text": teks})
    bersih.sort(key=lambda c: c["start"])

    srt = Path(job.outputs["srt"])
    try:
        cuelib.write_srt(srt, bersih)
        # Jaga format lain tetap selaras dengan hasil suntingan.
        if "txt" in job.outputs:
            cuelib.write_txt(Path(job.outputs["txt"]), bersih)
        if "vtt" in job.outputs:
            cuelib.write_vtt(Path(job.outputs["vtt"]), bersih)
    except OSError as exc:
        return jsonify(error=f"Gagal menyimpan: {exc}"), 500
    return jsonify(saved=len(bersih), formats=sorted(job.outputs))


# ------------------------------------------------------- ringkasan AI (DeepSeek)
#
# Satu-satunya bagian aplikasi yang mengirim data keluar dari mesin ini.
# Selalu dimulai oleh tindakan pengguna, tidak pernah otomatis.

_ringkasan: dict[str, dict] = {}
_ringkasan_lock = threading.Lock()


def _berkas_ringkasan(job_id: str) -> Path:
    return OUTPUTS / job_id / "summary.md"


def _sumber_teks(job) -> str:
    if "txt" in job.outputs and Path(job.outputs["txt"]).exists():
        return Path(job.outputs["txt"]).read_text(encoding="utf-8")
    if "srt" in job.outputs and Path(job.outputs["srt"]).exists():
        return "\n".join(c["text"] for c in cuelib.read_srt(Path(job.outputs["srt"])))
    return ""


def _kerjakan_ringkasan(job_id: str, teks: str, key: str) -> None:
    def lapor(tahap: str) -> None:
        with _ringkasan_lock:
            _ringkasan[job_id]["stage"] = tahap

    try:
        hasil, pakai = summarize.ringkas(teks, key, lapor=lapor)
        try:
            _berkas_ringkasan(job_id).write_text(hasil, encoding="utf-8")
        except OSError:
            pass  # gagal menyimpan bukan alasan membuang hasil
        with _ringkasan_lock:
            _ringkasan[job_id] = {"status": "done", "text": hasil, "usage": pakai,
                                  "stage": "", "finished": time.time()}
    except summarize.SummaryError as exc:
        with _ringkasan_lock:
            _ringkasan[job_id] = {"status": "error", "error": str(exc), "stage": ""}
    except Exception as exc:  # pragma: no cover
        with _ringkasan_lock:
            _ringkasan[job_id] = {"status": "error",
                                  "error": f"kesalahan tak terduga: {exc}", "stage": ""}


@app.post("/api/jobs/<job_id>/summary")
def buat_ringkasan(job_id: str):
    job = engine.get(job_id)
    if job is None or job.status != "done":
        abort(404)

    with _ringkasan_lock:
        ada = _ringkasan.get(job_id)
        if ada and ada["status"] == "running":
            return jsonify(status="running", stage=ada.get("stage", "")), 202

    teks = _sumber_teks(job)
    if not teks.strip():
        return jsonify(error="Transkrip kosong."), 400
    try:
        key = summarize.load_api_key(BASE)
    except summarize.SummaryError as exc:
        return jsonify(error=str(exc)), 503

    with _ringkasan_lock:
        _ringkasan[job_id] = {"status": "running", "stage": "menyiapkan"}
    threading.Thread(target=_kerjakan_ringkasan, args=(job_id, teks, key),
                     daemon=True).start()
    return jsonify(status="running", words=len(teks.split())), 202


@app.get("/api/jobs/<job_id>/summary")
def ambil_ringkasan(job_id: str):
    if engine.get(job_id) is None:
        abort(404)
    with _ringkasan_lock:
        d = _ringkasan.get(job_id)
    if d:
        return jsonify(d)
    berkas = _berkas_ringkasan(job_id)
    if berkas.exists():
        return jsonify(status="done", text=berkas.read_text(encoding="utf-8"),
                       usage={}, cached=True)
    return jsonify(status="none")


@app.get("/api/jobs/<job_id>/summary/download/docx")
def unduh_ringkasan(job_id: str):
    """Ringkasan sebagai .docx — dirender ulang tiap diminta.

    Sengaja tidak disimpan: berkasnya murah dibuat, dan menyimpannya berarti
    satu berkas lagi yang bisa basi saat ringkasan dibuat ulang.
    """
    job = engine.get(job_id)
    if job is None:
        abort(404)
    berkas = _berkas_ringkasan(job_id)
    if not berkas.exists():
        abort(404)

    d = job.to_dict()
    judul = Path(d["judul"]).stem
    meta = {"filename": d["filename"], "bahasa": d["lang"], "detik": int(d["duration"])}
    keluar = OUTPUTS / job_id / "ringkasan.docx"
    try:
        tidylib.tulis_ringkasan_docx(
            keluar, judul, meta, berkas.read_text(encoding="utf-8"))
    except (OSError, ImportError) as exc:
        return jsonify(error=f"gagal membuat DOCX: {exc}"), 500
    return send_file(keluar, as_attachment=True,
                     download_name=f"{judul} (ringkasan).docx")


@app.delete("/api/jobs/<job_id>/summary")
def hapus_ringkasan(job_id: str):
    if engine.get(job_id) is None:
        abort(404)
    with _ringkasan_lock:
        _ringkasan.pop(job_id, None)
    _berkas_ringkasan(job_id).unlink(missing_ok=True)
    return "", 204


# ------------------------------------------------------ rapikan transkrip (AI)
#
# Berbeda dari ringkasan: tidak memangkas isi. Keluaran sepanjang masukan,
# ditulis sebagai .md dan .docx.

_rapi: dict[str, dict] = {}
_rapi_lock = threading.Lock()


def _berkas_rapi(job_id: str, ext: str) -> Path:
    return OUTPUTS / job_id / f"rapi.{ext}"


def _kerjakan_rapi(job_id: str, meta: dict, cue_list: list, key: str) -> None:
    def lapor(tahap: str) -> None:
        with _rapi_lock:
            _rapi[job_id]["stage"] = tahap

    try:
        poin, (isi, pakai) = tidylib.rapikan(cue_list, key, lapor=lapor)
        meta = {**meta, **pakai}
        judul = Path(meta.get("judul") or meta["filename"]).stem
        md = tidylib.susun_markdown(judul, meta, poin, isi)
        try:
            _berkas_rapi(job_id, "md").write_text(md, encoding="utf-8")
            tidylib.tulis_docx(_berkas_rapi(job_id, "docx"), judul, meta, poin, isi)
        except (OSError, ImportError) as exc:
            with _rapi_lock:
                _rapi[job_id] = {"status": "error",
                                 "error": f"hasil jadi tapi gagal ditulis: {exc}",
                                 "stage": ""}
            return
        with _rapi_lock:
            _rapi[job_id] = {"status": "done", "stage": "", "usage": pakai,
                             "preview": isi[:1200], "poin": poin}
    except summarize.SummaryError as exc:
        with _rapi_lock:
            _rapi[job_id] = {"status": "error", "error": str(exc), "stage": ""}
    except Exception as exc:  # pragma: no cover
        with _rapi_lock:
            _rapi[job_id] = {"status": "error",
                             "error": f"kesalahan tak terduga: {exc}", "stage": ""}


@app.post("/api/jobs/<job_id>/tidy")
def buat_rapi(job_id: str):
    job = engine.get(job_id)
    if job is None or job.status != "done" or "srt" not in job.outputs:
        abort(404)
    with _rapi_lock:
        ada = _rapi.get(job_id)
        if ada and ada["status"] == "running":
            return jsonify(status="running", stage=ada.get("stage", "")), 202

    try:
        cue_list = cuelib.read_srt(Path(job.outputs["srt"]))
    except OSError:
        return jsonify(error="Berkas SRT tidak terbaca."), 500
    if not cue_list:
        return jsonify(error="Tidak ada cue untuk dirapikan."), 400
    try:
        key = summarize.load_api_key(BASE)
    except summarize.SummaryError as exc:
        return jsonify(error=str(exc)), 503

    d = job.to_dict()
    detik = int(d["duration"])
    meta = {"filename": d["filename"], "judul": d["judul"], "bahasa": d["lang"],
            "detik": detik, "durasi": f"{detik // 60}m {detik % 60:02d}d"}

    with _rapi_lock:
        _rapi[job_id] = {"status": "running", "stage": "menyiapkan"}
    threading.Thread(target=_kerjakan_rapi, args=(job_id, meta, cue_list, key),
                     daemon=True).start()
    teks, _ = tidylib.sumber_berstempel(cue_list)
    return jsonify(status="running", words=tidylib.hitung_kata(teks),
                   chunks=len(tidylib.potong(teks))), 202


@app.get("/api/jobs/<job_id>/tidy")
def ambil_rapi(job_id: str):
    if engine.get(job_id) is None:
        abort(404)
    with _rapi_lock:
        d = _rapi.get(job_id)
    if d:
        return jsonify({**d, "files": [e for e in ("md", "docx")
                                       if _berkas_rapi(job_id, e).exists()]})
    if _berkas_rapi(job_id, "md").exists():
        return jsonify(status="done", cached=True, usage={},
                       preview=_berkas_rapi(job_id, "md").read_text(encoding="utf-8")[:1200],
                       files=[e for e in ("md", "docx") if _berkas_rapi(job_id, e).exists()])
    return jsonify(status="none", files=[])


@app.get("/api/jobs/<job_id>/tidy/text")
def teks_rapi(job_id: str):
    """Isi penuh transkrip rapi, untuk dibaca langsung di panel.

    Dipisah dari rute status supaya balasan polling tetap ringan — isi ini
    bisa puluhan ribu huruf dan hanya perlu diambil sekali saat sudah jadi.
    """
    if engine.get(job_id) is None:
        abort(404)
    berkas = _berkas_rapi(job_id, "md")
    if not berkas.exists():
        abort(404)
    return berkas.read_text(encoding="utf-8"), 200, \
        {"Content-Type": "text/plain; charset=utf-8"}


@app.get("/api/jobs/<job_id>/tidy/download/<ext>")
def unduh_rapi(job_id: str, ext: str):
    if engine.get(job_id) is None or ext not in ("md", "docx"):
        abort(404)
    f = _berkas_rapi(job_id, ext)
    if not f.exists():
        abort(404)
    job = engine.get(job_id)
    nama = f"{Path(job.judul or job.filename).stem} (rapi).{ext}"
    return send_file(f, as_attachment=True, download_name=nama)


@app.delete("/api/jobs/<job_id>/tidy")
def hapus_rapi(job_id: str):
    if engine.get(job_id) is None:
        abort(404)
    with _rapi_lock:
        _rapi.pop(job_id, None)
    for e in ("md", "docx"):
        _berkas_rapi(job_id, e).unlink(missing_ok=True)
    return "", 204


# ------------------------------------------------------------- AI Chat
#
# Mengirim isi transkrip ke DeepSeek, sama seperti Ringkasan dan Rapikan.
# Hanya berjalan ketika pengguna menekan kirim.


def _konteks_chat(job) -> str:
    if "srt" not in job.outputs:
        return ""
    try:
        cue_list = cuelib.read_srt(Path(job.outputs["srt"]))
    except OSError:
        return ""
    return chatlib.bangun_konteks(cue_list, OUTPUTS / job.id / "rapi.md")


@app.get("/api/jobs/<job_id>/suggest")
def saran_tanya(job_id: str):
    """Tiga pertanyaan pembuka dari isi transkrip. Disimpan agar tidak diulang."""
    job = engine.get(job_id)
    if job is None or job.status != "done":
        abort(404)
    simpanan = OUTPUTS / job_id / "suggest.json"
    if simpanan.exists():
        try:
            return jsonify(items=json.loads(simpanan.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass

    konteks = _konteks_chat(job)
    if not konteks:
        return jsonify(items=[])
    try:
        key = summarize.load_api_key(BASE)
        items = chatlib.saran(konteks, key)
    except summarize.SummaryError as exc:
        return jsonify(error=str(exc), items=[]), 503
    try:
        simpanan.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return jsonify(items=items)


@app.post("/api/jobs/<job_id>/chat")
def tanya(job_id: str):
    job = engine.get(job_id)
    if job is None or job.status != "done":
        abort(404)
    data = request.get_json(silent=True) or {}
    pertanyaan = str(data.get("question", "")).strip()
    if not pertanyaan:
        return jsonify(error="Pertanyaan kosong."), 400
    riwayat = data.get("history")
    riwayat = riwayat if isinstance(riwayat, list) else []

    konteks = _konteks_chat(job)
    if not konteks:
        return jsonify(error="Transkrip tidak tersedia untuk ditanyai."), 400
    try:
        key = summarize.load_api_key(BASE)
        balasan, pakai = chatlib.jawab(konteks, riwayat, pertanyaan, key)
    except summarize.SummaryError as exc:
        return jsonify(error=str(exc)), 502
    return jsonify(reply=balasan, usage=pakai)


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="File melebihi batas 4 GB."), 413


if __name__ == "__main__":
    try:
        require_binaries()
    except TranscriberError as exc:
        raise SystemExit(f"error: {exc}")

    host = konfig.ambil(BASE, "HOST", "127.0.0.1")
    port = int(konfig.ambil(BASE, "PORT", "5005"))
    terbuka = host not in ("127.0.0.1", "localhost")

    # Menolak terbuka ke jaringan tanpa kata sandi. Rekaman, transkrip, dan
    # kuota DeepSeek semuanya terjangkau siapa pun yang bisa mencapai port ini.
    if terbuka and not SANDI and konfig.ambil(BASE, "ALLOW_NO_AUTH") != "1":
        raise SystemExit(
            "\n  Menolak mendengar di " + host + " tanpa kata sandi.\n\n"
            "  Siapa pun di jaringan bisa membaca dan menghapus transkrip Anda,\n"
            "  serta memakai kuota DeepSeek Anda.\n\n"
            "  Tambahkan ke berkas .env:\n"
            "      APP_PASSWORD=sandi-pilihan-anda\n\n"
            "  Bila jaringan Anda benar-benar tepercaya dan Anda tetap ingin\n"
            "  tanpa sandi, setel ALLOW_NO_AUTH=1 — tidak disarankan.\n")

    print(f"\n  Transkrip berjalan di  http://127.0.0.1:{port}")
    if terbuka:
        for ip in konfig.alamat_lan():
            print(f"  Dari komputer lain     http://{ip}:{port}")
        print("  Gerbang sandi          " + ("aktif" if SANDI else "MATI — siapa pun bisa masuk"))
    else:
        print("  Hanya mesin ini. Setel HOST=0.0.0.0 di .env untuk membuka ke jaringan.")
    print(f"  Thread whisper: {engine.threads}\n")

    app.run(host=host, port=port, debug=False, threaded=True)
