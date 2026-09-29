#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EPUB → 中间 md（沿用现有 md 格式，可直接给 Echo 播放，也可打印）

用法：
    python tools/epub2md.py <epub|目录> --probe            # 只统计结构，不写文件
    python tools/epub2md.py <epub|目录> --out dist/md      # 生成 md
    python tools/epub2md.py <epub|目录> --out dist/md --limit 5

格式基准 = 现有 md 文件（4 列词汇速查表 + ⭐难度分组 + 双栏 div）
"""

import argparse
import html
import json
import re
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from player import split_sentences  # noqa: E402  复用现有断句（含缩写表）

DICT_FILE = SRC_DIR / "dictionary.json"

# 非文章页（按文件名过滤）
SKIP_PATTERNS = (
    "cover", "toc", "nav", "ad_page", "copyright", "masthead",
    "titlepage", "backmatter", "index", "contributors",
)

OPF_NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}


JUNK_CHARS = "■▪•▸▹"


def clean(s: str) -> str:
    s = s or ""
    for ch in JUNK_CHARS:
        s = s.replace(ch, "")
    return re.sub(r"\s+", " ", s).strip()


def strip_ligatures(s: str) -> str:
    for k, v in {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}.items():
        s = s.replace(k, v)
    return s


# --------------------------------------------------------------------------
# EPUB 解析
# --------------------------------------------------------------------------

class DocParser(HTMLParser):
    """把一个 xhtml 文档解析成：标题 / 各级标题 / 正文段落 / 元数据。

    依据真实结构（The Economist epub）：
      <p><span class="te_section_title">Leaders</span>
         <span class="te_fly_span"> | Cost of living</span></p>
      <h1 class="te_article_title">…</h1>
      <h3 class="te_article_rubric">…副标题…</h3>
      <h3 class="te_article_datePublished">…</h3>
      <p>正文…</p>
      <p class="link_navbar">This article was downloaded by …</p>
    """

    META_KEYS = ("te_section_title", "te_fly_span", "te_article_title",
                 "te_article_rubric", "te_article_datepublished")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.headings = []      # [(tag, class, text)]
        self.paragraphs = []    # [text]（已剔除元数据/水印/推销段）
        self.spans = []         # [(class, text)]
        self.meta = {}          # 小写 class -> text
        self._h_stack = []
        self._span_stack = []   # [[cls, [text...]]]
        self._buf = []
        self._in_p = 0
        self._p_class = ""
        self._p_spans = []
        self._in_title = False
        self._skip = 0

    def _note_meta(self, cls, text):
        c = (cls or "").lower()
        for key in self.META_KEYS:
            if key in c and key not in self.meta and text:
                self.meta[key] = text

    def _flush_p(self):
        txt = clean("".join(self._buf))
        self._buf = []
        if not txt:
            return
        if any("te_section_title" in c for c in self._p_spans):
            return          # 元数据段（栏目行）
        if "link_navbar" in self._p_class:
            return          # zlibrary 水印/原链接
        if txt.startswith("For subscribers only"):
            return          # 推销段
        self.paragraphs.append(strip_ligatures(txt))

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class", "") or ""
        if tag in ("script", "style", "head"):
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
            self._buf = []
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_p()
            self._h_stack.append((tag, cls))
            self._buf = []
            return
        if tag == "p":
            self._flush_p()
            self._in_p += 1
            self._p_class = cls
            self._p_spans = []
            self._buf = []
            return
        if tag == "span":
            self._span_stack.append([cls, []])
            if self._in_p and cls:
                self._p_spans.append(cls)
            return
        if tag == "br":
            self._buf.append(" ")   # <br/> 当空格，避免单词粘连

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self._skip = max(0, self._skip - 1)
            return
        if tag == "title":
            self.title = strip_ligatures(clean("".join(self._buf)))
            self._in_title = False
            self._buf = []
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if self._h_stack:
                t, cls = self._h_stack.pop()
                txt = strip_ligatures(clean("".join(self._buf)))
                if txt:
                    self.headings.append((t, cls, txt))
                    self._note_meta(cls, txt)
            self._buf = []
            return
        if tag == "p":
            self._flush_p()
            self._in_p = max(0, self._in_p - 1)
            self._p_class = ""
            self._p_spans = []
            return
        if tag == "span":
            if self._span_stack:
                cls, buf = self._span_stack.pop()
                txt = strip_ligatures(clean("".join(buf)))
                if cls:
                    self.spans.append((cls, txt))
                    self._note_meta(cls, txt)
            return

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self._buf.append(data)
            return
        if self._span_stack:
            self._span_stack[-1][1].append(data)
        if self._in_p or self._h_stack:
            self._buf.append(data)


class Epub:
    def __init__(self, path: Path):
        self.path = path
        self.z = zipfile.ZipFile(path)
        self.opf_path = self._find_opf()
        self.opf_dir = str(PurePosixPath(self.opf_path).parent)
        if self.opf_dir == ".":
            self.opf_dir = ""
        self.manifest, self.spine_ids, self.meta = self._parse_opf()

    def _read(self, name) -> str:
        return self.z.read(name).decode("utf-8", "ignore")

    def _find_opf(self) -> str:
        cont = self._read("META-INF/container.xml")
        m = re.search(r'full-path="([^"]+)"', cont)
        if not m:
            raise SystemExit(f"无法定位 content.opf：{self.path.name}")
        return m.group(1)

    def _parse_opf(self):
        root = ET.fromstring(self._read(self.opf_path))
        manifest = {}
        for item in root.iter():
            if item.tag.endswith("}item"):
                manifest[item.get("id")] = {
                    "href": item.get("href"),
                    "type": item.get("media-type", ""),
                }
        spine_ids = []
        for ref in root.iter():
            if ref.tag.endswith("}itemref"):
                spine_ids.append(ref.get("idref"))
        meta = {
            "title": (root.findtext(".//dc:title", default="", namespaces=OPF_NS) or "").strip(),
            "date": (root.findtext(".//dc:date", default="", namespaces=OPF_NS) or "").strip(),
        }
        return manifest, spine_ids, meta

    def documents(self):
        """按 spine 顺序产出 (href, 绝对 zip 路径, DocParser)。"""
        for sid in self.spine_ids:
            item = self.manifest.get(sid)
            if not item or item["type"] not in ("application/xhtml+xml", "text/html"):
                continue
            href = item["href"]
            full = str(PurePosixPath(self.opf_dir) / href) if self.opf_dir else href
            try:
                src = self._read(full)
            except KeyError:
                continue
            p = DocParser()
            p.feed(src)
            yield href, full, p


# --------------------------------------------------------------------------
# 章节 → 文章
# --------------------------------------------------------------------------

def is_skippable(href: str) -> bool:
    low = href.lower()
    return any(pat in low for pat in SKIP_PATTERNS)


def pick_fields(doc: DocParser):
    """返回 (title, section, fly, standfirst, date)。"""
    m = doc.meta
    title = m.get("te_article_title") or doc.title
    section = m.get("te_section_title", "")
    fly = m.get("te_fly_span", "").lstrip("|").strip()
    standfirst = m.get("te_article_rubric", "")
    date = m.get("te_article_datepublished", "")
    return title, section, fly, standfirst, date


def chapter_paragraphs(doc: DocParser):
    return [p for p in doc.paragraphs if len(p) >= 2]


# --------------------------------------------------------------------------
# 生词标注 / md 生成（移植旧脚本逻辑，格式对齐现有 md）
# --------------------------------------------------------------------------

def load_dict():
    if not DICT_FILE.exists():
        print(f"⚠️  未找到词典 {DICT_FILE}，将不做生词标注（先跑 tools/extract_dict.py）")
        return {}
    return json.loads(DICT_FILE.read_text(encoding="utf-8"))


def find_vocab(sentence: str, dictionary: dict):
    """返回句中命中的词典词：[(word, ipa, zh, level)]，按出现顺序。"""
    hits = []
    seen = set()
    for m in re.finditer(r"\b[a-zA-Z][a-zA-Z'-]{2,}\b", sentence):
        raw = m.group(0)
        key = raw.lower()
        if key in seen:
            continue
        info = dictionary.get(key)
        if info:
            seen.add(key)
            hits.append((raw, info.get("ipa", ""), info.get("zh", ""), int(info.get("level", 0))))
    return hits


def highlight(sentence: str, words) -> str:
    result = sentence
    for w, _, _, _ in words:
        result = re.sub(r"\b" + re.escape(w) + r"\b",
                        f'<span style="color:red">{w}</span>', result, flags=re.IGNORECASE)
    return result


MD_STYLE = """<style>
.container { display: flex; }
.original { flex: 0 0 70%; padding-right: 15px; border-right: 1px solid #e0e0e0; margin-right: 15px; }
.vocab { flex: 0 0 30%; color: #888; font-size: 14px; }
@media print {
  .container { break-inside: avoid; page-break-inside: avoid; }
  .original { flex: 0 0 66%; }
  .vocab { flex: 0 0 34%; color: #555; }
}
</style>
"""

LEVEL_TITLES = {1: "⭐ 初级词汇", 2: "⭐⭐ 中级词汇", 3: "⭐⭐⭐ 高级词汇"}


def collect_vocab(sentences, dictionary: dict) -> dict:
    """汇总本篇生词：word -> {word, ipa, zh, level, example(首次出现句)。"""
    all_vocab = {}
    for sent in sentences:
        for raw, ipa, zh, level in find_vocab(sent, dictionary):
            key = raw.lower()
            if key not in all_vocab:
                example = sent if len(sent) <= 60 else sent[:57].rstrip() + "…"
                all_vocab[key] = {"word": raw, "ipa": ipa, "zh": zh,
                                  "level": level, "example": example}
    return all_vocab


def vocab_groups(all_vocab: dict):
    """按难度分组 → [(标题, [词条…]), …]，粒度与现有 md 一致。"""
    groups = []
    for level in (1, 2, 3):
        group = sorted((v for v in all_vocab.values() if v["level"] == level),
                       key=lambda x: x["word"].lower())
        if group:
            groups.append((LEVEL_TITLES[level], group))
    other = sorted((v for v in all_vocab.values() if v["level"] not in (1, 2, 3)),
                   key=lambda x: x["word"].lower())
    if other:
        groups.append(("其他词汇", other))
    return groups


def sentence_blocks(sentences, dictionary: dict):
    """每句一个双栏块（原文 + 侧栏生词）。md 与打印 HTML 共用。"""
    blocks = []
    for sent in sentences:
        hits = find_vocab(sent, dictionary)
        vocab_html = "".join(f"{raw} {ipa} {zh}<br>" for raw, ipa, zh, _ in hits[:5])
        blocks.append(
            '<div class="container">\n'
            f'<div class="original">{highlight(sent, hits)}</div>\n'
            f'<div class="vocab">{vocab_html}</div>\n'
            "</div>"
        )
    return blocks


def build_md(title: str, section: str, fly: str, standfirst: str,
             date: str, sentences, dictionary: dict) -> str:
    """按现有 md 的格式产出（4 列词汇速查表 + ⭐难度分组 + 双栏 div）。"""
    md = []
    md.append(f"# {title or 'Article'}\n\n")
    meta_bits = [b for b in (section, fly, date) if b]
    if meta_bits:
        md.append("> " + " · ".join(meta_bits) + "\n\n")
    if standfirst:
        md.append(f"*{standfirst}*\n\n")
    md.append("本资料从《经济学人》政治周刊中提取高频词汇，提供音标、中文释义及核心用法说明。\n\n")
    md.append("---\n\n")
    md.append("## 原文阅读\n\n")
    md.append(MD_STYLE + "\n")
    md.append("\n\n".join(sentence_blocks(sentences, dictionary)) + "\n\n")
    md.append("---\n\n")
    md.append("## 词汇速查表\n\n")
    for name, group in vocab_groups(collect_vocab(sentences, dictionary)):
        md.append(f"### {name}\n\n")
        md.append("| 单词 | 音标 | 释义 | 示例 |\n")
        md.append("| ---- | ---- | ---- | ---- |\n")
        for v in group:
            md.append(f"| {v['word']} | {v['ipa']} | {v['zh']} | {v['example']} |\n")
        md.append("\n")
    md.append("---\n\n")
    return "".join(md)


# --------------------------------------------------------------------------
# 整期合并的可打印 HTML（选项 B）
# --------------------------------------------------------------------------

PRINT_STYLE = """<style>
  :root { --ink:#111; --muted:#8a8a8a; --line:#dcdcdc; }
  @page { size: A4; margin: 14mm 12mm; }
  html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  body { margin:0; padding:26px 22px 60px; background:#fff; color:var(--ink);
         font: 15px/1.62 Georgia, "Times New Roman", "Songti SC", serif; }
  .wrap { max-width: 880px; margin: 0 auto; }
  h1.issue { font-size: 27px; margin:0 0 4px; letter-spacing:-.01em; }
  .issue-meta { color:var(--muted); font-size:13px; margin:0 0 18px; }
  .toc { border:1px solid var(--line); border-radius:8px; padding:14px 18px 18px; margin-bottom:26px; }
  .toc h2 { font-size:14px; text-transform:uppercase; letter-spacing:.09em;
            color:var(--muted); margin:0 0 10px; font-weight:600; }
  .toc ol { columns: 2; column-gap: 30px; margin:0; padding-left:20px; font-size:13.5px; }
  .toc li { margin: 2px 0; break-inside: avoid; }
  .toc a { color:inherit; text-decoration:none; }
  .toc a:hover { text-decoration:underline; }
  .toc .sec { color:var(--muted); }
  article { page-break-before: always; break-before: page; }
  article:first-of-type { page-break-before: avoid; break-before: auto; }
  .art-head { border-bottom:2px solid var(--ink); padding-bottom:8px; margin-bottom:16px; }
  .art-meta { color:var(--muted); font-size:12px; letter-spacing:.08em;
              text-transform:uppercase; margin:0 0 6px; }
  h2.art-title { font-size:23px; line-height:1.25; margin:0 0 8px; }
  .art-standfirst { font-style:italic; color:#333; margin:0; font-size:15px; }
  .container { display:flex; gap:16px; margin:0 0 5px;
               break-inside: avoid; page-break-inside: avoid; }
  .original { flex: 0 0 67%; }
  .vocab { flex: 0 0 33%; color:var(--muted); font-size:12.5px;
           line-height:1.45; border-left:1px solid #eee; padding-left:10px; }
  .art-vocab { page-break-before: auto; margin-top:22px; }
  table.vt { border-collapse:collapse; width:100%; font-size:12.5px; margin-bottom:6px; }
  table.vt th, table.vt td { border:1px solid var(--line); padding:3px 7px;
                             text-align:left; vertical-align:top; }
  table.vt th { background:#f6f6f6; font-weight:600; }
  table.vt td.ex { color:#666; }
  h3.vt-level { font-size:14.5px; margin:16px 0 6px; }
  .backtop { font-size:12px; color:var(--muted); margin-top:10px; }
  @media screen { .container:hover .original { background:#fffdf2; } }
  @media print {
    body { padding:0; font-size:11pt; }
    .no-print { display:none !important; }
    .toc { border:0; padding:0; }
    .toc ol { columns: 2; }
    a { color:inherit; text-decoration:none; }
    h2.art-title { font-size:16pt; }
    .vocab { font-size:8.5pt; }
    table.vt { font-size:8.5pt; }
  }
</style>
"""


def esc(s) -> str:
    return html.escape(str(s or ""))


def build_print_html(issue: str, articles, dictionary: dict, title: str = "") -> str:
    """整期合并：封面目录 + 每篇分页 + 双栏原文 + 词汇速查表。"""
    total_sent = sum(len(a["sentences"]) for a in articles)
    words = set()
    for a in articles:
        words.update(collect_vocab(a["sentences"], dictionary))

    out = [
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n",
        f"<title>{esc(title or issue)} · 学习资料</title>\n",
        PRINT_STYLE,
        "</head>\n<body>\n<div class=\"wrap\" id=\"top\">\n",
        f"<h1 class=\"issue\">{esc(title or issue)}</h1>\n",
        f"<p class=\"issue-meta\">共 {len(articles)} 篇 · {total_sent} 句 · "
        f"生词 {len(words)} 条 · <span class=\"no-print\">打印可分栏双面</span></p>\n",
    ]

    # 目录
    out.append("<nav class=\"toc\">\n<h2>目录</h2>\n<ol>\n")
    for a in articles:
        sec = f'<span class="sec">{esc(a["section"])} · </span>' if a.get("section") else ""
        out.append(f'<li><a href="#a{a["idx"]:03d}">{sec}{esc(a["title"])}</a></li>\n')
    out.append("</ol>\n</nav>\n")

    # 正文
    for a in articles:
        meta_bits = [b for b in (a.get("section"), a.get("fly"), a.get("date")) if b]
        out.append(f'<article id="a{a["idx"]:03d}">\n<div class="art-head">\n')
        if meta_bits:
            out.append(f'<p class="art-meta">{esc(" · ".join(meta_bits))}</p>\n')
        out.append(f'<h2 class="art-title">{esc(a["title"])}</h2>\n')
        if a.get("standfirst"):
            out.append(f'<p class="art-standfirst">{esc(a["standfirst"])}</p>\n')
        out.append("</div>\n")
        out.append("\n".join(sentence_blocks(a["sentences"], dictionary)))
        out.append('\n<div class="art-vocab">\n<h3 class="vt-level">词汇速查表</h3>\n')
        for name, group in vocab_groups(collect_vocab(a["sentences"], dictionary)):
            out.append(f'<h3 class="vt-level">{esc(name)}</h3>\n<table class="vt">\n')
            out.append("<tr><th>单词</th><th>音标</th><th>释义</th><th>示例</th></tr>\n")
            for v in group:
                out.append(f'<tr><td>{esc(v["word"])}</td><td>{esc(v["ipa"])}</td>'
                           f'<td>{esc(v["zh"])}</td><td class="ex">{esc(v["example"])}</td></tr>\n')
            out.append("</table>\n")
        out.append('<p class="backtop no-print"><a href="#top">↑ 回到目录</a></p>\n</div>\n</article>\n')

    out.append("</div>\n</body>\n</html>\n")
    return "".join(out)


def slug(text: str, maxlen: int = 60) -> str:
    text = strip_ligatures(text)
    text = re.sub(r"['’]", "", text)
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:maxlen].strip("_")


def issue_id(stem: str) -> str:
    """TheEconomist.2026.01.03 → te_2026.01.03（与源目录命名一致）"""
    s = re.sub(r"^The[_\- ]?Economist[._\- ]*", "", stem.strip(), flags=re.I)
    s = re.sub(r"^TE[._\- ]+", "", s, flags=re.I)
    s = re.sub(r"[^0-9A-Za-z.]+", "_", s).strip("._")
    if not s:
        return f"te_{slug(stem, 40)}"
    return f"te_{s}"


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def collect_epubs(target: Path):
    if target.is_file() and target.suffix.lower() == ".epub":
        return [target]
    if target.is_dir():
        return sorted(p for p in target.rglob("*.epub") if not p.name.startswith("._"))
    raise SystemExit(f"不是 epub 也不是目录：{target}")


def analyze(epub_path: Path, dictionary: dict):
    """返回 (articles, report)。articles 元素为 dict。"""
    ep = Epub(epub_path)
    articles, report = [], []
    for idx, (href, full, doc) in enumerate(ep.documents(), 1):
        title, section, fly, standfirst, date = pick_fields(doc)
        paras = chapter_paragraphs(doc)
        chars = sum(len(p) for p in paras)
        skip = is_skippable(href) or not paras or chars < 400
        rows = []
        sentences = []
        if not skip:
            for p in paras:
                sentences.extend(split_sentences(p))
            sentences = [s for s in sentences if len(s) >= 2]
            hits = sum(len(find_vocab(s, dictionary)) for s in sentences) if dictionary else 0
        report.append({
            "idx": idx, "href": href, "title": title, "section": section,
            "fly": fly, "date": date, "paras": len(paras), "chars": chars,
            "sentences": len(sentences), "vocab_hits": hits if not skip else 0,
            "skip": skip,
            "headings": [(t, c, x) for t, c, x in doc.headings][:6],
        })
        if not skip:
            articles.append({
                "idx": idx, "href": href, "title": title or f"Article {idx}",
                "section": section, "fly": fly, "standfirst": standfirst,
                "date": date, "sentences": sentences,
            })
    return articles, report


def print_probe(epub_path: Path, report, articles):
    print(f"\n{'='*78}\n📘 {epub_path.name}\n{'='*78}")
    kept = [r for r in report if not r["skip"]]
    skipped = [r for r in report if r["skip"]]
    print(f"章节总数 {len(report)}   保留 {len(kept)}   过滤 {len(skipped)}")
    print(f"句子总数 {sum(r['sentences'] for r in kept)}   生词命中 {sum(r['vocab_hits'] for r in kept)}")

    from collections import Counter
    sec = Counter(r["section"] or "（无栏目）" for r in kept)
    print(f"\n栏目分布（共 {len(sec)} 个）:")
    for name, n in sec.most_common():
        print(f"   {n:>3}  {name}")

    print("\n--- 保留的章节 ---")
    for r in kept[:12]:
        print(f"  [{r['idx']:>3}] {r['section'] or '—':<22} "
              f"p={r['paras']:<3} 句={r['sentences']:<3} 词={r['vocab_hits']:<3} {r['title'][:52]}")
    if len(kept) > 12:
        print(f"  … 其余 {len(kept)-12} 篇")

    print("\n--- 被过滤的章节 ---")
    for r in skipped[:15]:
        why = "文件名" if is_skippable(r["href"]) else ("无段落" if r["paras"] == 0 else f"太短({r['chars']})")
        print(f"  [{r['idx']:>3}] {why:<12} {r['href'][:60]}")

    print("\n--- 首篇结构样例 ---")
    if kept:
        r = kept[0]
        print(f"  href: {r['href']}")
        print(f"  title={r['title']!r}\n  section={r['section']!r} fly={r.get('fly','')!r} date={r['date']!r}")
        for t, c, x in r["headings"]:
            print(f"    <{t} class={c!r}> {x[:70]}")
        a = next(a for a in articles if a["idx"] == r["idx"])
        for s in a["sentences"][:2]:
            print(f"    句: {s[:110]}")


def main():
    ap = argparse.ArgumentParser(description="EPUB → 中间 md（Echo 可用 + 可打印）")
    ap.add_argument("target", help="epub 文件，或包含 epub 的目录")
    ap.add_argument("--out", default=None, help="输出根目录（默认 dist/md）")
    ap.add_argument("--probe", action="store_true", help="只统计结构，不写文件")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个 epub")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的 md")
    ap.add_argument("--print", dest="do_print", action="store_true",
                    help="另出整期合并的可打印 HTML（dist/print/<期号>.html）")
    args = ap.parse_args()

    target = Path(args.target).expanduser()
    epubs = collect_epubs(target)
    if args.limit:
        epubs = epubs[:args.limit]
    if not epubs:
        raise SystemExit("没有找到 epub 文件")

    dictionary = load_dict()
    out_root = Path(args.out).expanduser() if args.out else (ROOT / "dist" / "md")
    total_md = 0

    for epub_path in epubs:
        articles, report = analyze(epub_path, dictionary)
        if not articles:
            print(f"⚠️  {epub_path.name}：没有解析出文章")
            continue

        if args.probe:
            print_probe(epub_path, report, articles)
            continue

        issue = issue_id(epub_path.stem)
        out_dir = out_root / issue
        out_dir.mkdir(parents=True, exist_ok=True)
        for a in articles:
            name = f"{a['idx']:03d}_{slug(a['section'] or 'Article', 24)}_-_{slug(a['title'], 60)}.md"
            path = out_dir / name
            if path.exists() and not args.force:
                continue
            path.write_text(build_md(a["title"], a["section"], a["fly"], a["standfirst"],
                                     a["date"], a["sentences"], dictionary), encoding="utf-8")
            total_md += 1
        kept = sum(1 for r in report if not r["skip"])
        print(f"✅ {issue}: 写出 {kept} 篇（{sum(len(a['sentences']) for a in articles)} 句）→ {out_dir}")

        if args.do_print:
            date_txt = next((a["date"] for a in articles if a.get("date")), "")
            label = "The Economist" + (f" · {date_txt}" if date_txt else f" · {issue}")
            print_dir = ROOT / "dist" / "print"
            print_dir.mkdir(parents=True, exist_ok=True)
            html_path = print_dir / f"{issue}.html"
            html_path.write_text(build_print_html(issue, articles, dictionary, label),
                                 encoding="utf-8")
            print(f"🖨️  整期打印版 → {html_path}（{html_path.stat().st_size//1024} KB）")

    if not args.probe:
        print(f"\n总计写出 {total_md} 个 md → {out_root}")


if __name__ == "__main__":
    main()
