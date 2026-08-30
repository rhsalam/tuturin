// Perender Markdown minimal, dipakai bersama halaman daftar dan editor.
// Escape dijalankan LEBIH DULU, baru pemformatan diterapkan pada teks yang
// sudah aman — sehingga keluaran model tidak bisa menyuntikkan HTML.
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function md(t) {
  const baris = escapeHtml(t).split("\n");
  let html = "", diList = false, diQuote = false;
  const inline = (s) => s
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  const tutup = () => {
    if (diList) { html += "</ul>"; diList = false; }
    if (diQuote) { html += "</blockquote>"; diQuote = false; }
  };
  for (const b of baris) {
    const l = b.trim();
    if (!l) { tutup(); continue; }
    if (l === "---") { tutup(); html += "<hr>"; continue; }
    if (l.startsWith("&gt;")) {
      if (!diQuote) { tutup(); html += "<blockquote>"; diQuote = true; }
      html += `<p>${inline(l.replace(/^&gt;\s*/, ""))}</p>`;
      continue;
    }
    const li = l.match(/^[-*]\s+(.*)$/) || l.match(/^\d+\.\s+(.*)$/);
    if (li) {
      if (!diList) { tutup(); html += "<ul>"; diList = true; }
      html += `<li>${inline(li[1])}</li>`;
      continue;
    }
    tutup();
    const h = l.match(/^(#{1,4})\s+(.*)$/);
    if (h) html += `<h${h[1].length + 2}>${inline(h[2])}</h${h[1].length + 2}>`;
    else html += `<p>${inline(l)}</p>`;
  }
  tutup();
  return html;
}
