#!/bin/zsh
# 编译 macOS 原生 App：Echo.app
# 双击即可。会自动选择合适的 Swift 工具链（无需完整 Xcode）。

cd "$(dirname "$0")" || exit 1

APP="Echo.app"
CLT="/Library/Developer/CommandLineTools"

# 仅在终端（双击）运行时才等待按键，便于脚本化调用
pause() { if [ -t 0 ]; then read -k 1 "?按任意键关闭…"; fi; }

echo "🔨 编译 Echo.app …"

# ---- 选择可用的 Swift 工具链 ----
SW="swiftc"
SDK=""
if ! swiftc --version >/dev/null 2>&1; then
  # 系统 swiftc 不可用（常见原因：Xcode 许可未同意）→ 回退到 CLT 工具链
  if [ ! -x "$CLT/usr/bin/swiftc" ]; then
    echo "❌ 未找到可用的 Swift 编译器。请先安装命令行工具：xcode-select --install"
    pause
    exit 1
  fi
  echo "ℹ️  系统 swiftc 不可用（可能是 Xcode 许可未同意），改用 CLT 工具链编译。"
  echo "    （想彻底修复可自行执行：sudo xcodebuild -license accept）"
  SW="$CLT/usr/bin/swiftc"
  SDK=$(ls -d "$CLT"/SDKs/MacOSX*.sdk 2>/dev/null | sort -V | tail -1)
  export DEVELOPER_DIR="$CLT"
fi

run_swiftc() {
  if [ -n "$SDK" ]; then
    "$SW" -resource-dir "$CLT/usr/lib/swift" -sdk "$SDK" "$@"
  else
    "$SW" "$@"
  fi
}

# ---- 1) 先编译到临时目录；成功后才动 Echo.app（避免失败时毁掉旧 App）----
TMPECHO=$(mktemp -d)
trap 'rm -rf "$TMPECHO"' EXIT

echo "   编译主程序…"
if ! run_swiftc -O -o "$TMPECHO/Echo" src/macos/main.swift \
      -framework Cocoa -framework WebKit; then
  echo "❌ 编译失败，请把上面的报错发给我"
  pause
  exit 1
fi

ICON_OK=0
if [ -f tools/make_icon.swift ]; then
  if run_swiftc -O -o "$TMPECHO/make_icon" tools/make_icon.swift -framework AppKit 2>/dev/null; then
    ICON_OK=1
  fi
fi

# ---- 2) 组装 App bundle ----
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
mv "$TMPECHO/Echo" "$APP/Contents/MacOS/Echo"
cp src/macos/Info.plist "$APP/Contents/Info.plist"

# ---- 3) 图标 ----
if [ "$ICON_OK" = "1" ]; then
  ICONSET="$APP/Contents/Resources/Echo.iconset"
  if "$TMPECHO/make_icon" "$ICONSET" >/dev/null 2>&1 &&
     iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/Echo.icns" 2>/dev/null; then
    echo "🎨 图标已生成"
  else
    echo "⚠️ 图标生成失败（不影响使用，会显示默认图标）"
  fi
  rm -rf "$ICONSET"
fi

# ---- 4) 本地 ad-hoc 签名 ----
codesign --force --sign - "$APP" >/dev/null 2>&1 && echo "🔏 已完成本地签名"

touch "$APP"

echo ""
echo "✅ 已生成：$(pwd)/$APP"
echo "   双击 Echo.app 即可启动；关闭窗口后 Python 服务会自动停止。"
echo ""
open -R "$APP"
pause
