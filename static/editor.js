// Ikon SVG untuk tombol yang berganti keadaan. Semuanya konstanta di berkas
// ini — tidak ada masukan pengguna yang masuk ke innerHTML.
const IKON = {
  putar: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.2v13.6L19 12z"/></svg>',
  jeda: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="7" y="5" width="3.6" height="14" rx="1"/><rect x="13.4" y="5" width="3.6" height="14" rx="1"/></svg>',
  salin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>',
  centang: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5 10 17.5 19 7"/></svg>',
};

const JOB = document.body.dataset.job;
const FORMATS = (document.body.dataset.formats || "").split(",").filter(Boolean);
const $ = (s) => document.querySelector(s);
const audio = $("#audio"), list = $("#list"), canvas = $("#wave"),
      saveBtn = $("#save"), dirtyTag = $("#dirty"), bar = $("#bar");

let cues = [];        // cue mentah dari SRT
let baris = [];       // yang ditampilkan: blok ucapan, atau cue mentah
let peaks = [], durasi = 0, aktif = -1, kotor = false, gabung = true;

// Whisper memecah ucapan jadi potongan 1-3 kata; dibaca satu per satu tidak
// terpakai. Blok disatukan sampai ada jeda jelas, atau blok jadi terlalu
// panjang untuk satu gelembung.
const JEDA = 0.6, MAKS_DETIK = 14, MAKS_HURUF = 240;

function kelompokkan(cs) {
  if (!gabung) return cs.map((c, i) => ({ ...c, idx: [i] }));
  const out = [];
  for (let i = 0; i < cs.length; i++) {
    const c = cs[i], t = (c.text || "").trim();
    const b = out[out.length - 1];
    const nyambung = b &&
      c.start - b.end <= JEDA &&
      c.end - b.start <= MAKS_DETIK &&
      (b.text.length + t.length) <= MAKS_HURUF;
    if (nyambung) { b.end = c.end; b.text = (b.text + " " + t).trim(); b.idx.push(i); }
    else out.push({ start: c.start, end: c.end, text: t, idx: [i] });
  }
  return out;
}
let tabAktif = "ringkasan";   // harus cocok dengan tab ber-class is-on di editor.html
const poll = new Map();

const jam = (t) => {
  if (!isFinite(t) || t < 0) t = 0;
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = Math.floor(t % 60);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
           : `${m}:${String(s).padStart(2, "0")}`;
};
const jamPenuh = (t) => {
  const d = Math.max(0, Math.floor(t));
  return `${String(Math.floor(d / 3600)).padStart(2, "0")}:${String(Math.floor((d % 3600) / 60)).padStart(2, "0")}:${String(d % 60).padStart(2, "0")}`;
};

function tandaiKotor(v) { kotor = v; dirtyTag.hidden = !v; saveBtn.disabled = !v; }

// ------------------------------------------------------------ daftar cue

function gambarDaftar() {
  baris = kelompokkan(cues);
  if (!baris.length) { list.innerHTML = `<li class="kosong">Tidak ada cue.</li>`; return; }
  list.innerHTML = baris.map((c, i) => `
    <li class="cue" data-i="${i}">
      <span class="jam" data-seek="${i}" title="Putar dari sini">${jamPenuh(c.start)}</span>
      <span class="spk" title="Pemisahan pembicara belum aktif">1</span>
      <textarea rows="1" data-i="${i}">${escapeHtml(c.text)}</textarea>
    </li>`).join("");
  list.querySelectorAll("textarea").forEach(tinggi);
  const h = document.getElementById("hitung");
  if (h) h.textContent = gabung
    ? `${baris.length} blok (dari ${cues.length} cue)`
    : `${baris.length} cue`;
}
function tinggi(ta) { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; }

list.addEventListener("input", (ev) => {
  const ta = ev.target.closest("textarea");
  if (!ta) return;
  baris[+ta.dataset.i].text = ta.value.replace(/\n/g, " ");
  tinggi(ta);
  ta.closest(".cue").classList.add("edited");
  tandaiKotor(true);
});
list.addEventListener("click", (ev) => {
  const j = ev.target.closest("[data-seek]");
  if (!j) return;
  lompat(baris[+j.dataset.seek].start);
  audio.play();
});
list.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") ev.target.blur();
  if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); ev.target.blur(); }
});

// -------------------------------------------------------------- gelombang

function gambarGelombang() {
  const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w) return;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  if (!peaks.length || !durasi) return;
  const css = getComputedStyle(document.body);
  const garis = css.getPropertyValue("--line").trim() || "#ddd";
  const aksen = css.getPropertyValue("--accent").trim() || "#c60";
  const posX = (t) => (t / durasi) * w, tengah = h / 2;
  baris.forEach((c, i) => {
    const x = posX(c.start);
    g.fillStyle = aksen + (i === aktif ? "33" : i % 2 ? "0d" : "16");
    g.fillRect(x, 3, Math.max(1, posX(c.end) - x), h - 6);
  });
  const per = w / peaks.length;
  for (let i = 0; i < peaks.length; i++) {
    const t = (i / peaks.length) * durasi;
    const dlm = aktif >= 0 && t >= baris[aktif].start && t <= baris[aktif].end;
    g.fillStyle = dlm ? aksen : garis;
    const tg = Math.max(1, peaks[i] * (h - 12));
    g.fillRect(i * per, tengah - tg / 2, Math.max(1, per - 0.3), tg);
  }
  g.fillStyle = aksen;
  g.fillRect(posX(audio.currentTime || 0) - 1, 0, 2, h);
}
canvas.addEventListener("click", (ev) => {
  if (!durasi) return;
  const r = canvas.getBoundingClientRect();
  lompat(((ev.clientX - r.left) / r.width) * durasi);
});

// ---------------------------------------------------------------- pemutar

function lompat(t) {
  audio.currentTime = Math.max(0, Math.min(t, durasi || audio.duration || t));
  sinkron();
}
function cariAktif(t) {
  for (let i = 0; i < baris.length; i++) if (t >= baris[i].start && t < baris[i].end) return i;
  return -1;
}
function sinkron() {
  const t = audio.currentTime, total = durasi || audio.duration || 0;
  $("#tNow").textContent = jam(t);
  $("#tEnd").textContent = jam(total);
  if (total) bar.value = Math.round((t / total) * 1000);

  if ($("#loop").checked && !audio.paused && aktif >= 0 && t >= baris[aktif].end) {
    lompat(baris[aktif].start); return;
  }
  const baru = cariAktif(t);
  if (baru !== aktif) {
    aktif = baru;
    list.querySelectorAll(".cue.active").forEach((e) => e.classList.remove("active"));
    if (aktif >= 0) {
      const li = list.querySelector(`.cue[data-i="${aktif}"]`);
      if (li) {
        li.classList.add("active");
        if ($("#auto").checked) {
          const r = li.getBoundingClientRect(), m = list.getBoundingClientRect();
          if (r.top < m.top + 30 || r.bottom > m.bottom - 30)
            li.scrollIntoView({ block: "center", behavior: "smooth" });
        }
      }
    }
  }
  gambarGelombang();
}
audio.addEventListener("timeupdate", sinkron);
audio.addEventListener("seeked", sinkron);
audio.addEventListener("loadedmetadata", () => {
  if (!durasi) durasi = audio.duration;
  $("#tEnd").textContent = jam(durasi);
  $("#durasi").textContent = jamPenuh(durasi);
  gambarGelombang();
});
audio.addEventListener("play", () => {
  $("#play").innerHTML = IKON.jeda;
  $("#play").setAttribute("aria-label", "Jeda");
});
audio.addEventListener("pause", () => {
  $("#play").innerHTML = IKON.putar;
  $("#play").setAttribute("aria-label", "Putar");
});
audio.addEventListener("error", () => { $("#tNow").textContent = "audio gagal"; });

$("#gabung").onchange = (e) => {
  if (kotor && !confirm("Ada perubahan belum disimpan. Ganti tampilan akan membuangnya. Lanjut?")) {
    e.target.checked = gabung; return;
  }
  gabung = e.target.checked;
  tandaiKotor(false);
  gambarDaftar();
  aktif = -1; sinkron();
};

$("#play").onclick = () => (audio.paused ? audio.play() : audio.pause());
$("#back10").onclick = () => lompat(audio.currentTime - 10);
$("#fwd10").onclick = () => lompat(audio.currentTime + 10);
$("#rate").onchange = (e) => { audio.playbackRate = +e.target.value; };
$("#vol").oninput = (e) => { audio.volume = e.target.value / 100; };
bar.oninput = (e) => { if (durasi) lompat((e.target.value / 1000) * durasi); };

function pindahCue(arah) {
  if (!baris.length) return;
  let i = aktif < 0 ? (arah > 0 ? 0 : baris.length - 1) : aktif + arah;
  i = Math.max(0, Math.min(i, baris.length - 1));
  lompat(baris[i].start);
  list.querySelector(`textarea[data-i="${i}"]`)?.focus();
}
document.addEventListener("keydown", (ev) => {
  const diTeks = ev.target.tagName === "TEXTAREA" || ev.target.tagName === "INPUT";
  if (ev.code === "Space" && !diTeks) { ev.preventDefault(); $("#play").click(); }
  if (ev.key === "Tab" && !ev.target.closest(".pane-ai") && !diTeks) { ev.preventDefault(); pindahCue(ev.shiftKey ? -1 : 1); }
  if ((ev.metaKey || ev.ctrlKey) && ev.key === "s") { ev.preventDefault(); simpan(); }
});

// ----------------------------------------------------------------- simpan

let sudahDiperingatkan = false;

async function simpan() {
  if (!kotor) return;
  if (gabung && !sudahDiperingatkan) {
    const n = cues.length, m = baris.length;
    if (m < n && !confirm(
        `Baris sedang digabung jadi blok ucapan. Menyimpan akan menulis ulang ` +
        `SRT dari ${n} cue menjadi ${m} blok — cocok untuk dibaca, tapi cue ` +
        `subtitle jadi lebih panjang.\n\nMatikan "Gabung ucapan" dulu bila ingin ` +
        `menjaga cue asli.\n\nLanjut menyimpan?`)) return;
    sudahDiperingatkan = true;
  }
  saveBtn.disabled = true; saveBtn.textContent = "Menyimpan…";
  try {
    const r = await fetch(`/api/jobs/${JOB}/cues`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(baris.map(({ start, end, text }) => ({ start, end, text }))),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    tandaiKotor(false);
    if (gabung) cues = baris.map(({ start, end, text }) => ({ start, end, text }));
    list.querySelectorAll(".edited").forEach((e) => e.classList.remove("edited"));
    saveBtn.textContent = "Tersimpan";
    setTimeout(() => { saveBtn.textContent = "Simpan"; }, 2000);
  } catch (e) {
    alert("Gagal menyimpan: " + e.message);
    saveBtn.disabled = false; saveBtn.textContent = "Simpan";
  }
}
saveBtn.onclick = simpan;
window.addEventListener("beforeunload", (e) => { if (kotor) e.preventDefault(); });
window.addEventListener("resize", gambarGelombang);

// ------------------------------------------------------------- panel AI

const API = { ringkasan: "summary", rapi: "tidy" };
const teksAI = { ringkasan: "", rapi: "" };
let riwayat = [];      // {role, content} untuk konteks lanjutan
let menunggu = false;

document.querySelectorAll(".tab").forEach((t) => {
  t.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("is-on", x === t));
    tabAktif = t.dataset.tab;
    tampilkanPanel();
    if (tabAktif === "chat") muatSaran();
    else muatAI(tabAktif);
  };
});

function tampilkanPanel() {
  const chat = tabAktif === "chat";
  $("#chat").hidden = !chat;
  $("#aiBody").hidden = chat;
  document.querySelectorAll(".ai-tools .ico").forEach((b) => { b.disabled = chat; });
  if (chat) $("#aiMeta").textContent = "";
}

// ---------------------------------------------------------------- chat

const chatIsi = $("#chatIsi"), tanya = $("#tanya");

// Penanda waktu di jawaban dibuat bisa diklik supaya pengguna langsung
// mendengar bagian yang dirujuk.
function tautkanJam(html) {
  return html.replace(/\[(\d{2}):(\d{2}):(\d{2})\]/g,
    (m, h, mi, d) => `<span class="jam-tautan" data-t="${(+h) * 3600 + (+mi) * 60 + (+d)}">${m}</span>`);
}

function tambahPesan(peran, teks, kelas = "") {
  const el = document.createElement("div");
  el.className = `pesan ${peran === "user" ? "saya" : "ai"} ${kelas}`.trim();
  el.innerHTML = peran === "user"
    ? `<div class="gel">${escapeHtml(teks)}</div>`
    : `<div class="gel">${tautkanJam(md(teks))}</div>`;
  chatIsi.appendChild(el);
  chatIsi.scrollTop = chatIsi.scrollHeight;
  return el;
}

chatIsi.addEventListener("click", (ev) => {
  const j = ev.target.closest(".jam-tautan");
  if (!j) return;
  lompat(+j.dataset.t);
  audio.play();
});

let saranDimuat = false;
async function muatSaran() {
  const kotak = $("#saran");
  if (!kotak || saranDimuat) return;
  saranDimuat = true;
  try {
    const d = await (await fetch(`/api/jobs/${JOB}/suggest`)).json();
    if (!d.items || !d.items.length) { kotak.remove(); return; }
    kotak.innerHTML = `<p class="saran-judul">Coba tanyakan:</p>` +
      d.items.map((q) => `<button class="saran-btn" type="button">${escapeHtml(q)}</button>`).join("");
    kotak.querySelectorAll(".saran-btn").forEach((b) => {
      b.onclick = () => kirimTanya(b.textContent);
    });
  } catch { kotak.remove(); }
}

async function kirimTanya(teks) {
  teks = (teks || "").trim();
  if (!teks || menunggu) return;
  $("#saran")?.remove();
  menunggu = true;
  $("#kirim").disabled = true;
  tanya.value = ""; tanya.style.height = "auto";

  tambahPesan("user", teks);
  const tunggu = document.createElement("div");
  tunggu.className = "pesan ai";
  tunggu.innerHTML = `<div class="gel mikir">Membaca transkrip</div>`;
  chatIsi.appendChild(tunggu);
  chatIsi.scrollTop = chatIsi.scrollHeight;

  try {
    const r = await fetch(`/api/jobs/${JOB}/chat`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: teks, history: riwayat }),
    });
    const d = await r.json();
    tunggu.remove();
    if (!r.ok) { tambahPesan("assistant", d.error || `HTTP ${r.status}`, "galat"); return; }
    riwayat.push({ role: "user", content: teks });
    riwayat.push({ role: "assistant", content: d.reply });
    tambahPesan("assistant", d.reply);
  } catch (e) {
    tunggu.remove();
    tambahPesan("assistant", `Gagal menghubungi server: ${e.message}`, "galat");
  } finally {
    menunggu = false;
    $("#kirim").disabled = false;
    tanya.focus();
  }
}

$("#chatForm").addEventListener("submit", (ev) => { ev.preventDefault(); kirimTanya(tanya.value); });
tanya.addEventListener("input", () => {
  tanya.style.height = "auto";
  tanya.style.height = Math.min(tanya.scrollHeight, 128) + "px";
});
tanya.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); kirimTanya(tanya.value); }
});

async function muatAI(tab, mulai = false) {
  const body = $("#aiBody"), meta = $("#aiMeta");
  const ep = API[tab];
  try {
    if (mulai) {
      const r = await fetch(`/api/jobs/${JOB}/${ep}`, { method: "POST" });
      const d = await r.json();
      if (!r.ok) { body.innerHTML = `<p class="kosong">${escapeHtml(d.error)}</p>`; return; }
    }
    const r = await fetch(`/api/jobs/${JOB}/${ep}`);
    const d = await r.json();
    if (tab !== tabAktif) return;   // pengguna sudah pindah tab

    if (d.status === "running") {
      body.innerHTML = `<p class="kosong">Memproses lewat DeepSeek… <b>${escapeHtml(d.stage || "")}</b></p>`;
      meta.textContent = "";
      clearTimeout(poll.get(tab));
      poll.set(tab, setTimeout(() => muatAI(tab), 2500));
      return;
    }
    if (d.status === "error") { body.innerHTML = `<p class="kosong">${escapeHtml(d.error)}</p>`; return; }
    if (d.status === "none") {
      meta.textContent = "";
      body.innerHTML = `<p class="kosong">Belum dibuat.
        <button data-mulai="${tab}">Buat sekarang</button></p>`;
      return;
    }

    const u = d.usage || {};
    meta.textContent = tab === "rapi"
      ? (u.words_before ? `${u.words_before} → ${u.words_after} kata` : "tersimpan")
      : (u.words ? `${u.words} kata · ${u.chunks} bagian` : "tersimpan");

    if (tab === "rapi") {
      // Isi penuh diambil dari berkasnya, bukan dari pratinjau di balasan
      // status — tidak ada alasan memaksa mengunduh untuk sekadar membaca.
      let teks = d.preview || "";
      try {
        const t = await fetch(`/api/jobs/${JOB}/tidy/text`);
        if (t.ok) teks = await t.text();
      } catch { /* gagal ambil isi penuh — pratinjau tetap ditampilkan */ }
      if (tab !== tabAktif) return;
      teksAI.rapi = teks;
      // Tanpa tombol unduh di badan panel: ikon unduh di toolbar sudah
      // melakukan hal yang sama persis.
      body.innerHTML = md(teks);
    } else {
      teksAI.ringkasan = d.text || "";
      body.innerHTML = md(d.text || "");
    }
  } catch (e) {
    body.innerHTML = `<p class="kosong">Gagal memuat: ${escapeHtml(e.message)}</p>`;
  }
}

$("#aiBody").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-mulai]");
  if (b) muatAI(b.dataset.mulai, true);
});

/** Salin teks ke papan klip. Kembalikan true bila berhasil.
 *
 * navigator.clipboard HANYA ada di konteks aman (HTTPS atau localhost). Saat
 * aplikasi dibuka dari komputer lain lewat http://192.168.x.x, API itu tidak
 * ada sama sekali — mengakses .writeText darinya melempar TypeError dan
 * membuat tombol tampak mati. Karena itu ada jalur cadangan execCommand yang
 * masih bekerja di konteks tidak aman.
 */
async function salinTeks(teks) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try {
      await navigator.clipboard.writeText(teks);
      return true;
    } catch { /* izin ditolak atau dokumen tidak fokus — coba cara lama */ }
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = teks;
    ta.setAttribute("readonly", "");
    // Di luar layar tapi tetap dapat difokuskan; iOS tidak menyalin dari
    // elemen display:none atau visibility:hidden.
    ta.style.cssText = "position:fixed;top:0;left:-9999px;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, teks.length);
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

/** Sorot isi panel AI agar pengguna bisa menyalin manual. */
function sorotIsiPanel() {
  const isi = $("#aiBody");
  if (!isi) return;
  const rentang = document.createRange();
  rentang.selectNodeContents(isi);
  const pilih = window.getSelection();
  pilih.removeAllRanges();
  pilih.addRange(rentang);
}

document.querySelector(".ai-tools").addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-act]");
  if (!b) return;
  const teks = teksAI[tabAktif];
  if (b.dataset.act === "salin") {
    if (!teks) return;
    const berhasil = await salinTeks(teks);
    if (berhasil) {
      b.innerHTML = IKON.centang;
      b.dataset.tip = "Tersalin";
      setTimeout(() => { b.innerHTML = IKON.salin; b.dataset.tip = "Salin ke papan klip"; }, 1200);
    } else {
      // Jangan gagal diam-diam: sorot teksnya supaya bisa disalin manual.
      sorotIsiPanel();
      b.dataset.tip = "Tidak bisa menyalin — teks sudah disorot, tekan Ctrl/Cmd+C";
      alert("Peramban menolak akses papan klip di koneksi ini.\n\n" +
            "Teksnya sudah disorot — tekan Ctrl+C (atau Cmd+C) untuk menyalin.");
    }
  }
  if (b.dataset.act === "unduh") {
    if (!teks) return;
    // Keduanya diunduh sebagai .docx dari server: siap disunting di Word,
    // tanpa perlu pengguna berurusan dengan Markdown.
    location.href = tabAktif === "rapi"
      ? `/api/jobs/${JOB}/tidy/download/docx`
      : `/api/jobs/${JOB}/summary/download/docx`;
  }
  if (b.dataset.act === "ulang") {
    if (confirm("Buat ulang? Ini memakai kuota DeepSeek dan mengirim transkrip ke server mereka.")) {
      muatAI(tabAktif, true);
    }
  }
});

// ------------------------------------------------- pengalih panel (sempit)
// Di layar sempit kedua panel tidak muat berdampingan, jadi ditumpuk dan
// dipilih lewat tombol. Pemutar tetap dipaku di bawah agar selalu terjangkau.
document.querySelectorAll(".alih-btn").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".alih-btn").forEach((x) => x.classList.toggle("is-on", x === b));
    const keTx = b.dataset.panel === "tx";
    document.body.classList.toggle("lihat-tx", keTx);
    // scrollHeight bernilai 0 selama elemen masih display:none, sehingga tinggi
    // textarea yang dihitung saat panel tersembunyi ikut kolaps jadi ~10px dan
    // teksnya terpotong. Hitung ulang setelah panelnya benar-benar tampil.
    if (keTx) list.querySelectorAll("textarea").forEach(tinggi);
    gambarGelombang();   // lebar kanvas berubah saat panel bertukar
  };
});

// ---------------------------------------------------------------- mulai

(async () => {
  const d = new Date((window.JOB_CREATED || Date.now() / 1000) * 1000);
  $("#tanggal").textContent = d.toLocaleString("id-ID",
    { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

  try {
    cues = await (await fetch(`/api/jobs/${JOB}/cues`)).json();
    gambarDaftar();
  } catch { list.innerHTML = `<li class="kosong">Gagal memuat cue.</li>`; }

  const minta = new URLSearchParams(location.search).get("tab");
  if (minta && (API[minta] || minta === "chat")) {
    tabAktif = minta;
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("is-on", x.dataset.tab === minta));
  }
  $("#play").innerHTML = IKON.putar;

  tampilkanPanel();
  // Saran pertanyaan memanggil DeepSeek, jadi ditunda sampai tab AI Chat
  // benar-benar dibuka — tab bawaan kini Ringkasan.
  if (tabAktif === "chat") muatSaran();
  else muatAI(tabAktif);

  try {
    const p = await (await fetch(`/api/jobs/${JOB}/peaks`)).json();
    if (p.peaks) { peaks = p.peaks; durasi = p.duration || durasi; }
  } catch { /* gelombang opsional */ }
  $("#durasi").textContent = jamPenuh(durasi || audio.duration || 0);
  gambarGelombang();
})();
