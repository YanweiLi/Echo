#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生词标注：四六级 / 超纲分层 + 词形还原 + 噪声过滤。

数据：src/wordbank.json（tools/build_wordbank.py 从 ECDICT 生成）
      src/dictionary.json（早期人工整理的高频词，音标/释义质量更好，优先采用）

三个档位：
    4  四级（四六级都收的词算四级）
    6  仅六级
    0  超纲（四六级之外，但在常用词频段内）

过滤规则（目的都是“少标废话”）：
    · 词频排名 <= basic_rank 的词视为已会（about / political / government …）
    · 超纲词要求 ≥4 字母，且全文出现过小写形式（排除 Trump / Kremlin 这类专名）
    · 每个档位有配额，全篇总量也有上限，保证词汇表能排进一页
    · 每句最多标 per_sentence 个，越难越优先
"""

import json
import re
from functools import lru_cache
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
BANK_FILE = SRC_DIR / "wordbank.json"
CURATED_FILE = SRC_DIR / "dictionary.json"

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")
SUFFIX = ("'s", "’s", "'", "’")

GROUP_TITLES = {4: "CET-4 四级", 6: "CET-6 六级", 0: "超纲"}
GROUP_ORDER = (4, 6, 0)

BASIC_RANK = 1500          # 比这更常见的词不标
PER_SENTENCE = 3           # 每句最多几个上标
QUOTAS = {4: 18, 6: 14, 0: 10}
MAX_TERMS = 40             # 全篇上限（保证词汇表约一页）


@lru_cache(maxsize=1)
def bank():
    """({word: 词条}, {变形: 原形}, {派生词: 词根})，已用人名词典覆盖音标/释义。"""
    data = json.loads(BANK_FILE.read_text(encoding="utf-8"))
    words, forms = data["words"], data.get("forms") or {}
    roots = data.get("roots") or {}
    try:
        curated = json.loads(CURATED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        curated = {}
    for word, info in curated.items():
        entry = words.get(word)
        if entry:
            entry["ipa"] = info.get("ipa") or entry["ipa"]
            entry["zh"] = info.get("zh") or entry["zh"]
    return words, forms, roots


def lemma_candidates(word: str):
    """规则还原：返回可能的基本形（按可能性排序，不含自身）。"""
    c = []
    w = word
    if w.endswith("ies") and len(w) > 4:
        c.append(w[:-3] + "y")
    if w.endswith("ied") and len(w) > 4:
        c.append(w[:-3] + "y")
    if w.endswith("es") and len(w) > 3:
        c.append(w[:-2])
    if w.endswith("s") and not w.endswith(("ss", "us", "is")) and len(w) > 3:
        c.append(w[:-1])
    if w.endswith("ed") and len(w) > 3:
        c += [w[:-2], w[:-1]]
        if len(w) > 4 and w[-3] == w[-4]:
            c.append(w[:-3])          # stopped → stop
    if w.endswith("ing") and len(w) > 4:
        stem = w[:-3]
        c += [stem, stem + "e"]
        if len(stem) > 1 and stem[-1] == stem[-2]:
            c.append(stem[:-1])       # running → run
    if w.endswith("ly") and len(w) > 4:
        c += [w[:-2], w[:-2] + "e"]
    for suf in ("er", "est"):
        if w.endswith(suf) and len(w) > len(suf) + 2:
            stem = w[:-len(suf)]
            c += [stem, stem + "e"]
            if len(stem) > 1 and stem[-1] == stem[-2]:
                c.append(stem[:-1])
    seen, out = set(), []
    for cand in c:
        if cand and cand != word and cand not in seen:
            seen.add(cand)
            out.append(cand)
    return out


def _strip_suffix(token: str) -> str:
    low = token.lower().strip("'-")
    for suf in SUFFIX:
        if low.endswith(suf) and len(low) > len(suf) + 1:
            return low[:-len(suf)]
    return low


def resolve(token: str):
    """token → (原形, 词条)；查不到返回 None。

    优先还原到基本形（warning→warn、fighting→fight），
    这样词汇表里不会出现一堆 -ing / -ed 变体。
    """
    words, forms, _roots = bank()
    low = _strip_suffix(token)

    head = forms.get(low)
    if head:
        return head, words[head]
    entry = words.get(low)
    if entry:
        return low, entry
    for cand in lemma_candidates(low):
        entry = words.get(cand)
        if entry:
            return cand, entry
        head = forms.get(cand)
        if head:
            return head, words[head]
    return None


def _tokenize(sentence: str):
    return TOKEN_RE.findall(sentence)


def _lower_seen(paragraphs):
    """全文出现过小写形式的词（用来排除专有名词）。"""
    seen = set()
    for para in paragraphs:
        for sent in para:
            for tok in _tokenize(sent):
                if tok[0].islower():
                    seen.add(_strip_suffix(tok))
    return seen


def _candidates(paragraphs, basic_rank, tiers, max_oov, lower_seen):
    """全篇候选词（按首次出现顺序），尚未按配额裁剪。"""
    order, heads = [], set()
    oov = 0
    for para in paragraphs:
        for sent in para:
            for tok in _tokenize(sent):
                got = resolve(tok)
                if not got:
                    continue
                head, entry = got
                if head in heads:
                    continue
                tier = int(entry.get("tier", 0))
                if tier not in tiers:
                    continue
                rank = int(entry.get("rank") or 0)
                if rank and rank <= basic_rank:
                    continue                     # 太基础，视为已会
                if tier == 0:
                    if len(head) < 4 or head not in lower_seen:
                        continue                 # 专名 / 缩写 / 太短
                    if oov >= max_oov:
                        continue
                    oov += 1
                heads.add(head)
                order.append({"word": head, "text": tok, "ipa": entry.get("ipa", ""),
                              "zh": entry.get("zh", ""), "tier": tier, "rank": rank,
                              "example": sent if len(sent) <= 64
                              else sent[:61].rstrip() + "…"})
    return order


def _dedupe_roots(terms):
    """warning 和 warn 同时入选时只留词根。

    （giving、talking 这类已在建库时并入词根，这里处理四六级档的同族重复。）
    """
    _words, _forms, roots = bank()
    kept = [t["word"] for t in terms]
    out = []
    for term in terms:
        base = roots.get(term["word"])
        if base and base in kept:
            continue
        out.append(term)
    return out


def _apply_quotas(candidates, quotas, max_terms):
    """按档位配额裁剪，保持首次出现顺序。"""
    left = dict(quotas)
    picked = []
    for term in candidates:
        if len(picked) >= max_terms:
            break
        tier = term["tier"]
        if left.get(tier, 0) <= 0:
            continue
        left[tier] -= 1
        picked.append(term)
    return picked


def annotate(paragraphs, basic_rank: int = BASIC_RANK,
             per_sentence: int = PER_SENTENCE, quotas=None, max_terms: int = MAX_TERMS,
             tiers=None):
    """给段落里的句子标注生词。

    返回 (marked, terms)：
        marked  与 paragraphs 同形（段落 → 句子 → 该句标注的词）
        terms   全篇词条，按首次出现排序，已带编号 n（从 1 开始）
    tiers   只保留这些档位（None = 全部）
    """
    tiers = set(GROUP_ORDER if tiers is None else tiers)
    quotas = dict(QUOTAS if quotas is None else quotas)
    lower_seen = _lower_seen(paragraphs)
    candidates = _candidates(paragraphs, basic_rank, tiers,
                             quotas.get(0, MAX_TERMS), lower_seen)
    terms = _dedupe_roots(_apply_quotas(candidates, quotas, max_terms))
    for i, term in enumerate(terms, 1):
        term["n"] = i
    by_head = {t["word"]: t for t in terms}

    marked = []
    for para in paragraphs:
        p_marks = []
        for sent in para:
            hits, seen = [], set()
            for tok in _tokenize(sent):
                got = resolve(tok)
                if not got:
                    continue
                head, _entry = got
                if head in seen or head not in by_head:
                    continue
                seen.add(head)
                hits.append(by_head[head])
            hits.sort(key=lambda t: (t["tier"] != 0, t["tier"] != 6))
            p_marks.append(hits[:per_sentence])
        marked.append(p_marks)
    return marked, terms


def tier_counts(terms):
    return {tier: sum(1 for t in terms if t["tier"] == tier) for tier in GROUP_ORDER}


def groups(terms):
    """[(档位标题, [词条…]), …]，空的档位不出现；组内按出现顺序。"""
    out = []
    for tier in GROUP_ORDER:
        items = sorted((t for t in terms if t["tier"] == tier), key=lambda t: t["n"])
        if items:
            out.append((GROUP_TITLES[tier], items))
    return out


def ordered_by_group(terms):
    """把词条按 四级 → 六级 → 超纲 排好（组内保持首次出现顺序）。"""
    return [t for _title, items in groups(terms) for t in items]
