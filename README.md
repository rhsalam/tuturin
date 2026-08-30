# Transkrip Web

Antarmuka web untuk transkripsi audio & video ke teks. Transkripsi, gelombang,
dan penyuntingan berjalan sepenuhnya lokal di atas
[whisper.cpp](https://github.com/ggerganov/whisper.cpp).

**Satu pengecualian:** fitur AI — Chat, Ringkasan, dan Rapikan — mengirim isi
transkrip ke DeepSeek. Semuanya hanya berjalan atas tindakan Anda, tidak pernah
otomatis. Saran pertanyaan dibuat saat tab AI Chat pertama kali dibuka.
Bagian lain tidak pernah menghubungi internet selain saat mengunduh model.

## Prasyarat

```bash
brew install ffmpeg whisper-cpp
```

Model diunduh otomatis ke `~/.local/share/whisper-models` saat pertama dipakai.

## Menjalankan

```bash
./run.sh
```

Buka <http://127.0.0.1:5005>. Ganti port dengan `PORT=8000 ./run.sh`.

Secara bawaan server hanya mendengarkan di `127.0.0.1` — tidak terjangkau dari
komputer lain.

## HTTPS lokal (Caddy)

Peramban hanya memberikan **mikrofon, papan klip, dan passkey** pada "konteks
aman". Di HTTP biasa ketiganya mati — itulah sebabnya merekam dari ponsel
mustahil tanpa lapisan ini.

`Caddyfile` menjalankan Caddy di port 8443 sebagai reverse proxy ke Flask di
`127.0.0.1:5005`, dengan sertifikat dari CA lokal Caddy sendiri (`tls internal`).
Port 8443 dipilih supaya Caddy tidak perlu dijalankan sebagai root.

```bash
caddy run --config Caddyfile
```

### Mempercayai sertifikatnya (sekali saja)

Tanpa langkah ini peramban menolak membuka halaman.

**Di Mac** — perlu kata sandi Anda:

```bash
sudo caddy trust
```

**Di ponsel** — kirim `caddy-root.crt` ke perangkat (AirDrop paling mudah untuk
iPhone), lalu:

- iOS: Settings → Profile Downloaded → Install, kemudian
  Settings → General → About → Certificate Trust Settings → nyalakan sakelarnya.
- Android: Settings → Security → Encryption & credentials → Install a certificate
  → CA certificate.

Sesudah itu buka `https://192.168.1.23:8443`.

### Yang berubah setelah HTTPS aktif

- `.env` menyetel `HOST=127.0.0.1` — Flask tidak lagi menghadap jaringan
  langsung; Caddy yang menghadapinya. Alamat `http://192.168.1.23:5005` sengaja
  mati, supaya tidak ada lagi jalur tanpa enkripsi.
- `SECURE_COOKIE=1` menandai cookie sesi dan perangkat sebagai `Secure`.
- `ProxyFix` dipasang di `app.py`. Tanpa itu setiap permintaan tampak datang
  dari `127.0.0.1` sehingga pembatas percobaan akan mengunci semua orang
  sekaligus, dan `url_for` menghasilkan `http://` meski peramban memakai HTTPS.

Bila IP mesin berubah, perbarui daftar host di baris pertama blok `Caddyfile`.

## Rekam langsung

Ada dua sumber, keduanya memakai perekam dan antrean yang sama dengan berkas
unggahan — tidak ada jalur transkripsi terpisah.

**Rekam mikrofon** — dari mikrofon perangkat.

**Rekam audio tab** — menangkap suara yang keluar dari tab peramban, misalnya
video yang sedang diputar. Saat ditekan, peramban menampilkan kotak berbagi:

1. Pilih **tab** yang memutar videonya, bukan "Seluruh Layar".
2. Centang **"Also share tab audio"** di pojok kiri bawah kotak itu.

Bila audionya tidak ikut, aplikasi mengatakannya alih-alih merekam senyap.
Trek video langsung dibuang; hanya audionya yang direkam.

Menghentikan berbagi lewat bilah peramban ikut menutup rekaman, jadi tidak ada
rekaman yang menggantung.

**Batasnya di macOS:** berbagi "Seluruh Layar" tidak menyertakan suara sistem —
itu batasan macOS, bukan aplikasi ini. Jadi cara ini hanya menangkap audio dari
tab peramban. Untuk merekam suara aplikasi lain (Zoom, pemutar musik, berkas
video di luar peramban), diperlukan perangkat audio virtual seperti BlackHole
(`brew install blackhole-2ch`) yang dipasangkan dengan Multi-Output Device agar
suaranya tetap terdengar sambil ditangkap. Sesudah itu BlackHole muncul sebagai
perangkat masukan dan bisa direkam lewat tombol mikrofon.

- Format mengikuti dukungan peramban: `audio/webm;codecs=opus`, atau `audio/mp4`
  di Safari. Keduanya sudah ada di daftar ekstensi yang diterima.
- Selama merekam ditampilkan penghitung waktu dan meteran tingkat suara, supaya
  jelas mikrofonnya benar-benar menangkap suara.
- Data dipotong tiap detik agar rekaman panjang tidak menumpuk di memori.
- Transkripsi berjalan **setelah** rekaman dihentikan, bukan sambil berbicara.
  Whisper yang melihat konteks utuh jauh lebih akurat daripada yang menebak dari
  potongan beberapa detik.

Bila dibuka lewat alamat tanpa HTTPS, tombolnya mati dengan keterangan alasannya
— bukan gagal diam-diam saat ditekan.

## Membuka ke jaringan

Tambahkan ke `.env`:

```
HOST=0.0.0.0
APP_PASSWORD=sandi-pilihan-anda
```

Saat mulai, alamat yang bisa dipakai komputer lain akan dicetak, misalnya
`http://192.168.1.23:5005`.

### Masuk sekali per perangkat

Halaman masuk punya kotak **"Ingat perangkat ini selama 90 hari"** yang menyala
secara bawaan. Bila dicentang, server menerbitkan token perangkat sehingga sandi
tidak diminta lagi di perangkat itu — beban terbesar pemakaian harian, terutama
mengetik frasa panjang di ponsel.

Yang disimpan di `perangkat.json` adalah **hash** token, bukan tokennya. Berkas itu
bocor pun tidak bisa dipakai untuk masuk.

Halaman **/perangkat** menampilkan seluruh perangkat yang diingat beserta label
(mis. "Safari di iPhone"), kapan pertama masuk, dan kapan terakhir dipakai. Tiap
perangkat bisa dicabut sendiri-sendiri, atau sekaligus lewat "Cabut semua kecuali
perangkat ini". **Keluar** juga mencabut perangkat yang sedang dipakai.

Inilah penyeimbang token berumur panjang: kalau ponsel hilang, cabut dari laptop
tanpa mengganggu perangkat lain.

### Pembatasan percobaan

Lima kegagalan dari satu alamat IP mengunci login selama 5 menit; kegagalan
berikutnya menggandakan durasinya hingga maksimum 1 jam. Berhasil masuk mereset
hitungannya. Pembatas ini ada di memori, jadi ikut hilang saat server dimulai ulang.

### Batas yang tidak bisa ditutup tanpa HTTPS

Di HTTP, sandi **dan** token perangkat melintas tanpa enkripsi. Siapa pun yang bisa
menyadap jaringan yang sama dapat menyalin token dan memakainya — dan token berumur
90 hari justru lebih berharga bila tercuri. Halaman pencabutan adalah penanggulangan,
bukan pencegahan.

Hanya HTTPS yang menutup celah ini. HTTPS juga akan menghidupkan papan klip bawaan
peramban (lihat catatan tentang tombol Salin) dan membuka jalan ke passkey — masuk
dengan sidik jari atau Face ID, yang mustahil selama masih HTTP.

**Kata sandi wajib.** Bila `HOST` bukan localhost dan `APP_PASSWORD` kosong,
server menolak jalan dan menjelaskan alasannya. Itu disengaja: tanpa gerbang,
siapa pun yang bisa mencapai port ini dapat membaca dan menghapus seluruh
transkrip Anda, mengunggah berkas, serta menghabiskan kuota DeepSeek Anda —
kunci API tersimpan di server, jadi mereka tidak melihatnya tetapi tetap bisa
memakainya. Untuk jaringan yang benar-benar tepercaya, `ALLOW_NO_AUTH=1`
melewati penolakan itu; tidak disarankan.

Yang perlu diingat:

- **Lalu lintasnya HTTP polos.** Kata sandi dan isi transkrip melintas tanpa
  enkripsi di jaringan lokal. Jangan dipakai di Wi-Fi publik.
- **Satu kata sandi untuk semua.** Tidak ada akun per orang; siapa pun yang
  tahu sandinya punya akses penuh, termasuk menghapus.
- **Server pengembangan Flask.** Cukup untuk beberapa orang di satu kantor.
  Untuk lebih dari itu, jalankan di belakang gunicorn dan TLS.
- **Jangan diteruskan ke internet.** Ini dirancang untuk jaringan lokal saja.

Ganti kata sandi kapan saja dengan menyunting `APP_PASSWORD` di `.env` lalu
memulai ulang server. Menekan **Keluar** mengakhiri sesi di peramban.

Kembali ke mode hanya-lokal: hapus baris `HOST` dari `.env`.

## Halaman depan

Halaman depan adalah **pustaka**: panel unggah yang bisa dilipat, lalu daftar
seluruh transkrip.

Tiap kartu dibagi menurut peran isinya:

- **Kiri — informasi.** Nama berkas, durasi, tanggal, bahasa, model, kecepatan
  proses, lalu tautan **Ringkasan** dan **Transkrip rapi** yang bertanda centang
  bila sudah dibuat. Tautan ini membuka tab yang bersangkutan di editor.
- **Kanan — tindakan.** Lencana status, satu tombol utama **Buka**, dan menu
  **⋯** berisi sisanya.

Isi menu dikelompokkan menurut asal berkasnya, bukan sekadar berderet:

| Kelompok | Isi |
|---|---|
| Unduh transkrip | Teks (TXT), Subtitle (SRT), dan format lain yang dipilih |
| Unduh dokumen | Ringkasan (DOCX), Transkrip rapi (DOCX), Transkrip rapi (MD) |
| Kelola | Hapus transkrip ini |

Menu menutup sendiri saat klik di luar, saat menekan Escape, atau saat menu lain
dibuka.

**Catatan bagi yang menyunting kode ini:** `gambar()` menggambar ulang seluruh
daftar tiap beberapa detik dan mengganti elemen kartunya. Fungsi itu sengaja
tidak berbuat apa-apa selama ada menu yang terbuka — tanpa penjaga tersebut,
menu terbanting tutup di tengah pemakaian.

Ada kotak pencarian nama berkas, dan ringkasan jumlah transkrip beserta total
durasi audio di pojok kanan atas.

## Folder

Folder di sini adalah **label**, bukan direktori. Tidak ada berkas yang
dipindahkan di disk — hanya cara mengelompokkan tampilan. Konsekuensinya:
mengganti nama folder tidak berisiko merusak apa pun, dan berkas hasil tetap
di `outputs/<id>/` seperti biasa.

- Isian **Folder** di panel unggah menempatkan berkas baru langsung ke folder.
  Kotaknya menyarankan nama yang sudah ada, supaya "IIBF" dan "iibf" tidak
  menjadi dua folder berbeda.
- Menu **⋯** pada kartu punya **Masukkan ke folder…** / **Pindahkan folder…**.
  Mengosongkan isiannya mengeluarkan transkrip dari folder.
- Bilah kelompok muncul di atas daftar begitu ada minimal satu folder, lengkap
  dengan jumlah isinya: `Semua`, tiap folder, lalu `Tanpa folder`.
- Penyaring folder dan kotak pencarian bekerja bersamaan.

Nama folder disimpan di `outputs/<id>/job.json`, jadi bertahan setelah server
dimulai ulang.

### Ringkasan dan Rapikan pindah ke editor

Sebelumnya kedua fitur AI bisa dijalankan dan dibaca langsung di kartu. Itu
membuat kartu menumpuk enam tombol dan menduplikasi logika yang sudah ada di
editor. Sekarang keduanya hanya hidup di panel kiri editor; kartu cukup
menunjukkan status dan menautkan ke sana.

## Fitur

- Unggah beberapa berkas sekaligus, seret-lepas atau pilih manual
- Audio **dan** video (MP4, MKV, MOV, WEBM) — trek video dibuang otomatis
- Deteksi bahasa dari cuplikan **di tengah** rekaman, bukan 30 detik pertama;
  rekaman seminar sering diawali musik intro yang membuat deteksi meleset
- Progres langsung dengan pratinjau kalimat yang sedang ditranskripsi
- Output TXT, SRT, VTT, JSON — unduh atau baca langsung di halaman
- Pembersihan otomatis halusinasi penutup (lihat di bawah)
- **Editor validasi**: dengarkan audio sambil membaca cue, koreksi langsung
- **Ringkasan AI** lewat DeepSeek (opsional, perlu kunci API)
- **Rapikan AI**: transkrip utuh yang dibersihkan, keluar sebagai .md dan .docx

## Ringkasan AI

Tombol **Ringkasan AI** pada kartu job mengirim transkrip ke DeepSeek dan
mengembalikan ringkasan berstruktur: topik utama, argumen dan poin penting,
lalu urutan pembahasan.

Siapkan kunci di berkas `.env` pada folder ini (sudah masuk `.gitignore`):

```
DEEPSEEK_API_KEY=sk-xxxxxxxx
```

Bisa juga lewat variabel lingkungan dengan nama yang sama. Jangan menaruh kunci
di dalam kode.

Transkrip panjang dipotong per 5.000 kata dengan tumpang tindih, diringkas
bertahap, lalu digabung. Rekaman 51 menit (5.634 kata) memakai 2 bagian dan
sekitar 17.000 token; rekaman 108 menit memakai 3 bagian, sekitar 43 detik.
Hasil disimpan di `outputs/<id>/summary.md` sehingga tidak dibuat ulang tanpa
diminta.

## Rapikan AI

Tombol **Rapikan AI** berbeda dari Ringkasan: ia **tidak memangkas isi**.
Seluruh transkrip dikembalikan utuh, hanya dibersihkan.

Yang diperbaiki:
- Tanda baca dan huruf besar.
- Kata sisipan: eh, anu, gitu, ya kan, apa namanya.
- Pengulangan kata yang jelas salah rekam.
- Ejaan istilah: insya Allah, Subhanahu wa Ta'ala, rezeki, omzet, cashflow.

Yang dijaga agar tidak berubah: gaya bicara lisan. Kata seperti *banget*,
*aja*, *enggak*, *kok*, *udah* dipertahankan apa adanya — merapikan bukan
memformalkan.

Keluarannya berisi kepala dokumen (judul, sumber, durasi, bahasa, jumlah kata),
6 butir poin utama, lalu isi lengkap dengan sub-judul per topik. Penanda waktu
`[00:08:21]` disisipkan tiap ±1 menit dari SRT dan memulai setiap paragraf.

### Bentuk dijamin di luar model

Model bahasa tidak konsisten menaati aturan bentuk. Pada pengujian ia
menghasilkan paragraf rata-rata 21 kalimat (diminta 3–6) dengan 16 penanda
waktu terselip di tengah. Karena itu pembagian paragraf dan pembuangan sisipan
dikerjakan oleh kode setelah model selesai, bukan diserahkan ke prompt:

- `paragrafkan()` memecah tiap paragraf jadi maksimal 6 kalimat dan memastikan
  semuanya diawali penanda waktu.
- `buang_sisipan()` membuang pengisi jeda di akhir klausa, dengan daftar
  lindung agar "kayak gitu" dan "seperti gitu" tidak ikut terbuang.

Hasil pada ceramah 51 menit: 5.634 → 5.421 kata (−3,8%), "gitu" turun 88%,
178 paragraf rata-rata 4,8 kalimat, semuanya berpenanda waktu, nol yang
terselip. Kosakata lisan tetap utuh.

**Yang perlu diwaspadai:** model kadang "memperbaiki" nama yang salah dengar
memakai pengetahuan umumnya. Itu sering menolong — `Raja Darugamas` menjadi
`Raja Garuda Mas` — tapi bisa juga menghasilkan tebakan yang salah dan terdengar
meyakinkan. Perlakukan nama, angka, dan kutipan dalam ringkasan sebagai bahan
yang masih perlu Anda periksa terhadap audio.

## Editor validasi

Tombol **Periksa & koreksi** pada kartu job membuka ruang kerja dua panel:
keluaran AI di kiri, transkrip dan pemutar di kanan.

**Panel kiri** — tab *Ringkasan* (bawaan), *AI Chat*, dan *Transkrip Rapi*.
Tab Ringkasan dan Transkrip Rapi punya tombol salin, unduh, dan buat ulang.

Tombol unduh menghasilkan **.docx** untuk keduanya — siap disunting di Word,
tanpa perlu berurusan dengan Markdown. Berkas ringkasan dirender ulang tiap
diminta, jadi selalu mengikuti ringkasan terbaru.

Saran pertanyaan pada tab AI Chat baru diminta ketika tab itu dibuka, bukan saat
editor dimuat. Membuka editor karena itu tidak memakai kuota DeepSeek.

**Panel kanan** — daftar ucapan bertautan waktu:
- Klik penanda waktu untuk memutar dari titik itu
- Baris yang sedang berbunyi tersorot dan tergulir sendiri (*Gulir Otomatis*)
- Sunting teks langsung; simpan menulis ulang SRT, TXT, dan VTT sekaligus
- Gelombang audio dengan blok cue; klik untuk melompat
- Pemutar: mundur/maju 10 detik, volume, kecepatan 0,5×–2×, *Ulang cue*

### AI Chat

Bertanya langsung kepada isi rekaman. Saat dibuka, tiga **saran pertanyaan**
dibuat dari transkrip itu sendiri — bukan pertanyaan umum. Contoh keluaran pada
ceramah "4 Tingkatan Rejeki":

> Siapa nama orang kaya di New York yang disebut sebagai 'The unexpected hero'
> karena membagikan roti saat krisis ekonomi?

Saran disimpan di `outputs/<id>/suggest.json` sehingga tidak dibuat ulang.

Jawaban menyertakan penanda waktu seperti `[00:20:22]`, dan penanda itu **bisa
diklik** untuk melompatkan pemutar sekaligus menyorot barisnya di panel kanan.

Model diarahkan menjawab hanya dari transkrip. Pertanyaan di luar isi rekaman
dijawab dengan penolakan terus terang, bukan dikarang. Riwayat percakapan ikut
dikirim sehingga pertanyaan lanjutan seperti "di menit berapa dia dibahas?"
tetap dipahami.

Riwayat chat hanya hidup selama halaman terbuka — memuat ulang halaman
mengosongkannya.

### Gabung ucapan

Whisper memecah ucapan jadi potongan 1–3 kata — satu ceramah 51 menit
menghasilkan 2.179 cue, yang tidak terbaca sebagai teks. Sakelar
**Gabung ucapan** (menyala secara bawaan) menyatukannya menjadi blok saat ada
jeda di bawah 0,6 detik, sampai batas 14 detik atau 240 huruf. Hasilnya 240
blok, bukan 2.179 baris.

Perlu diperhatikan: menyimpan dalam mode gabung menulis ulang SRT menjadi blok
panjang tersebut. Bagus untuk dibaca, kurang cocok sebagai subtitle. Aplikasi
meminta konfirmasi sekali sebelum melakukannya; matikan sakelarnya bila ingin
menjaga cue asli.

### Tablet dan ponsel

Di bawah 900px kedua panel tidak muat berdampingan, jadi tata letaknya berubah:

- Muncul pengalih **AI / Transkrip** di paling atas; hanya satu panel tampil.
- **Pemutar dipaku ke bawah layar** dan tetap terlihat dari panel mana pun.
  Sebelum diperbaiki, pemutar berada di y=1151 pada layar setinggi 812 — untuk
  menekan tombol putar pengguna harus menggulir melewati seluruh transkrip.
- Tinggi memakai `100dvh`, bukan `100vh`, karena bilah alamat peramban seluler
  menyusut saat digulir dan `100vh` menyisakan bagian yang tertutup.

Jebakan yang sempat menjerat: pemutar berada **di dalam** panel transkrip.
Menyembunyikan panel itu dengan `display: none` ikut mematikan pemutar meskipun
ia `position: fixed` — elemen fixed tidak lolos dari induk yang `display: none`.
Karena itu yang disembunyikan adalah anak-anak panel, bukan panelnya.

### Target sentuh

Aturan ukuran tombol memakai `@media (max-width: 900px), (pointer: coarse)`.
Bagian `pointer: coarse` penting: iPad lanskap berukuran 1024px sehingga lolos
dari ambang lebar, tetapi tetap perangkat sentuh. Diuji — pada mode sentuh
ikon membesar dari 26px menjadi 42px.

Kotak teks memakai `font-size: 1rem` (16px) agar iOS tidak memperbesar halaman
saat kotak difokuskan.

### Pemisahan pembicara belum ada

Whisper tidak memisahkan pembicara. Kolom pembicara ada di antarmuka tetapi
seluruh baris ditandai sebagai satu orang, dan hal itu dinyatakan terbuka di
atas daftar. Diarisasi sungguhan memerlukan model terpisah seperti
`pyannote.audio`.

Pintasan: **Spasi** putar/jeda · **Tab** cue berikutnya · **Shift+Tab** sebelumnya
· **Esc** keluar dari kotak teks · **Cmd/Ctrl+S** simpan.

Riwayat job disimpan di `outputs/<id>/job.json`, jadi editor tetap bisa dibuka
setelah server dimulai ulang.

## Halusinasi penutup

Whisper kerap menempelkan frasa penutup video di ujung transkrip — misalnya
"Terima kasih telah menonton!" atau "Sub indo by ..." — karena data latihnya
banyak berisi subtitle YouTube. Frasa ini **tidak ada di audio**.

Aplikasi membuang segmen terakhir bila cocok pola tersebut, dan menampilkan
catatan berisi teks yang dibuang supaya keputusannya bisa Anda periksa. Lihat
`HALLUCINATION_TAIL` di `transcriber.py` untuk menambah pola.

## Batasan yang perlu diketahui

- **Antrean satu-pekerja.** Whisper memakai seluruh core CPU, jadi menjalankan
  dua transkripsi bersamaan justru memperlambat keduanya. Berkas diproses
  berurutan.
- **Ringkasan dan Rapikan tidak gratis dan tidak lokal.** Keduanya memakai kuota
  DeepSeek Anda dan mengirim seluruh transkrip ke server mereka. Rapikan jauh
  lebih mahal karena keluarannya sepanjang masukan: ceramah 51 menit memakai
  ~37.000 token dan 80 detik.
- **Penanda waktu berulang.** Paragraf pecahan dalam menit yang sama memakai
  penanda yang sama, jadi `[00:00:00]` bisa muncul beberapa kali berturut-turut.
- **Menyunting cue tidak mengubah JSON.** Format JSON ditulis sekali oleh
  whisper dan tidak ikut diperbarui saat Anda menyimpan suntingan; SRT, TXT,
  dan VTT ikut.
- **Nama orang dan istilah asing sering meleset.** Ini batas model, bukan bug —
  kalimat percakapan biasa akurat, tapi nama diri perlu koreksi manual.
- **Server pengembangan Flask.** Cukup untuk pemakaian pribadi di satu mesin.
  Untuk dipakai banyak orang, jalankan di belakang WSGI seperti gunicorn.

## Struktur

```
app.py            Rute HTTP dan validasi unggahan
transcriber.py    Antrean job, konversi ffmpeg, pemanggilan whisper-cli
templates/        Halaman utama
static/           Gaya dan logika antarmuka
uploads/          Berkas mentah (dihapus saat job dihapus)
cues.py           Baca-tulis SRT dan perhitungan gelombang
summarize.py      Ringkasan DeepSeek
tidy.py           Perapian transkrip + keluaran .docx
chat.py           Tanya jawab dan saran pertanyaan
.env              Kunci API — tidak ikut ke git
outputs/<id>/     Hasil transkrip, job.json, cache gelombang
```

## API

| Metode   | Rute                              | Keterangan                    |
|----------|-----------------------------------|-------------------------------|
| `GET`    | `/api/jobs`                       | Daftar job                    |
| `POST`   | `/api/jobs`                       | Unggah; field: `file`, `lang`, `model`, `formats` |
| `GET`    | `/api/jobs/<id>`                  | Status satu job               |
| `GET`    | `/api/jobs/<id>/text/<fmt>`       | Isi transkrip sebagai teks    |
| `GET`    | `/api/jobs/<id>/download/<fmt>`   | Unduh berkas                  |
| `DELETE` | `/api/jobs/<id>`                  | Hapus job dan berkasnya       |
| `GET`    | `/api/folders`                    | Nama folder dan jumlah isinya |
| `PUT`    | `/api/jobs/<id>/folder`           | Pindahkan job ke folder       |
| `GET`    | `/jobs/<id>/editor`               | Halaman editor validasi       |
| `GET`    | `/api/jobs/<id>/audio`            | Berkas sumber (dukung Range)  |
| `GET`    | `/api/jobs/<id>/peaks`            | Data gelombang (di-cache)     |
| `GET`    | `/api/jobs/<id>/cues`             | Cue sebagai JSON              |
| `PUT`    | `/api/jobs/<id>/cues`             | Simpan cue hasil suntingan    |
| `POST`   | `/api/jobs/<id>/summary`          | Mulai ringkasan (asinkron)    |
| `GET`    | `/api/jobs/<id>/summary`          | Status atau hasil ringkasan   |
| `GET`    | `/api/jobs/<id>/summary/download/docx` | Ringkasan sebagai .docx  |
| `DELETE` | `/api/jobs/<id>/summary`          | Hapus ringkasan tersimpan     |
| `POST`   | `/api/jobs/<id>/tidy`             | Mulai perapian (asinkron)     |
| `GET`    | `/api/jobs/<id>/tidy`             | Status atau pratinjau         |
| `GET`    | `/api/jobs/<id>/tidy/download/<md\|docx>` | Unduh hasil rapi     |
| `DELETE` | `/api/jobs/<id>/tidy`             | Hapus hasil rapi              |
| `GET`    | `/api/jobs/<id>/suggest`          | Tiga saran pertanyaan         |
| `POST`   | `/api/jobs/<id>/chat`             | Tanya jawab atas transkrip    |

Contoh:

```bash
curl -X POST http://127.0.0.1:5005/api/jobs \
  -F "file=@rapat.mp3" -F "lang=id" -F "formats=txt" -F "formats=srt"
```

## Alternatif baris perintah

Untuk pemakaian tanpa browser tersedia `transkrip` di `~/.local/bin`:

```bash
transkrip ~/Downloads/rekaman.mp3
```
