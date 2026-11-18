#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建词库 src/wordbank.json（一次性脚本）

数据源：skywind3000/ECDICT（MIT License）的 ecdict.csv
用法：
    .venv/bin/python tools/build_wordbank.py                 # 自动下载到 /tmp（约 63 MB）
    .venv/bin/python tools/build_wordbank.py --csv /path/to/ecdict.csv

输出结构：
    {
      "_meta": {"source": …, "built": …, "tier4": n, "tier6": n, "oov": n, "forms": n},
      "words": {"abandon": {"ipa": "/əˈbændən/", "zh": "放弃、抛弃", "tier": 4, "rank": 2057}, …},
      "forms": {"abandoned": "abandon", …}       # 变形 → 原形（来自 ECDICT exchange 字段）
    }

tier: 4 = 四级（含四六级都收的），6 = 仅六级，0 = 超纲（四六级之外，但在常用词频段内）

超纲词只收 BNC 排名 (low, high] 区间的词：太常见的本来就该会，太生僻的
（专有名词、术语、古词）对学习者没价值，而且会让标注变成噪声。
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "wordbank.json"
URL = "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv"
CACHE = Path("/tmp/ecdict.csv")

# 超纲词的词频窗口（BNC 排名）：>LOW 说明不是最基础那批，<=HIGH 说明还算常用
OOV_LOW, OOV_HIGH = 2000, 15000
MAX_ZH = 26
POS_PREFIX = re.compile(r"^(?:[a-z]{1,6}\.\s*)+", re.I)
SPLIT_ZH = re.compile(r"[,，;；]")


def to_ipa(raw: str) -> str:
    """ECDICT 的注音是 ASCII 近似 → 规范成 /…/ 形式的 IPA。"""
    s = (raw or "").strip()
    if not s:
        return ""
    s = re.split(r"[;；,，]|\s+", s)[0]          # 只留第一种读法
    for a, b in (("ә", "ə"), (":", "ː"), ("'", "ˈ"), ("ɡ", "g"), (".", "ˌ")):
        s = s.replace(a, b)
    s = re.sub(r"[^A-Za-zəɪʊæɑɒɔɜʌʃʒθðŋˈˌː-]", "", s)   # 清掉 ^ є 之类的脏字符
    s = re.sub(r"^/+|/+$", "", s).strip("ˌː-")
    return f"/{s}/" if s else ""


def short_zh(raw: str) -> str:
    """取第一条释义里的前 2–3 个义项，例如“vt. 放弃, 抛弃, 遗弃” → “放弃、抛弃、遗弃”。"""
    text = (raw or "").replace("\\n", "\n")
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("["):
            continue
        line = POS_PREFIX.sub("", line)
        senses = []
        for part in SPLIT_ZH.split(line):
            part = part.strip(" 。.．")
            if part and part not in senses:
                senses.append(part)
            if len(senses) >= 3:
                break
        joined = "、".join(senses)
        if joined:
            return joined[:MAX_ZH]
    return ""


def parse_exchange(field: str):
    """exchange: d:abandoned/p:abandoned/i:abandoning/3:abandons → ['abandoned', …]"""
    out = []
    for part in (field or "").split("/"):
        if ":" not in part:
            continue
        tag, val = part.split(":", 1)
        if tag in ("d", "p", "i", "3", "r", "t", "s", "1") and val:
            out.append(val.strip().lower())
    return out


def download() -> Path:
    if CACHE.exists():
        return CACHE
    print(f"⏳ 下载 ECDICT（约 63 MB）→ {CACHE}")
    subprocess.run(["curl", "-sS", "-o", str(CACHE), URL], check=True, timeout=600)
    return CACHE


def main():
    ap = argparse.ArgumentParser(description="构建词库（四六级 + 常用超纲词）")
    ap.add_argument("--csv", default=None, help="ecdict.csv 路径（缺省则下载到 /tmp）")
    args = ap.parse_args()

    src = Path(args.csv) if args.csv else download()
    if not src.exists():
        sys.exit(f"找不到 {src}")

    # 第 1 遍：收集全表词频（判断“派生词的词根是否太基础”）
    ranks = {}
    with src.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            w = (row.get("word") or "").strip().lower()
            if not w:
                continue
            try:
                ranks[w] = int(row.get("bnc") or 0)
            except ValueError:
                ranks[w] = 0
    print(f"   词频表：{len(ranks)} 词")

    words, forms, roots = {}, {}, {}
    stats = {"tier4": 0, "tier6": 0, "oov": 0, "drop": 0}
    with src.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            word = (row.get("word") or "").strip().lower()
            if not re.fullmatch(r"[a-z][a-z'-]*", word) or len(word) < 2:
                continue
            try:
                rank = int(row.get("bnc") or 0)
            except ValueError:
                rank = 0

            tags = (row.get("tag") or "").split()
            if "cet4" in tags:
                tier = 4
            elif "cet6" in tags:
                tier = 6
            elif OOV_LOW < rank <= OOV_HIGH:
                tier = 0
            else:
                continue

            zh = short_zh(row.get("translation") or "")
            if not zh:
                continue

            # 派生词（warning ← warn、giving ← give）：超纲档直接并到词根，
            # 四六级档保留原词，但记下词根，运行时可去重。
            base = ""
            for part in (row.get("exchange") or "").split("/"):
                if part.startswith("0:"):
                    base = part[2:].strip().lower()
            if base and base != word:
                base_rank = ranks.get(base, 0)
                if tier == 0 and (base in words or (0 < base_rank <= OOV_LOW)):
                    stats["drop"] += 1
                    if base in words:
                        forms.setdefault(word, base)
                    continue
                roots[word] = base

            words[word] = {"ipa": to_ipa(row.get("phonetic") or ""),
                           "zh": zh, "tier": tier, "rank": rank}
            stats["tier4" if tier == 4 else ("tier6" if tier == 6 else "oov")] += 1
            for form in parse_exchange(row.get("exchange") or ""):
                if form != word and form not in words:
                    forms[form] = word

    # 变形只在原形已收录时才保留（避免指向未收录的词）
    forms = {f: h for f, h in forms.items() if h in words and f not in words}
    roots = {w: b for w, b in roots.items()
             if w in words and b and b != w and b in words}

    data = {
        "_meta": {
            "source": "ECDICT (https://github.com/skywind3000/ECDICT, MIT License)",
            "built": time.strftime("%Y-%m-%d"),
            "tier4": stats["tier4"], "tier6": stats["tier6"], "oov": stats["oov"],
            "oov_rank_window": [OOV_LOW, OOV_HIGH],
            "forms": len(forms), "roots": len(roots),
        },
        "words": dict(sorted(words.items())),
        "forms": dict(sorted(forms.items())),
        "roots": dict(sorted(roots.items())),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    print(f"✅ {OUT.relative_to(ROOT)}：四级 {stats['tier4']} / 六级 {stats['tier6']} / "
          f"超纲 {stats['oov']} / 变形 {len(forms)}，{OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
