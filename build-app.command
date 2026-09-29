#!/bin/zsh
# 编译 macOS 原生 App：Echo.app
# 双击即可（使用系统自带的 swiftc，无需完整 Xcode）

cd "$(dirname "$0")" || exit 1

APP="Echo.app"

# 仅在终端（双击）运行时才等待按键，便于脚本化调用
pause() { if [ -t 0 ]; then read -k 1 "?按任意键关闭…"; fi; }

echo "🔨 编译 Echo.app …"

if ! command -v swiftc >/dev/null 2>&1; then
  echo "❌ 未找到 swiftc，请先安装命令行工具：xcode-select --install"
  pause
  exit 1
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# 1) 编译 Swift 主程序
swiftc -O -o "$APP/Contents/MacOS/Echo" src/macos/main.swift \
  -framework Cocoa -framework WebKit 2>&1 | tail -20
if [ ! -x "$APP/Contents/MacOS/Echo" ]; then
  echo "❌ 编译失败，请把上面的报错发给我"
  pause
  exit 1
fi

# 2) Info.plist
cp src/macos/Info.plist "$APP/Contents/Info.plist"

# 3) 生成图标
if [ -f tools/make_icon.swift ]; then
  ICONSET="$APP/Contents/Resources/Echo.iconset"
  if swiftc -O -o /tmp/echo-make-icon tools/make_icon.swift -framework AppKit 2>/dev/null &&
     /tmp/echo-make-icon "$ICONSET" >/dev/null &&
     iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/Echo.icns"; then
    echo "🎨 图标已生成"
  else
    echo "⚠️ 图标生成失败（不影响使用，会显示默认图标）"
  fi
  rm -rf "$ICONSET" /tmp/echo-make-icon
fi

# 4) 本地 ad-hoc 签名
codesign --force --sign - "$APP" >/dev/null 2>&1 && echo "🔏 已完成本地签名"

# 5) 让 Finder 立即刷新
touch "$APP"

echo ""
echo "✅ 已生成：$(pwd)/$APP"
echo "   双击 Echo.app 即可启动；关闭窗口后 Python 服务会自动停止。"
echo ""
open -R "$APP"
pause
