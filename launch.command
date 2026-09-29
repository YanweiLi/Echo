#!/bin/zsh
# Echo 一键启动器：双击即可运行
# 使用「已记录的搜索路径」直接启动；未记录时才弹窗选择并记住

cd "$(dirname "$0")"
PY=.venv/bin/python

# 1) 确保运行环境（首次、或系统 Python 变动导致失效时自动重建）
if ! $PY -c "import edge_tts" >/dev/null 2>&1; then
  echo "⏳ 正在准备运行环境（首次约 1 分钟）…"
  PYBIN=/usr/bin/python3
  [ -x "$PYBIN" ] || PYBIN=$(command -v python3)
  rm -rf .venv
  "$PYBIN" -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

# 2) 取已记录的搜索路径
DIR=$($PY src/player.py --print-path 2>/dev/null)

# 3) 未记录或目录不存在时：弹窗选择并记住
if [ -z "$DIR" ] || [ ! -d "$DIR" ]; then
  DIR=$(osascript -e 'POSIX path of (choose folder with prompt "选择包含学习笔记 md 的文件夹：")' 2>/dev/null | tr -d '\n')
  if [ -z "$DIR" ]; then
    echo "❌ 未选择文件夹。"
    echo "（要更换搜索路径，请双击 choose-folder.command）"
    read -k 1 "?按任意键关闭…"
    exit 1
  fi
  $PY src/player.py --save-path "$DIR" >/dev/null
  echo "📌 已记住搜索路径：$DIR"
fi

# 4) 启动
echo "🎧 启动中（页面马上打开，音频后台生成）…"
echo "   搜索路径：$DIR"
echo ""
$PY src/player.py --dir "$DIR" --serve --open

echo ""
read -k 1 "?服务已停止，按任意键关闭窗口…"
