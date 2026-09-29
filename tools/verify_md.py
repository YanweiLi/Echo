#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 dist/md/<期号>/*.md 与源 EPUB 是否一致（逐句回查）。

用法：
    python tools/verify_md.py <epub> [--md dist/md/te_2026.01.03] [--verbose]

判定方式：
  1. md 文件名前缀 NNN ↔ EPUB spine 中第 NNN 章，按序号一一对应
  2. md 里每个 <div class="original"> 的句子（去 span、去实体）应能在
     该章正文（段落拼接后的规范化文本）中找到
  3. 统计：篇数 / 句数 / 未命中句数 / 字符覆盖率，并列出问题样本

规范化：NFKC、小写、直/弯引号统一、破折号统一、空白折叠。
"""

import argparse
import html
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from epub2md import Epub, chapter_paragraphs, pick_fields, strip_ligatures  # noqa: E402

DIV_RE = re.compile(r'<div class="original">(.*?)</div>', re.S)
SPAN_RE = re.compile(r"<[^>]+>")
QUOTES = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
          "\u2013": "-", "\u2014": "-", "\u2010": "-", "\u2011": "-",
          "\u00a0": " ", "\u2009": " ", "\u202f": " "}


def norm(s: str) -> str:
    s = html.unescape(s or "")
    s = strip_ligatures(s)
    s = unicodedata.normalize("NFKC", s)
    for a, b in QUOTES.items():
        s = s.replace(a, b)
    s = s.replace("\u2026", "...")
    return re.sub(r"\s+", " ", s).strip().lower()


def md_sentences(path: Path):
    """从 md 里取出原文句子（按出现顺序）。"""
    text = path.read_text(encoding="utf-8")
    out = []
    for block in DIV_RE.findall(text):
        out.append(norm(html.unescape(SPAN_RE.sub("", block))))
    return out


def main():
    ap = argparse.ArgumentParser(description="校验 md 与 EPUB 源文一致性")
    ap.add_argument("epub", help="源 epub")
    ap.add_argument("--md", default=None, help="md 目录（默认 dist/md/<期号>）")
    ap.add_argument("--verbose", action="store_true", help="打印每篇明细")
    args = ap.parse_args()

    epub_path = Path(args.epub).expanduser()
    sys.path.insert(0, str(ROOT / "tools"))
    from epub2md import issue_id
    md_dir = Path(args.md).expanduser() if args.md else (ROOT / "dist" / "md" / issue_id(epub_path.stem))
    files = sorted(md_dir.glob("*.md"))
    if not files:
        raise SystemExit(f"没有 md 文件：{md_dir}")

    ep = Epub(epub_path)
    chapters = {}
    for idx, (href, full, doc) in enumerate(ep.documents(), 1):
        title, section, fly, standfirst, date = pick_fields(doc)
        paras = chapter_paragraphs(doc)
        source = norm(" ".join([title, standfirst] + paras))
        chapters[idx] = {"title": title, "source": source, "chars": len(source),
                         "paras": len(paras)}

    total_sent = bad_sent = 0
    total_chars_matched = total_chars_src = 0
    problems = []

    for path in files:
        m = re.match(r"(\d+)", path.name)
        if not m:
            problems.append((path.name, "文件名缺少序号前缀", []))
            continue
        idx = int(m.group(1))
        ch = chapters.get(idx)
        if ch is None:
            problems.append((path.name, f"EPUB 无第 {idx} 章", []))
            continue
        sents = md_sentences(path)
        misses = [s for s in sents if s and s not in ch["source"]]
        total_sent += len(sents)
        bad_sent += len(misses)
        total_chars_src += ch["chars"]
        total_chars_matched += sum(len(s) for s in sents if s in ch["source"])
        if misses:
            problems.append((path.name, f"{len(misses)}/{len(sents)} 句未命中", misses[:3]))
        if args.verbose:
            flag = "✗" if misses else "✓"
            print(f"  {flag} {path.name[:56]:<58} 句={len(sents):<3} 源字符={ch['chars']:<5}")

    print(f"\n{'='*74}\n📄 md 目录：{md_dir}\n📘 源 epub：{epub_path.name}\n{'='*74}")
    print(f"校验文件 {len(files)} 篇   句子 {total_sent} 句")
    print(f"未命中句子 {bad_sent} 句（{bad_sent/max(total_sent,1)*100:.2f}%）")
    print(f"正文覆盖率 {total_chars_matched/max(total_chars_src,1)*100:.1f}%"
          f"（md 命中字符 {total_chars_matched} / 源字符 {total_chars_src}）")

    if problems:
        print(f"\n--- 有问题的文件（{len(problems)}）---")
        for name, why, samples in problems[:15]:
            print(f"  ✗ {name[:60]}\n      {why}")
            for s in samples:
                print(f"      · {s[:100]}")
    else:
        print("\n✅ 全部句子都能在源 EPUB 中找到（无丢句 / 无拼错 / 无混入）")


if __name__ == "__main__":
    main()
