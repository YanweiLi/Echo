#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A4 打印版生成：每周精选 HTML + Chrome/Edge 无头导出 PDF。

CLI（tools/epub2md.py）与 App UI（src/player.py）共用同一套排版。

文章字典约定：
    {
      "title": str, "section": str, "date": str, "standfirst": str,
      "paragraphs": [[句子, …], …],          # 按段落分组
      "glossary": [{"word", "ipa", "zh"}, …]  # 按首次出现顺序，正文按此编号
    }
"""

import html
import re
import shutil
import subprocess
from pathlib import Path

# Leaders 优先，其余栏目按下面的顺序各取首篇
WEEK_SECTIONS = [
    "Leaders", "Briefing", "United States", "Finance & economics",
    "Business", "Science & technology", "International", "Europe",
    "Britain", "Asia", "China", "The Americas", "Middle East & Africa",
    "Culture", "By Invitation",
]
WEEK_EXCLUDE = {"The world this week", "Letters", "Obituary", "Obituaries"}

PRINT_WEEK_STYLE = """<style>
  @page { size: A4; margin: 16mm 15mm 16mm 18mm; }
  html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  body { margin:0; color:#111;
         font: 10.5pt/1.55 Georgia, "Times New Roman", "Songti SC", serif; }
  .wrap { max-width: 176mm; margin: 0 auto; }

  .cover { min-height: 240mm; display:flex; flex-direction:column;
           break-after: page; page-break-after: always; }
  .kicker { font-size:9pt; letter-spacing:.18em; text-transform:uppercase;
            color:#555; margin:0 0 4mm; }
  h1.issue { font-size:24pt; line-height:1.2; margin:0 0 2mm; letter-spacing:-.01em; }
  .issue-meta { color:#555; font-size:10pt; margin:0 0 8mm; }
  .toc { list-style:none; padding:0; margin:0; border-top:1px solid #222; }
  .toc li { display:flex; align-items:baseline; gap:3mm; padding:2.2mm 0;
            border-bottom:.4pt solid #ccc; font-size:10.5pt; break-inside:avoid; }
  .toc .chk { width:4mm; }
  .toc .tno { color:#777; width:8mm; font-variant-numeric:tabular-nums; }
  .toc .tsec { color:#666; width:34mm; font-size:9pt; text-transform:uppercase;
               letter-spacing:.06em; }
  .toc .ttitle { flex:1; }
  .toc .tsent { color:#777; font-size:9pt; white-space:nowrap; }
  .howto { margin-top:auto; font-size:9pt; color:#555; border:.5pt solid #bbb;
           padding:4mm; line-height:1.6; }

  article { break-before: page; page-break-before: always; }
  .art-head { border-bottom:1.2pt solid #111; padding-bottom:2.5mm; margin-bottom:4mm; }
  .art-meta { font-size:8.5pt; color:#555; letter-spacing:.08em;
              text-transform:uppercase; margin:0 0 2mm; }
  .art-title { font-size:17pt; line-height:1.25; margin:0 0 2mm; }
  .art-standfirst { font-style:italic; color:#333; font-size:10.5pt; margin:0; }
  .art-body { padding-right:24mm; border-right:.5pt dotted #bbb; }
  .art-body p { margin:0 0 2.2mm; text-align:justify; orphans:3; widows:3; }
  .w { font-weight:700; text-decoration:underline dotted #555; text-underline-offset:2px; }
  .w sup { font-size:.62em; font-weight:400; color:#444; }
  .sn { font-size:.58em; color:#999; margin-left:1px; }

  .gloss { margin-top:5mm; }
  .gloss h3 { font-size:10pt; margin:0 0 2mm; padding-top:2mm; border-top:.8pt solid #111; }
  .gloss h4.gh { font-size:8.6pt; font-weight:700; letter-spacing:.06em; color:#333;
                 margin:0 0 1.2mm; break-after:avoid; page-break-after:avoid; }
  .gloss h4.gh .gc { font-weight:400; color:#888; margin-left:2mm; letter-spacing:0; }
  .gloss .gloss-cols + h4.gh { margin-top:2.4mm; }
  .gloss-cols { columns:3; column-gap:5mm; }
  .gi { break-inside:avoid; page-break-inside:avoid; margin:0 0 1.3mm;
        font-size:8.2pt; line-height:1.3; }
  .gn { display:inline-block; min-width:5mm; color:#777;
        font-variant-numeric:tabular-nums; }
  .ipa { color:#555; }
  .zh { color:#222; }
  @media print {
    body { font-size:10.5pt; }
    a { color:inherit; text-decoration:none; }
  }
</style>
"""


def esc(s) -> str:
    return html.escape(str(s or ""))


def _art_no(a) -> int:
    return int(a.get("idx", a.get("ai", 0)) or 0)


def pick_week_articles(articles, picks=(), max_articles: int = 8,
                       max_sentences: int = 300):
    """Leaders 优先 + 各栏目首篇，凑 6–8 篇；或按篇号指定。"""
    if picks:
        wanted = set(picks)
        chosen = [a for a in articles if _art_no(a) in wanted]
    else:
        chosen = [a for a in articles if a.get("section") == "Leaders"][:5]
        for sec in WEEK_SECTIONS:
            if len(chosen) >= max_articles:
                break
            if sec == "Leaders":
                continue
            cand = next((a for a in articles if a.get("section") == sec
                         and not any(a is c for c in chosen)), None)
            if cand:
                chosen.append(cand)
        for a in articles:
            if len(chosen) >= max_articles:
                break
            if a.get("section") in WEEK_EXCLUDE or any(a is c for c in chosen):
                continue
            chosen.append(a)

    chosen.sort(key=_art_no)
    picked, total = [], 0
    for a in chosen:
        n = len(a["sentences"])
        if picked and (len(picked) >= max_articles or total + n > max_sentences):
            continue
        picked.append(a)
        total += n
    return picked[:max_articles]


def merge_paragraphs(paragraphs, marks=None, target_words: int = 70):
    """一句一段的数据（EPUB 导出的 md）→ 按词数合并成自然段，便于整页阅读。

    传了 marks（与 paragraphs 同形的标注）就一起合并，保证对齐。
    """
    if not paragraphs or any(len(p) != 1 for p in paragraphs):
        return (paragraphs, marks) if marks is not None else paragraphs
    merged, merged_marks, buf, buf_marks, words = [], [], [], [], 0
    for i, para in enumerate(paragraphs):
        sent = para[0]
        buf.append(sent)
        if marks is not None:
            buf_marks.append(marks[i][0] if i < len(marks) and marks[i] else [])
        words += len(sent.split())
        if words >= target_words:
            merged.append(buf)
            merged_marks.append(buf_marks)
            buf, buf_marks, words = [], [], 0
    if buf:
        merged.append(buf)
        merged_marks.append(buf_marks)
    if marks is None:
        return merged
    return merged, merged_marks


TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")

# 档位标题与顺序（与 src/vocab.py 一致，这里只用于排版）
GROUP_TITLES = {4: "CET-4 四级", 6: "CET-6 六级", 0: "超纲"}
GROUP_ORDER = (4, 6, 0)


def _sentence_html(sent: str, marks, sn: int) -> str:
    """句内生词加粗 + 点状下划线（黑白打印也清楚），句末附句子编号。

    marks: [词条…]（词条带 text=文中原样、n=编号），按出现顺序
    """
    pending = {}
    for m in marks or ():
        pending.setdefault(m["text"].lower(), []).append(m)
    parts, last = [], 0
    for mt in TOKEN_RE.finditer(sent):
        queue = pending.get(mt.group(0).lower())
        if not queue:
            continue
        term = queue.pop(0)
        parts.append(esc(sent[last:mt.start()]))
        parts.append(f'<b class="w">{esc(mt.group(0))}<sup>{term["n"]}</sup></b>')
        last = mt.end()
    parts.append(esc(sent[last:]))
    return "".join(parts) + f'<sup class="sn">{sn}</sup>'


def _body_html(paragraphs, marks) -> str:
    out, sn = [], 1
    for pi, para in enumerate(paragraphs):
        parts = []
        for si, sent in enumerate(para):
            sent_marks = marks[pi][si] if marks and pi < len(marks) else []
            parts.append(_sentence_html(sent, sent_marks, sn))
            sn += 1
        out.append("<p>" + " ".join(parts) + "</p>")
    return "\n".join(out)


def _gloss_html(entries) -> str:
    """词汇速查：四级 → 六级 → 超纲 三档，编号与正文上标一致。"""
    if not entries:
        return ""
    out = ['<section class="gloss">\n']
    for tier in GROUP_ORDER:
        items = [e for e in entries if int(e.get("tier", 0)) == tier]
        if not items:
            continue
        out.append(f'<h4 class="gh">{esc(GROUP_TITLES[tier])}'
                   f'<span class="gc">{len(items)} 词</span></h4>\n')
        out.append('<div class="gloss-cols">\n')
        for e in items:
            out.append(f'<p class="gi"><span class="gn">{e["n"]}</span>'
                       f'<b>{esc(e["word"])}</b> <span class="ipa">{esc(e["ipa"])}</span> '
                       f'<span class="zh">{esc(e["zh"])}</span></p>\n')
        out.append("</div>\n")
    out.append("</section>\n")
    return "".join(out)


def build_week_html(articles, title: str = "") -> str:
    """封面目录 + 每篇从新页开始（正文 + 词汇速查）。"""
    total_sent = sum(len(s) for a in articles for s in a["paragraphs"])
    out = [
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n",
        f"<title>{esc(title)} · 学习资料</title>\n",
        PRINT_WEEK_STYLE,
        "</head>\n<body>\n<div class=\"wrap\" id=\"top\">\n",
        "<section class=\"cover\">\n",
        '<p class="kicker">The Economist · 精读讲义</p>\n',
        f'<h1 class="issue">{esc(title)}</h1>\n',
        f'<p class="issue-meta">{len(articles)} 篇 · {total_sent} 句 · A4 双面打印</p>\n',
        '<ol class="toc">\n',
    ]
    for n, a in enumerate(articles, 1):
        count = sum(len(p) for p in a["paragraphs"])
        out.append(
            f'<li><span class="chk">☐</span><span class="tno">{n:02d}</span>'
            f'<span class="tsec">{esc(a.get("section") or "—")}</span>'
            f'<span class="ttitle">{esc(a["title"])}</span>'
            f'<span class="tsent">{count} 句</span></li>\n'
        )
    out.append("</ol>\n")
    out.append(
        '<div class="howto">用法：每天 2 页（1 篇），先通读原文，再用右侧留白记生词；'
        '读完对照文末词汇速查表复盘。打印：A4 · 100% · 双面长边翻转 · 关闭页眉页脚。</div>\n'
    )
    out.append("</section>\n")

    for n, a in enumerate(articles, 1):
        entries = a.get("glossary") or []
        meta = " · ".join(b for b in (a.get("section"), a.get("date")) if b)
        out.append(f'<article id="a{n:03d}">\n<header class="art-head">\n')
        out.append(f'<p class="art-meta">第 {n} / {len(articles)} 篇 · {esc(meta)}</p>\n')
        out.append(f'<h2 class="art-title">{esc(a["title"])}</h2>\n')
        if a.get("standfirst"):
            out.append(f'<p class="art-standfirst">{esc(a["standfirst"])}</p>\n')
        out.append('</header>\n<div class="art-body">\n')
        out.append(_body_html(a["paragraphs"], a.get("marks")))
        out.append('\n</div>\n')
        out.append(_gloss_html(entries))
        out.append('</article>\n')

    out.append("</div>\n</body>\n</html>\n")
    return "".join(out)


CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


def chrome_binary():
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def html_to_pdf(html_path: Path, pdf_path: Path, timeout: int = 300) -> bool:
    """用 Chrome/Edge 无头模式导出 A4 PDF（页面尺寸由 CSS @page 决定）。"""
    chrome = chrome_binary()
    if not chrome:
        return False
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.exists():
        pdf_path.unlink()
    cmd = [
        chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}", Path(html_path).resolve().as_uri(),
    ]
    try:
        subprocess.run(cmd, check=True, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.SubprocessError, OSError):
        return False
    return pdf_path.exists()
