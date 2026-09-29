#!/bin/zsh
# 更换 Echo 记录的「搜索路径」

cd "$(dirname "$0")"
PY=.venv/bin/python

if [ ! -x "$PY" ]; then
  echo "⚠️ 请先双击 launch.command 完成首次安装。"
  read -k 1 "?按任意键关闭…"
  exit 1
fi

echo "当前搜索路径："
$PY src/player.py --print-path 2>/dev/null || true
echo ""

DIR=$(osascript -e 'POSIX path of (choose folder with prompt "选择包含学习笔记 md 的文件夹：")' 2>/dev/null | tr -d '\n')
if [ -z "$DIR" ]; then
  echo "已取消，未做修改。"
  read -k 1 "?按任意键关闭…"
  exit 0
fi

$PY src/player.py --save-path "$DIR"
echo ""
read -k 1 "?已更新，按任意键关闭…"
