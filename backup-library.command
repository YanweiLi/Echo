#!/bin/zsh
# 备份 Echo 资料库（dist/md）到 ~/Echo-backup，双击即可
# 可用 BACKUP_DIR 环境变量改备份位置：
#   BACKUP_DIR=/Volumes/xxx/Echo-backup ./backup-library.command

cd "$(dirname "$0")" || exit 1

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  echo "❌ 找不到 .venv，请先双击 launch.command 初始化环境"
  read -k 1 "?按任意键关闭…"
  exit 1
fi

DEST_ARG=()
if [ -n "$BACKUP_DIR" ]; then
  DEST_ARG=(--dest "$BACKUP_DIR")
fi

echo "🗄  Echo 资料库备份"
echo ""
"$PY" tools/backup_library.py "${DEST_ARG[@]}"
echo ""
read -k 1 "?按任意键关闭…"
