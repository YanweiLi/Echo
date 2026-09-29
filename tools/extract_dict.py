#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性脚本：把旧脚本里的 VOCAB_DICT 抽成 src/dictionary.json

来源：/Volumes/EAGET忆捷/英语学习/Economist/scripts/add_vocab_translation.py
输出：src/dictionary.json
      { "mediate": {"zh": "调解，斡旋", "ipa": "/ˈmiːdieɪt/", "level": 2} }

用途：① EPUB→md 时做生词标注 ② Echo 查词「本地优先」
"""

import importlib.util
import json
from collections import Counter
from pathlib import Path

SRC = Path("/Volumes/EAGET忆捷/英语学习/Economist/scripts/add_vocab_translation.py")
OUT = Path(__file__).resolve().parent.parent / "src" / "dictionary.json"


def load_vocab_dict(src_path: Path):
    """直接加载旧脚本模块，取出 VOCAB_DICT（旧脚本有 __main__ 守卫，导入是安全的）。"""
    spec = importlib.util.spec_from_file_location("_legacy_vocab", src_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载：{src_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    vd = getattr(mod, "VOCAB_DICT", None)
    if not isinstance(vd, dict) or not vd:
        raise SystemExit("旧脚本里没找到可用的 VOCAB_DICT")
    return vd


def normalize(value):
    """兼容 (中文, 音标, '⭐') / (中文, 音标) / dict 三种写法。"""
    zh = ipa = ""
    level = 0
    if isinstance(value, (tuple, list)):
        if len(value) >= 1:
            zh = str(value[0]).strip()
        if len(value) >= 2:
            ipa = str(value[1]).strip()
        if len(value) >= 3:
            level = str(value[2]).count("⭐")
    elif isinstance(value, dict):
        zh = str(value.get("zh") or value.get("meaning") or "").strip()
        ipa = str(value.get("ipa") or value.get("pronunciation") or "").strip()
        level = str(value.get("level") or "").count("⭐")
    return {"zh": zh, "ipa": ipa, "level": level}


def main():
    if not SRC.exists():
        raise SystemExit(f"找不到源脚本：{SRC}")

    raw = load_vocab_dict(SRC)
    out = {}
    for word, value in raw.items():
        w = str(word).strip()
        if not w:
            continue
        item = normalize(value)
        if not item["zh"] and not item["ipa"]:
            continue
        out[w.lower()] = item

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")

    counts = Counter(v["level"] for v in out.values())
    print(f"✅ 已写出：{OUT}")
    print(f"   词条总数：{len(out)}（源 {len(raw)}）")
    for lv in sorted(counts):
        label = ("⭐" * lv) if lv else "（无难度）"
        print(f"   {label:<12} {counts[lv]} 条")
    print(f"   缺音标：{sum(1 for v in out.values() if not v['ipa'])}"
          f"    缺中文：{sum(1 for v in out.values() if not v['zh'])}")


if __name__ == "__main__":
    main()
