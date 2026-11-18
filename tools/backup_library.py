#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包资料库（dist/md）到备份目录，按份保留。

用法：
    .venv/bin/python tools/backup_library.py                 # 备份到 ~/Echo-backup
    .venv/bin/python tools/backup_library.py --keep 12       # 保留最近 12 份
    .venv/bin/python tools/backup_library.py --dest /Volumes/xxx/Echo-backup

也可以直接双击项目根目录的 backup-library.command。

为什么需要：dist/ 在 .gitignore 里（不进版本库），一旦被误删，只能靠
epub2md.py 从 EPUB 重新转换。备份 md 后可以直接解压恢复。
"""

import argparse
import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT / "dist" / "md"
DEFAULT_DEST = Path.home() / "Echo-backup"
PREFIX = "echo-md-"


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def count_issues(src: Path) -> int:
    return sum(1 for d in src.iterdir() if d.is_dir() and not d.name.startswith("."))


def make_backup(src: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    stem = f"{PREFIX}{time.strftime('%Y%m%d-%H%M')}"
    out = dest / f"{stem}.zip"
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(src.rglob("*")):
            if path.is_file() and not path.name.startswith("."):
                z.write(path, path.relative_to(src))
                n += 1
    print(f"✅ 已备份 {n} 个文件 → {out}（{human(out.stat().st_size)}）")
    return out


def prune(dest: Path, keep: int):
    files = sorted(dest.glob(f"{PREFIX}*.zip"))
    for old in files[:-keep] if keep > 0 else []:
        old.unlink()
        print(f"🗑  删除旧备份 {old.name}")


def main():
    ap = argparse.ArgumentParser(description="备份资料库（dist/md）")
    ap.add_argument("--src", default=str(DEFAULT_SRC), help="要备份的目录（默认 dist/md）")
    ap.add_argument("--dest", default=str(DEFAULT_DEST), help="备份存放目录（默认 ~/Echo-backup）")
    ap.add_argument("--keep", type=int, default=8, help="保留最近几份（默认 8，0 表示不删）")
    args = ap.parse_args()

    src = Path(args.src).expanduser()
    dest = Path(args.dest).expanduser()
    if not src.is_dir():
        sys.exit(f"❌ 找不到资料库：{src}")
    issues = count_issues(src)
    if not issues:
        sys.exit(f"❌ {src} 里没有期号目录，可能路径不对或资料库被清空")

    print(f"📚 {src}：{issues} 期")
    free = shutil.disk_usage(dest if dest.exists() else dest.parent).free
    print(f"💾 备份目录可用空间：{human(free)}")

    make_backup(src, dest)
    prune(dest, args.keep)
    kept = sorted(dest.glob(f"{PREFIX}*.zip"))
    print(f"📦 {dest} 现有 {len(kept)} 份备份"
          + (f"，最早 {kept[0].name}" if kept else ""))


if __name__ == "__main__":
    main()
