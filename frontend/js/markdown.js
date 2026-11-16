// 一个够用的 Markdown 渲染器（不依赖第三方库）：先转义，再识别块级与行内语法。
const escapeHtml = (text) => text.replace(/[&<>"]/g, (char) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char]));

function inline(text) {
  const codes = [];
  let out = escapeHtml(text).replace(/`([^`]+)`/g, (_, code) => {
    codes.push(`<code>${code}</code>`);
    return `\u0000${codes.length - 1}\u0000`;
  });
  out = out
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return out.replace(/\u0000(\d+)\u0000/g, (_, index) => codes[Number(index)]);
}

const isTableRow = (line) => /^\s*\|.*\|\s*$/.test(line);
const cells = (line) => line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());

export function renderMarkdown(source) {
  const lines = String(source || "").replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const fence = line.match(/^\s*```\s*([\w+-]*)\s*$/);
    if (fence) {
      const body = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) body.push(lines[index++]);
      index += 1;
      const lang = fence[1] ? ` data-lang="${escapeHtml(fence[1])}"` : "";
      html.push(`<pre${lang}><code>${escapeHtml(body.join("\n"))}</code></pre>`);
      continue;
    }
    if (!line.trim()) { index += 1; continue; }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      html.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`);
      index += 1;
      continue;
    }
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { html.push("<hr>"); index += 1; continue; }
    if (/^\s*>/.test(line)) {
      const quote = [];
      while (index < lines.length && /^\s*>/.test(lines[index])) quote.push(lines[index++].replace(/^\s*>\s?/, ""));
      html.push(`<blockquote>${renderMarkdown(quote.join("\n"))}</blockquote>`);
      continue;
    }
    if (isTableRow(line) && index + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[index + 1]) && lines[index + 1].includes("-")) {
      const head = cells(line);
      index += 2;
      const rows = [];
      while (index < lines.length && isTableRow(lines[index])) rows.push(cells(lines[index++]));
      html.push(`<table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${
        rows.map((row) => `<tr>${row.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }
    const bullet = /^\s*[-*+]\s+/;
    const ordered = /^\s*\d+[.)]\s+/;
    if (bullet.test(line) || ordered.test(line)) {
      const pattern = bullet.test(line) ? bullet : ordered;
      const tag = pattern === bullet ? "ul" : "ol";
      const items = [];
      while (index < lines.length && (pattern.test(lines[index]) || (/^\s{2,}\S/.test(lines[index]) && items.length))) {
        if (pattern.test(lines[index])) items.push(lines[index].replace(pattern, ""));
        else items[items.length - 1] += " " + lines[index].trim();
        index += 1;
      }
      html.push(`<${tag}>${items.map((item) => `<li>${inline(item)}</li>`).join("")}</${tag}>`);
      continue;
    }
    const paragraph = [];
    while (index < lines.length && lines[index].trim() && !/^\s*```/.test(lines[index])
      && !/^#{1,4}\s/.test(lines[index]) && !/^\s*>/.test(lines[index])
      && !bullet.test(lines[index]) && !ordered.test(lines[index])) {
      paragraph.push(lines[index++]);
    }
    html.push(`<p>${paragraph.map(inline).join("<br>")}</p>`);
  }
  return html.join("");
}
