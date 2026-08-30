const $ = (s) => document.querySelector(s);
const form = $("#form"), fileInput = $("#file"), drop = $("#drop"),
      picked = $("#picked"), submit = $("#submit"), errBox = $("#err"),
      urlInput = $("#url"), urlHapus = $("#urlHapus"),
      jobsEl = $("#jobs"), cari = $("#cari");

let semua = [];
let folderAktif = null;   // null = semua, "" = tanpa folder
const terpilih = new Set();   // id job yang dicentang

const fmtBytes = (n) => {
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i < 2 ? 0 : 1)} ${u[i]}`;
};
const fmtDur = (s) => {
  s = Math.round(s || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), d = s % 60;
  return h ? `${h}j ${m}m` : m ? `${m}m ${String(d).padStart(2, "0")}d` : `${d}d`;
};
const fmtTgl = (ts) => new Date(ts * 1000).toLocaleDateString("id-ID",
  { day: "2-digit", month: "short", year: "numeric" });

// ------------------------------------------------------------- unggah

function adaBahan() {
  return fileInput.files.length > 0 || urlInput.value.trim() !== "";
}

function refreshPicked() {
  const files = [...fileInput.files];
  picked.innerHTML = files
    .map((f) => `<li><span>${escapeHtml(f.name)}</span><span>${fmtBytes(f.size)}</span></li>`).join("");
  picked.hidden = files.length === 0;
  // Berkas dan tautan boleh berjalan bersamaan; cukup salah satu terisi.
  submit.disabled = !adaBahan();
  urlHapus.hidden = urlInput.value.trim() === "";
}
fileInput.addEventListener("change", refreshPicked);
urlInput.addEventListener("input", refreshPicked);
urlHapus.addEventListener("click", () => {
  urlInput.value = ""; refreshPicked(); urlInput.focus();
});
["dragenter", "dragover"].forEach((e) =>
  drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((e) =>
  drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (ev) => {
  if (ev.dataTransfer?.files?.length) { fileInput.files = ev.dataTransfer.files; refreshPicked(); }
});

$("#toggleUnggah").onclick = (e) => {
  const buka = document.body.classList.toggle("unggah-tutup");
  e.currentTarget.setAttribute("aria-expanded", String(!buka));
  $("#karet").textContent = buka ? "▸" : "▾";
};

// ------------------------------------------------------------ rekam langsung
// Dua sumber, satu jalur perekaman:
//   - mikrofon  -> getUserMedia
//   - audio tab -> getDisplayMedia, trek videonya dibuang
// Keduanya butuh konteks aman (HTTPS atau localhost).
const rekamBtn = $("#rekamBtn"), rekamLabel = $("#rekamLabel"),
      rekamTabBtn = $("#rekamTabBtn"), rekamTabLabel = $("#rekamTabLabel"),
      rekamWaktu = $("#rekamWaktu"), rekamMeter = $("#rekamMeter"),
      rekamNota = $("#rekamNota");

let perekam = null, potongan = [], arus = null, arusAsli = null,
    sumberRekam = "mikrofon", mulaiPada = 0,
    jamRekam = null, audioCtx = null, animasi = null;

function bisaMerekam() {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia &&
            typeof MediaRecorder !== "undefined");
}
function bisaRekamTab() {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia &&
            typeof MediaRecorder !== "undefined");
}
function sedangMerekam() { return !!perekam && perekam.state === "recording"; }

function tipeRekaman() {
  for (const t of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"]) {
    if (MediaRecorder.isTypeSupported(t)) return t;
  }
  return "";
}

function catat(pesan, galat = false) {
  rekamNota.textContent = pesan;
  rekamNota.classList.toggle("galat", galat);
}

function setelRekam(aktif) {
  const tab = sumberRekam === "tab";
  rekamBtn.classList.toggle("aktif", aktif && !tab);
  rekamTabBtn.classList.toggle("aktif", aktif && tab);
  rekamLabel.textContent = aktif && !tab ? "Berhenti & transkripsi" : "Rekam mikrofon";
  rekamTabLabel.textContent = aktif && tab ? "Berhenti & transkripsi" : "Rekam audio tab";
  // Sumber lain dikunci selama merekam agar tidak ada dua rekaman bertabrakan.
  rekamBtn.disabled = aktif && tab;
  rekamTabBtn.disabled = (aktif && !tab) || !bisaRekamTab();
  rekamWaktu.hidden = !aktif;
  rekamMeter.hidden = !aktif;
  submit.disabled = aktif || !adaBahan();
}

function pantauLevel(stream) {
  try {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const sumber = audioCtx.createMediaStreamSource(stream);
    const analis = audioCtx.createAnalyser();
    analis.fftSize = 512;
    sumber.connect(analis);
    const buf = new Uint8Array(analis.frequencyBinCount);
    const bar = rekamMeter.querySelector("i");
    const gambar = () => {
      analis.getByteTimeDomainData(buf);
      let puncak = 0;
      for (const v of buf) puncak = Math.max(puncak, Math.abs(v - 128));
      bar.style.width = Math.min(100, (puncak / 128) * 260) + "%";
      animasi = requestAnimationFrame(gambar);
    };
    gambar();
  } catch { /* meter hanya hiasan — perekaman tetap jalan tanpanya */ }
}

function hentikanPantau() {
  if (animasi) cancelAnimationFrame(animasi), animasi = null;
  if (audioCtx) audioCtx.close().catch(() => {}), audioCtx = null;
}

function mulaiDenganArus(arusRekam, sumber, asli) {
  arus = arusRekam;
  arusAsli = asli || arusRekam;
  sumberRekam = sumber;

  const tipe = tipeRekaman();
  potongan = [];
  perekam = new MediaRecorder(arus, tipe ? { mimeType: tipe } : undefined);
  perekam.ondataavailable = (ev) => { if (ev.data.size) potongan.push(ev.data); };
  perekam.onstop = kirimRekaman;
  perekam.start(1000);          // potongan tiap detik agar tidak menumpuk di memori

  // Menghentikan berbagi lewat bilah peramban harus ikut menutup rekaman,
  // bukan meninggalkannya menggantung.
  arusAsli.getTracks().forEach((t) => {
    t.addEventListener("ended", () => { if (sedangMerekam()) hentikanRekam(); });
  });

  mulaiPada = Date.now();
  rekamWaktu.textContent = "0:00";
  jamRekam = setInterval(() => {
    rekamWaktu.textContent = fmtDur((Date.now() - mulaiPada) / 1000);
  }, 1000);
  pantauLevel(arus);
  setelRekam(true);
}

async function mulaiRekam() {
  catat("");
  let s;
  try {
    s = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
  } catch (e) {
    catat(e.name === "NotAllowedError"
      ? "Izin mikrofon ditolak. Beri izin di pengaturan situs, lalu coba lagi."
      : e.name === "NotFoundError"
        ? "Tidak ada mikrofon terpasang di komputer ini."
        : `Mikrofon tidak bisa dibuka: ${e.message}`, true);
    return;
  }
  mulaiDenganArus(s, "mikrofon");
}

async function mulaiRekamTab() {
  catat("");
  let s;
  try {
    // Chrome tidak mengizinkan permintaan audio saja; video diminta lalu dibuang.
    s = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
  } catch (e) {
    catat(e.name === "NotAllowedError"
      ? "Berbagi dibatalkan."
      : `Tidak bisa menangkap audio tab: ${e.message}`, true);
    return;
  }

  const trekAudio = s.getAudioTracks();
  if (!trekAudio.length) {
    s.getTracks().forEach((t) => t.stop());
    catat("Tab itu tidak mengirim audio. Pilih tab (bukan Seluruh Layar) " +
          "dan centang \"Also share tab audio\" di kotak dialognya.", true);
    return;
  }
  s.getVideoTracks().forEach((t) => t.stop());   // video tidak dipakai sama sekali
  mulaiDenganArus(new MediaStream(trekAudio), "tab", s);
}

function hentikanRekam() {
  if (perekam && perekam.state !== "inactive") perekam.stop();
  if (jamRekam) clearInterval(jamRekam), jamRekam = null;
  hentikanPantau();
  if (arusAsli) arusAsli.getTracks().forEach((t) => t.stop());
  if (arus) arus.getTracks().forEach((t) => t.stop());
  arus = arusAsli = null;
  setelRekam(false);
}

async function kirimRekaman() {
  const tipe = perekam.mimeType || "audio/webm";
  const blob = new Blob(potongan, { type: tipe });
  potongan = [];
  if (blob.size < 2000) {
    catat("Rekaman terlalu pendek — tidak ada yang dikirim.", true);
    return;
  }

  const ext = tipe.includes("mp4") ? "mp4" : "webm";
  const t = new Date();
  const dua = (n) => String(n).padStart(2, "0");
  const awalan = sumberRekam === "tab" ? "Rekaman tab" : "Rekaman";
  const nama = `${awalan} ${t.getFullYear()}-${dua(t.getMonth() + 1)}-${dua(t.getDate())} ` +
               `${dua(t.getHours())}.${dua(t.getMinutes())}.${ext}`;

  const data = new FormData(form);
  data.delete("file");                       // buang pilihan berkas, kirim rekaman
  data.append("file", new File([blob], nama, { type: tipe }));

  catat(`Mengunggah ${fmtBytes(blob.size)}…`);
  try {
    const r = await fetch("/api/jobs", { method: "POST", body: data });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
    catat("Rekaman masuk antrean.");
    segarkan();
  } catch (e) {
    catat(`Gagal mengunggah: ${e.message}`, true);
  }
}

if (!bisaMerekam()) {
  rekamBtn.disabled = true;
  rekamTabBtn.disabled = true;
  catat(window.isSecureContext
    ? "Peramban ini tidak mendukung perekaman."
    : "Perekaman butuh HTTPS. Buka lewat alamat https:// untuk memakainya.");
} else {
  rekamBtn.onclick = () => (sedangMerekam() ? hentikanRekam() : mulaiRekam());
  rekamTabBtn.onclick = () => (sedangMerekam() ? hentikanRekam() : mulaiRekamTab());
  rekamTabBtn.disabled = !bisaRekamTab();
  if (!bisaRekamTab()) rekamTabBtn.title = "Peramban ini tidak mendukung tangkap audio tab";
  window.addEventListener("beforeunload", (e) => { if (sedangMerekam()) e.preventDefault(); });
}

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  errBox.hidden = true;
  if (!adaBahan()) return;
  if (!form.querySelector("input[name=formats]:checked")) {
    errBox.textContent = "Pilih minimal satu format output."; errBox.hidden = false; return;
  }
  // Tautan perlu satu-dua detik untuk mengambil metadata di server, jadi
  // labelnya harus jujur soal apa yang sedang ditunggu.
  submit.disabled = true;
  submit.textContent = urlInput.value.trim() ? "Membaca tautan…" : "Mengunggah…";
  try {
    const res = await fetch("/api/jobs", { method: "POST", body: new FormData(form) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
    fileInput.value = ""; urlInput.value = ""; refreshPicked();
  } catch (e) {
    errBox.textContent = e.message; errBox.hidden = false;
  } finally {
    submit.textContent = "Mulai transkripsi"; refreshPicked();
  }
  segarkan();
});

// ------------------------------------------------------------- pustaka

const SVG = (isi, w = 18) =>
  `<svg viewBox="0 0 24 24" width="${w}" height="${w}" fill="none" stroke="currentColor" ` +
  `stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${isi}</svg>`;

const IK = {
  folder: SVG('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
  berkas: SVG('<path d="M14 3v5h5"/><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>'),
  titik: SVG('<circle cx="12" cy="5" r="1.6" fill="currentColor" stroke="none"/>' +
             '<circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/>' +
             '<circle cx="12" cy="19" r="1.6" fill="currentColor" stroke="none"/>'),
  pindah: SVG('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 11v5"/><path d="M9.5 13.5 12 11l2.5 2.5"/>'),
  nama: SVG('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>'),
  unduh: SVG('<path d="M12 3v12"/><path d="M7.5 10.5 12 15l4.5-4.5"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>'),
  hapus: SVG('<path d="M4 7h16"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/><path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/>'),
  kotak: SVG('<rect x="4" y="4" width="16" height="16" rx="3"/>'),
  kotakCentang: SVG('<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8.5 12.5 11 15l4.5-4.5"/>'),
};

const NAMA_FORMAT = { txt: "Teks (TXT)", srt: "Subtitle (SRT)",
                      vtt: "Subtitle (VTT)", json: "Data (JSON)" };

const PER_HALAMAN = 12;
let halamanKini = 1;
let daftarFolder = [];        // dari /api/folders — termasuk yang kosong

const SEDANG = ["queued", "downloading", "converting", "detecting", "transcribing"];
const sibukJob = (j) => SEDANG.includes(j.status);

function semuaFolder() { return daftarFolder.map((f) => f.nama); }

// --------------------------------------------------------------- folder

function isiPilihanUnggah() {
  const sel = $("#folder");
  if (!sel) return;
  const dipilih = sel.value;                 // pertahankan pilihan saat digambar ulang
  sel.innerHTML = `<option value="">Tanpa folder</option>` +
    semuaFolder().map((f) =>
      `<option value="${escapeHtml(f)}">${escapeHtml(f)}</option>`).join("") +
    `<option value="__baru__">+ Folder baru…</option>`;
  if (dipilih && [...sel.options].some((o) => o.value === dipilih)) sel.value = dipilih;
}

// Memilih "+ Folder baru…" membuat foldernya sekarang juga, lalu langsung
// memilihnya — supaya nama itu tidak hilang bila unggahan dibatalkan.
document.addEventListener("change", async (ev) => {
  if (ev.target.id !== "folder" || ev.target.value !== "__baru__") return;
  const sel = ev.target;
  const nama = prompt("Nama folder baru:", "");
  if (nama === null || !nama.trim()) { sel.value = ""; return; }
  try {
    const r = await fetch("/api/folders", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nama }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    daftarFolder.push({ nama: d.nama, jumlah: 0 });
    isiPilihanUnggah();
    sel.value = d.nama;
    segarkan();
  } catch (e) {
    alert("Gagal membuat folder: " + e.message);
    sel.value = "";
  }
});

function gambarFolderKartu() {
  isiPilihanUnggah();
  const grid = $("#folderGrid");
  if (!daftarFolder.length) {
    grid.innerHTML = `<p class="folder-kosong">Belum ada folder. Buat satu untuk mengelompokkan transkrip.</p>`;
    return;
  }
  grid.innerHTML = daftarFolder.map((f) => `
    <div class="folder-kartu ${folderAktif === f.nama ? "on" : ""}" data-buka="${escapeHtml(f.nama)}">
      ${IK.folder}
      <span class="folder-nama" title="${escapeHtml(f.nama)}">${escapeHtml(f.nama)}</span>
      <span class="folder-jumlah">${f.jumlah}</span>
      <details class="menu" data-stop="1">
        <summary title="Tindakan folder" aria-label="Tindakan folder">${IK.titik}</summary>
        <div class="menu-isi">
          <p class="menu-judul">Folder</p>
          <button data-folrename="${escapeHtml(f.nama)}">Ganti nama…</button>
          <button class="menu-hapus" data-foldel="${escapeHtml(f.nama)}">Bubarkan folder</button>
        </div>
      </details>
    </div>`).join("");
}

$("#folderGrid").addEventListener("click", async (ev) => {
  if (ev.target.closest("details.menu")) return;      // menu punya penangannya sendiri
  const k = ev.target.closest("[data-buka]");
  if (!k) return;
  const nama = k.dataset.buka;
  folderAktif = folderAktif === nama ? null : nama;   // klik lagi = kembali ke semua
  halamanKini = 1;
  gambar();
});

$("#folderGrid").addEventListener("click", async (ev) => {
  const ren = ev.target.closest("[data-folrename]");
  const del = ev.target.closest("[data-foldel]");
  if (!ren && !del) return;
  const nama = (ren || del).dataset[ren ? "folrename" : "foldel"];
  try {
    if (ren) {
      const baru = prompt("Nama baru untuk folder ini:", nama);
      if (baru === null || !baru.trim()) return;
      const r = await fetch(`/api/folders/${encodeURIComponent(nama)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder: baru }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      if (folderAktif === nama) folderAktif = (await r.json()).folder;
    } else {
      if (!confirm(`Bubarkan folder "${nama}"?\n\nTranskrip di dalamnya TIDAK dihapus — ` +
                   `hanya dikeluarkan dari folder.`)) return;
      const r = await fetch(`/api/folders/${encodeURIComponent(nama)}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      if (folderAktif === nama) folderAktif = null;
    }
    segarkan();
  } catch (e) { alert("Gagal: " + e.message); }
});

$("#buatFolder").onclick = async () => {
  const nama = prompt("Nama folder baru:", "");
  if (nama === null || !nama.trim()) return;
  try {
    const r = await fetch("/api/folders", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nama }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
    segarkan();
  } catch (e) { alert("Gagal membuat folder: " + e.message); }
};

// --------------------------------------------------------------- baris

function barisProses(j) {
  // "downloading" sengaja tidak masuk: kemajuannya nyata (0-99), bukan tak-tentu.
  const indet = ["queued", "converting", "detecting"].includes(j.status);
  const cuplik = j.preview.length ? escapeHtml(j.preview[j.preview.length - 1]) : "";
  return `<div class="proses-baris">
    <div>
      <div class="proses-nama">${escapeHtml(j.judul || j.filename)}</div>
      <div class="proses-meta">${escapeHtml(j.status_label)}${j.duration ? " · " + fmtDur(j.duration) : ""}</div>
      <div class="bar ${indet ? "indet" : ""}"><i style="width:${j.progress}%"></i></div>
      ${cuplik ? `<div class="pratinjau">${cuplik}</div>` : ""}
    </div>
    <div><span class="status ${j.status}">${escapeHtml(j.status_label)}</span></div>
  </div>`;
}

function barisBerkas(j) {
  const bisaBuka = j.outputs.includes("srt");
  const nama = escapeHtml(j.judul || j.filename);
  const unduhTranskrip = j.outputs.map((f) =>
    `<a href="/api/jobs/${j.id}/download/${f}">${NAMA_FORMAT[f] || f.toUpperCase()}</a>`).join("");
  const dokumen = [
    ...(j.ai.ringkasan ? [`<a href="/api/jobs/${j.id}/summary/download/docx">Ringkasan (DOCX)</a>`] : []),
    ...(j.ai.rapi_docx ? [`<a href="/api/jobs/${j.id}/tidy/download/docx">Transkrip rapi (DOCX)</a>`] : []),
  ].join("");

  return `<tr class="${terpilih.has(j.id) ? "terpilih" : ""}" data-id="${j.id}">
    <td class="td-pilih"><input type="checkbox" data-pilih="${j.id}" ${terpilih.has(j.id) ? "checked" : ""}
        aria-label="Pilih ${nama}"></td>
    <td class="td-nama">
      ${bisaBuka ? `<a href="/jobs/${j.id}/editor">${IK.berkas}<span>${nama}</span></a>`
                 : `<span style="display:flex;gap:.5rem;align-items:center">${IK.berkas}<span>${nama}</span></span>`}
    </td>
    <td class="td-folder">${j.folder ? `<span class="k-folder">${escapeHtml(j.folder)}</span>` : ""}</td>
    <td class="td-tanggal">${fmtTgl(j.created)}</td>
    <td class="td-jenis">${j.status === "error" ? "Gagal" : "Transkripsi"}</td>
    <td class="td-menu">
      <details class="menu">
        <summary title="Tindakan lain" aria-label="Tindakan lain">${IK.titik}</summary>
        <div class="menu-isi">
          ${unduhTranskrip ? `<p class="menu-judul">Unduh transkrip</p>${unduhTranskrip}` : ""}
          ${dokumen ? `<p class="menu-judul">Unduh dokumen</p>${dokumen}` : ""}
          <p class="menu-judul">Pindahkan ke folder</p>
          ${[""].concat(semuaFolder()).map((f) => `
            <button class="menu-fol ${(j.folder || "") === f ? "kini" : ""}"
                    data-pindah="${j.id}" data-ke="${escapeHtml(f)}">
              <span class="tanda">${(j.folder || "") === f ? "✓" : ""}</span>
              ${f ? escapeHtml(f) : "Tanpa folder"}
            </button>`).join("")}
          <button class="menu-fol" data-folderbaru="${j.id}"><span class="tanda">+</span>Folder baru…</button>
          <p class="menu-judul">Kelola</p>
          <button data-nama="${j.id}">Ganti nama…</button>
          <button class="menu-hapus" data-del="${j.id}">Hapus transkrip ini</button>
        </div>
      </details>
    </td>
  </tr>`;
}

// --------------------------------------------------------------- render

function gambar() {
  // Menggambar ulang mengganti seluruh elemen, sehingga menu yang sedang
  // dibuka pengguna ikut terbanting tutup. Tunda sampai menunya ditutup.
  if (document.querySelector("details.menu[open]")) return;

  gambarFolderKartu();

  const sibuk = semua.filter(sibukJob);
  $("#proses").innerHTML = sibuk.map(barisProses).join("");

  let siap = semua.filter((j) => !sibukJob(j));
  if (folderAktif !== null) siap = siap.filter((j) => (j.folder || "") === folderAktif);
  const q = (cari.value || "").toLowerCase().trim();
  if (q) siap = siap.filter((j) => (j.judul || j.filename).toLowerCase().includes(q));

  const totalHal = Math.max(1, Math.ceil(siap.length / PER_HALAMAN));
  if (halamanKini > totalHal) halamanKini = totalHal;
  const mulai = (halamanKini - 1) * PER_HALAMAN;
  const halaman = siap.slice(mulai, mulai + PER_HALAMAN);

  // Yang sedang diproses tidak masuk tabel, tetapi tetap terlihat di kartu
  // atas — jadi harus ikut dihitung, kalau tidak lencana folder (13) dan
  // ringkasan ini (12) saling bertentangan di layar yang sama.
  let sibukDiSini = folderAktif === null ? sibuk
    : sibuk.filter((j) => (j.folder || "") === folderAktif);
  if (q) sibukDiSini = sibukDiSini.filter(
    (j) => (j.judul || j.filename).toLowerCase().includes(q));
  const jumlahTampil = siap.length + sibukDiSini.length;

  $("#berkasJudul").textContent = folderAktif === null ? "Berkas Terbaru"
    : folderAktif === "" ? "Tanpa Folder" : folderAktif;
  $("#ringkasBerkas").textContent = jumlahTampil
    ? `${jumlahTampil} berkas` + (folderAktif !== null ? " · klik folder lagi untuk kembali" : "")
    : "";

  jobsEl.innerHTML = halaman.length
    ? `<table class="tabel-berkas"><tbody>${halaman.map(barisBerkas).join("")}</tbody></table>`
    : `<p class="kosong">${semua.length ? "Tidak ada berkas di sini." : "Belum ada transkripsi. Unggah berkas untuk memulai."}</p>`;

  gambarHalaman(totalHal, siap.length);
  gambarMassal();

  const selesai = semua.filter((j) => j.status === "done");
  const total = selesai.reduce((a, j) => a + (j.duration || 0), 0);
  $("#ringkas").innerHTML = semua.length
    ? `<b>${selesai.length}</b> transkrip · <b>${fmtDur(total)}</b> audio` : "";
}

function gambarHalaman(total, jumlahBerkas) {
  const nav = $("#halaman");
  if (total <= 1) { nav.hidden = true; return; }
  nav.hidden = false;

  const tombol = (label, hal, aktif = false, mati = false, judul = "") =>
    `<button data-hal="${hal}" class="${aktif ? "on" : ""}" ${mati ? "disabled" : ""}` +
    `${judul ? ` title="${judul}" aria-label="${judul}"` : ""}>${label}</button>`;
  const jeda = () => `<span class="hal-jeda">…</span>`;

  // Jendela nomor: pertama, terakhir, dan tetangga halaman kini. Tanpa ini
  // seluruh nomor tercetak — 600 berkas berarti 50 tombol dalam satu baris.
  const sekitar = new Set([1, total, halamanKini,
                           halamanKini - 1, halamanKini + 1]);
  const nomor = [...sekitar].filter((n) => n >= 1 && n <= total).sort((a, b) => a - b);

  let html = tombol("«", 1, false, halamanKini === 1, "Halaman pertama") +
             tombol("‹", halamanKini - 1, false, halamanKini === 1, "Sebelumnya");
  nomor.forEach((n, i) => {
    if (i && n - nomor[i - 1] > 1) html += jeda();
    html += tombol(n, n, n === halamanKini);
  });
  html += tombol("›", halamanKini + 1, false, halamanKini === total, "Berikutnya") +
          tombol("»", total, false, halamanKini === total, "Halaman terakhir");

  const dari = (halamanKini - 1) * PER_HALAMAN + 1;
  const sampai = Math.min(halamanKini * PER_HALAMAN, jumlahBerkas);
  html += `<span class="hal-info">${dari}\u2013${sampai} dari ${jumlahBerkas}</span>`;
  nav.innerHTML = html;
}

$("#halaman").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-hal]");
  if (!b || b.disabled) return;
  // gambar() menolak menggambar ulang selama ada menu terbuka; tutup dulu
  // supaya nomor halaman tidak berubah tanpa isinya ikut berubah.
  document.querySelectorAll("details.menu[open]").forEach((x) => { x.open = false; });
  halamanKini = +b.dataset.hal;
  gambar();
  $("#berkasJudul").scrollIntoView({ block: "start", behavior: "smooth" });
});

// ------------------------------------------------------- tindakan massal

function gambarMassal() {
  const bar = $("#massal");
  const adaBaris = !!jobsEl.querySelector("[data-pilih]");
  if (!adaBaris) { bar.hidden = true; return; }
  bar.hidden = false;
  const semuaTercentang = [...jobsEl.querySelectorAll("[data-pilih]")].every((c) => c.checked);
  $("#pilihSemua").innerHTML = semuaTercentang ? IK.kotakCentang : IK.kotak;
  $("#pilihJumlah").textContent = terpilih.size ? `${terpilih.size} dipilih` : "Pilih berkas";
  $("#massal").querySelectorAll(".massal-ikon button").forEach((b) => { b.disabled = !terpilih.size; });
  $("[data-m=nama]").innerHTML = IK.nama;
  $("[data-m=unduh]").innerHTML = IK.unduh;
  $("[data-m=hapus]").innerHTML = IK.hapus;

  // Pemilih folder: daftar yang bisa diklik, bukan kotak isian nama.
  const menu = $("#menuPindah");
  $("#ikonPindah").innerHTML = IK.pindah;
  menu.hidden = !terpilih.size;
  if (menu.hidden) menu.open = false;
  $("#isiPindah").innerHTML =
    `<p class="menu-judul">Pindahkan ${terpilih.size} berkas ke</p>` +
    [""].concat(semuaFolder()).map((f) => `
      <button class="menu-fol" data-mke="${escapeHtml(f)}">
        <span class="tanda"></span>${f ? escapeHtml(f) : "Tanpa folder"}
      </button>`).join("") +
    `<button class="menu-fol" data-mbaru="1"><span class="tanda">+</span>Folder baru…</button>`;
}

function bersihkanPilihan() {
  terpilih.clear();
  jobsEl.querySelectorAll("[data-pilih]").forEach((c) => { c.checked = false; });
  jobsEl.querySelectorAll("tr.terpilih").forEach((k) => k.classList.remove("terpilih"));
  gambarMassal();
}

jobsEl.addEventListener("change", (ev) => {
  const c = ev.target.closest("[data-pilih]");
  if (!c) return;
  if (c.checked) terpilih.add(c.dataset.pilih); else terpilih.delete(c.dataset.pilih);
  c.closest("tr").classList.toggle("terpilih", c.checked);
  gambarMassal();      // tanpa menggambar ulang tabel, agar centang tidak hilang
});

$("#pilihSemua").onclick = () => {
  const kotak = [...jobsEl.querySelectorAll("[data-pilih]")];
  const semuanya = kotak.every((c) => c.checked);
  kotak.forEach((c) => {
    c.checked = !semuanya;
    if (c.checked) terpilih.add(c.dataset.pilih); else terpilih.delete(c.dataset.pilih);
    c.closest("tr").classList.toggle("terpilih", c.checked);
  });
  gambarMassal();
};

async function kirimFolder(ids, folder) {
  const r = await fetch("/api/jobs/folder", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids, folder }),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
  return r.json();
}

$(".massal-ikon").addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-m]");
  if (!b || !terpilih.size) return;
  const ids = [...terpilih];
  try {
    if (b.dataset.m === "nama") {
      if (ids.length !== 1) { alert("Ganti nama hanya untuk satu berkas."); return; }
      return gantiNamaJob(ids[0]);
    } else if (b.dataset.m === "unduh") {
      if (ids.length === 1) {
        // Satu berkas tetap diunduh apa adanya; membungkusnya jadi ZIP hanya
        // menambah satu langkah membuka arsip.
        const j = semua.find((x) => x.id === ids[0]);
        const fmt = j && j.outputs.includes("txt") ? "txt" : (j && j.outputs[0]);
        if (fmt) location.href = `/api/jobs/${ids[0]}/download/${fmt}`;
        else alert("Transkrip ini belum punya berkas keluaran.");
        return;
      }
      // Beberapa berkas: satu permintaan, satu ZIP. Pemanggilan window.open()
      // sekali per berkas dulu diblokir pemblokir pop-up setelah yang pertama.
      location.href = `/api/jobs/unduh-zip?ids=${encodeURIComponent(ids.join(","))}`;
      return;
    } else if (b.dataset.m === "hapus") {
      if (!confirm(`Hapus ${ids.length} transkrip beserta berkasnya? Tindakan ini permanen.`)) return;
      const gagal = [];
      for (const id of ids) {
        const r = await fetch(`/api/jobs/${id}`, { method: "DELETE" });
        if (!r.ok) gagal.push(id);
      }
      bersihkanPilihan();
      if (gagal.length) alert(`${gagal.length} transkrip gagal dihapus (mungkin masih diproses).`);
    }
    segarkan();
  } catch (e) { alert("Gagal: " + e.message); }
});

$("#isiPindah").addEventListener("click", async (ev) => {
  const ke = ev.target.closest("[data-mke]");
  const baru = ev.target.closest("[data-mbaru]");
  if (!ke && !baru) return;
  if (!terpilih.size) return;

  let folder;
  if (baru) {
    folder = prompt("Nama folder baru:", "");
    if (folder === null || !folder.trim()) return;
  } else {
    folder = ke.dataset.mke;
  }
  try {
    await kirimFolder([...terpilih], folder);
    $("#menuPindah").open = false;
    bersihkanPilihan();
    segarkan();
  } catch (e) { alert("Gagal memindahkan: " + e.message); }
});

async function gantiNamaJob(id) {
  const j = semua.find((x) => x.id === id);
  const baru = prompt("Nama tampilan untuk transkrip ini:\n(nama berkas aslinya tidak berubah)",
                      j ? j.judul || j.filename : "");
  if (baru === null) return;
  try {
    const r = await fetch(`/api/jobs/${id}/nama`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ judul: baru }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    document.querySelectorAll("details.menu[open]").forEach((x) => { x.open = false; });
    bersihkanPilihan();
    segarkan();
  } catch (e) { alert("Gagal mengganti nama: " + e.message); }
}

cari.addEventListener("input", () => { halamanKini = 1; gambar(); });


let timer = null;
async function poll() {
  try {
    const [j, f] = await Promise.all([
      fetch("/api/jobs").then((r) => r.json()),
      fetch("/api/folders").then((r) => r.json()),
    ]);
    semua = j;
    daftarFolder = Array.isArray(f) ? f : [];
  } catch { return jadwal(4000); }
  gambar();
  const aktif = semua.some((j) =>
    SEDANG.includes(j.status));
  jadwal(aktif ? 1000 : 6000);
}
function jadwal(ms) { clearTimeout(timer); timer = setTimeout(poll, ms); }

/** Segarkan tampilan sesudah tindakan yang mengubah data.
 *
 * gambar() sengaja menolak menggambar ulang selama ada menu terbuka, supaya
 * menu tidak terbanting tutup di tengah pemakaian. Tetapi hampir semua
 * tindakan DIJALANKAN dari dalam menu yang terbuka — kalau menunya tidak
 * ditutup lebih dulu, data baru terambil tetapi layar tidak pernah berubah.
 */
function segarkan() {
  document.querySelectorAll("details.menu[open]").forEach((x) => { x.open = false; });
  poll();
}


// <details> tidak menutup sendiri: tutup saudaranya saat satu dibuka, dan
// tutup semua saat klik di luar atau menekan Escape.
document.addEventListener("toggle", (ev) => {
  const d = ev.target;
  if (d.tagName !== "DETAILS" || !d.open) return;
  document.querySelectorAll("details.menu[open]").forEach((x) => { if (x !== d) x.open = false; });
}, true);

document.addEventListener("click", (ev) => {
  if (ev.target.closest("details.menu")) return;
  document.querySelectorAll("details.menu[open]").forEach((x) => { x.open = false; });
});

document.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape") return;
  document.querySelectorAll("details.menu[open]").forEach((x) => { x.open = false; });
});

jobsEl.addEventListener("click", async (ev) => {
  // Pemilih folder di dalam menu: satu klik, tanpa mengetik nama.
  const pindah = ev.target.closest("[data-pindah]");
  if (pindah) {
    try {
      await kirimFolder([pindah.dataset.pindah], pindah.dataset.ke);
      document.querySelectorAll("details.menu[open]").forEach((x) => { x.open = false; });
      segarkan();
    } catch (e) { alert("Gagal memindahkan: " + e.message); }
    return;
  }

  const baru = ev.target.closest("[data-folderbaru]");
  if (baru) {
    const nama = prompt("Nama folder baru:", "");
    if (nama === null) return;
    try {
      await kirimFolder([baru.dataset.folderbaru], nama);
      document.querySelectorAll("details.menu[open]").forEach((x) => { x.open = false; });
      segarkan();
    } catch (e) { alert("Gagal membuat folder: " + e.message); }
    return;
  }

  const nama = ev.target.closest("[data-nama]");
  if (nama) return gantiNamaJob(nama.dataset.nama);

  const del = ev.target.closest("[data-del]");
  if (!del) return;
  const j = semua.find((x) => x.id === del.dataset.del);
  if (!confirm(`Hapus "${j ? j.judul || j.filename : "job ini"}" beserta transkrip dan hasil AI-nya?`)) return;
  const r = await fetch(`/api/jobs/${del.dataset.del}`, { method: "DELETE" });
  if (!r.ok) alert((await r.json().catch(() => ({}))).error || "Gagal menghapus.");
  terpilih.delete(del.dataset.del);
  segarkan();
});

refreshPicked();
poll();
