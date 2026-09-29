# Echo 🎧

把《经济学人》英语学习笔记（md 文档）自动转成**可逐句跟读的网页播放器**，帮助练习英语听力与口语。

## 项目结构

源码与产物严格分开，仓库里只提交源码：

```
Echo/
├── src/                        ← 📦 源码
│   ├── player.py                   主程序
│   ├── template.html               单篇播放器模板
│   ├── playlist_template.html      播放列表模板
│   ├── dictionary.json             内置词典（511 词：中文 / 音标 / 难度）
│   └── macos/                      macOS 原生外壳（Swift + WKWebView）
├── tools/                      ← 🛠 转换与校验工具
│   ├── epub2md.py                  EPUB → 中间 md（含整期可打印 HTML）
│   ├── verify_md.py                md ↔ EPUB 逐句回查校验
│   ├── extract_dict.py             从历史脚本抽取内置词典
│   └── make_icon.swift             应用图标生成器
├── launch.command              ← ⭐ 双击启动（网页版）
├── build-app.command           ← ⭐ 编译 macOS App
├── choose-folder.command       ← 更换搜索路径
├── requirements.txt
├── Echo.app                    ← 🖥 编译出的 App（不入库）
├── dist/                       ← 🎵 产物（不入库）
│   ├── md/<期号>/*.md              资料库（Echo 输入 + 可打印）
│   ├── print/<期号>.html           整期合并的可打印 HTML
│   └── <文章>/sent_XXXX.mp3        逐句音频
├── data/                       ← 🔒 个人数据（config / vocab / progress，不入库）
├── .venv/                      ← 运行环境（不入库）
└── README.md / LICENSE / .gitignore
```

## 一键启动（推荐）

双击 **`launch.command`** 即可，**会记住你的搜索路径，之后直接秒启**：

1. 首次运行：弹窗选择学习笔记文件夹 → **自动记住**（写入 `data/config.json`）
2. 之后运行：直接用记住的路径启动，不再弹窗
3. **页面秒开**（无需等待）：后台并发生成音频
4. 边听边生成：点哪句就**即时生成**哪句；顶部显示「⏳ 生成中 x/y」，未生成的句子灰显

> 要更换搜索路径：双击 **`choose-folder.command`**。
> 首次运行（或系统 Python 变动导致环境失效时）会自动重建环境（需要联网，约 1 分钟）。
> 停止服务：在终端窗口按 `Ctrl+C`。

### 已记录的搜索路径

当前记录在 `data/config.json`：

```json
{ "search_dirs": ["/Volumes/EAGET忆捷/英语学习/Economist/2026"] }
```

命令行管理：

```bash
python src/player.py --print-path                    # 查看已记录的路径
python src/player.py --save-path "/某个/目录"         # 记录/更换搜索路径
python src/player.py                                 # 不带参数：用记录的路径 + 服务模式启动
```

## macOS 原生 App（可选）

想把 Echo 做成一个真正的 macOS 程序（Dock 图标 + 独立窗口，不用浏览器）：

1. 双击 **`build-app.command`** 编译（约 10 秒，用系统自带的 `swiftc`，无需完整 Xcode）
2. 双击生成的 **`Echo.app`** → 打开独立窗口，内置浏览器直接加载播放器
3. **关闭窗口即退出**，Python 服务会自动停止（不会残留进程）

说明：

- `Echo.app` 需放在项目根目录（与 `src/`、`.venv/` 同级），它会自动定位项目
- 体积仅约 **1.5 MB**：不含 Python 运行时，复用项目里的 `.venv`
- 也可以拷到「应用程序」文件夹：App 会自动回退到 `~/Projects/Echo` 查找项目
- 源码：`src/macos/main.swift`；图标：`tools/make_icon.swift`

## 服务模式

`--serve` 会启动一个本地服务（默认 `http://127.0.0.1:8756/`）：

- 页面**立即打开**，不等待任何音频生成
- 后台**预生成**：默认 9 路并发，按顺序生成
- **按需优先**：你点某句时走独立通道（默认 3 路），约 1–2 秒即可播放
- 音频边生成边缓存在 `dist/` 下，下次启动直接复用

```bash
python src/player.py --dir "/Volumes/EAGET忆捷/英语学习/Economist/2026" --serve --open
```

## 数据源：EPUB → 中间 md（推荐） 📚

原始资料是《经济学人》的 **EPUB**（比 PDF 干净得多：无 OCR 连字、无断词粘连、段落完整）。
`tools/epub2md.py` 把 EPUB 转成**沿用本项目格式**的 md —— 这份 md **一物两用**：

```
EPUB ──[tools/epub2md.py]──> dist/md/<期号>/*.md ──┬──> Echo 播放（--dir，零代码改动）
                                                  └──> dist/print/<期号>.html（打印资料）
```

### 转换命令

```bash
# 1) 只统计结构（不写文件），用来确认解析规则
.venv/bin/python tools/epub2md.py "…/te_2026.01.03/TheEconomist.2026.01.03.epub" --probe

# 2) 转换一期（同时出整期打印版）
.venv/bin/python tools/epub2md.py "…/01_economist/te_2026.01.03" --print

# 3) 批量转换整个库（子目录按期号自动识别；已存在的跳过）
.venv/bin/python tools/epub2md.py "/Volumes/…/awesome-english-ebooks-master/01_economist" --print

# 4) 校验：md 里每个句子都能在源 EPUB 中找到
.venv/bin/python tools/verify_md.py "…/TheEconomist.2026.01.03.epub"
```

| 参数 | 说明 |
| ---- | ---- |
| `--probe` | 只统计章节 / 篇数 / 句数 / 栏目分布，不写文件 |
| `--out` | 输出根目录（默认 `dist/md`） |
| `--print` | 另出整期合并的可打印 HTML → `dist/print/<期号>.html` |
| `--limit N` | 只处理前 N 期 |
| `--force` | 覆盖已存在的 md |

### 解析规则（要点）

- **栏目**取自 `<span class="te_section_title">`，**标题**取自 `<h1 class="te_article_title">`，
  副标题取 `te_article_rubric`，日期取 `te_article_datePublished`
- 自动剔除：封面 / 目录 / 广告页 / 纯图片页 / 「This article was downloaded by …」水印段 /
  「For subscribers only …」推销段 / 文末 ■ 装饰符
- 命名 `<NNN>_<栏目>_-_<标题>.md` —— 刻意匹配 Echo 的侧栏标签规则，**播放器零改动**
- 断句直接复用 `player.py` 的 `split_sentences()`（含缩写表），与老数据保持一致

实测（77 期 / 5682 篇）：**逐句回查 0 丢句**，正文覆盖率 ~97%
（差额正是被剔除的水印 / 推销 / 元数据段），连字残留与断词粘连均为 0。

### md 格式契约（Echo 依赖，勿改）

1. 一句一个块：`<div class="container">` 内含 `.original`（原文，生词包 `<span style="color:red">`）
   + `.vocab`（`词 /ipa/ 释义<br>`）
2. `## 词汇速查表` 必须是 **4 列表格** `| 单词 | 音标 | 释义 | 示例 |`
   （`extract_vocab()` 要求 `len(cells) >= 4`，3 列会读不出侧栏生词）
3. 难度分组 `### ⭐ / ⭐⭐ / ⭐⭐⭐`（Echo 忽略，打印时有用）
4. 不加 YAML front-matter，不生成「中文翻译」章节

## 多期浏览（期号 → 文章） 🗓

`--dir` 指向的目录**本身没有 md、但有 md 子目录**时，按子目录分期：

```bash
.venv/bin/python src/player.py --dir dist/md --serve --open
```

- 启动**只读目录索引**（77 期 / 5682 篇也秒开），正文与音频**按需解析**
- 顶栏出现**期号下拉**；切换时 `GET /issue/<期号>` 拉那一期的文章，并自动预热该期音频
- 音频编号全局唯一（`/audio/<全局篇号>/<句号>.mp3`），换期不会错乱
- 老数据（目录里直接放 md）仍按**单期**处理，行为与以前完全一致

## 打印学习资料 🖨

`--print` 会为每期生成一份自包含 HTML（`dist/print/<期号>.html`）：
目录 + 每篇强制分页 + 原文与生词双栏 + 词汇速查表。

- 浏览器打开 → `⌘P` → 选 A4、双面，即可当纸质资料
- 单篇也可以直接打印对应的 md（VS Code 预览 / Typora 等）

## 功能

- 解析 md 中 `<div class="original">…</div>` 的英文正文，自动断句
- 用 **edge-tts**（微软神经网络语音，免费）逐句合成 MP3
- 单篇播放器 + **多篇播放列表**：
  - 原文逐句高亮跟随朗读，0.5× – 2× 变速
  - **播放设置**：单句循环 · 自动连播 · 循环模式（不循环 / **单篇循环** / **列表循环**）
  - 上一句/下一句，可跨文章跳转
  - 左侧播放列表切换文章，右侧生词速查（音标 / 释义 / 例句）
  - **点击正文任意单词即可查词**：弹出卡片显示中文翻译 + 英文释义，可一键朗读
  - **📒 生词本**：查词时点「☆ 生词本」收藏，可集中复习、朗读、跳回原句、导出
  - 键盘快捷键：`空格` 播放/暂停，`←` `→` 切换句子，`Esc` 关闭查词卡片/生词本

## 命令行用法

单篇：

```bash
python src/player.py "/Volumes/EAGET忆捷/英语学习/Economist/2026/The_Economist_Europe_-_28_March_2026_Britain_1.md" --open
```

整个目录生成播放列表：

```bash
python src/player.py --dir "/Volumes/EAGET忆捷/英语学习/Economist/2026" --open
```

## 常用参数

| 参数 | 说明 | 默认 |
| ---- | ---- | ---- |
| `--dir` | 扫描目录下所有 md，生成播放列表 | — |
| `--serve` | 服务模式：页面秒开，音频后台生成、按需即取 | 关 |
| `--port` | 服务端口（占用则自动顺延） | `8756` |
| `--voice` | 发音人（如 `en-US-JennyNeural` 美音、`en-GB-RyanNeural` 英音男声） | `en-GB-SoniaNeural` |
| `--rate` | 语速，如 `+10%` / `-10%` | `+0%` |
| `--out` | 输出根目录 | `dist/` |
| `--workers` | 并发合成数（服务模式下自动拆分为预生成 + 按需） | `12` |
| `--skip-tts` | 复用已有 MP3，仅重建页面 | 关 |
| `--open` | 生成后自动打开播放器 | 关 |

## 播放设置 ▶️

顶栏三个设置相互独立、可自由组合：

| 设置 | 选项 | 作用 |
| ---- | ---- | ---- |
| **🔁 单句** | 开 / 关 | 开启后反复朗读**当前这一句**（精听跟读） |
| **连播** | 开 / 关 | 开启后一句播完自动播下一句；关闭则每句播完停下 |
| **循环模式** | 不循环 | 播到最后一篇最后一句就停止 |
| | **单篇循环** | 一篇播完自动**重播该篇**（不跳到下一篇） |
| | **列表循环** | 全部播完**回到第一篇**继续（默认） |

举例：

- 想**精听一篇**：循环模式选「单篇循环」→ 这一篇会一直循环，适合反复磨耳朵
- 想**整库连着听**：循环模式选「列表循环」→ 所有文章依次播完再从头开始
- 想**精听一句**：打开「🔁 单句」→ 当前句反复读，配合「0.5×」变速跟读

## 学习进度 ✅

一篇一篇学，学完就标记：

- 焦点卡片右上角有 **「✓ 标记完成」**按钮；标记后变为「✓ 已完成（点击取消）」
- 左侧播放列表里已完成的文章会显示 **绿色 ✓**、标题变灰
- 列表标题显示总进度：**「32 篇 · 已完成 5」**
- 进度会自动保存，关掉浏览器/重启电脑都不丢。存储位置：
  - 服务模式 → `data/progress.json`（可手动编辑/备份）
  - 静态模式 → 浏览器 `localStorage`

`data/progress.json` 格式（以 md 文件名作为唯一标识）：

```json
{ "The_Economist_Europe_-_28_March_2026_Britain_1": "2026-09-29T09:02:10" }
```

## 生词本 📒

- 查词卡片上点 **「☆ 生词本」** 即可收藏（点「★ 已加入」可取消）
- 顶部 **「📒 N」** 按钮打开生词本：每条可 **🔊 朗读 / ↪ 跳回原句 / 🗑 删除**，还能 **导出** / **清空**
- 收藏内容：单词 + 中文释义 + 英文释义 + 原句上下文
- 存储位置：
  - 服务模式 → `data/vocab.json`（也可手动编辑/备份）
  - 静态模式 → 浏览器 `localStorage`

## 设置页 ⚙️

顶栏 **「⚙️」** 打开，可查看与修改：

| 项目 | 可否修改 | 说明 |
| ---- | ---- | ---- |
| **搜索路径** | ✅ 添加 / 移除 | 扫描 md 的目录；第一个为默认。新增后**重启 Echo** 才会重新扫描 |
| **数据目录** | ✅ 更改 / 恢复默认 | config / 生词本 / 学习进度 的存放位置，**改完立即生效** |
| 生词本文件 | 查看 | 完整路径，右侧 📂 可在 Finder 中显示 |
| 学习进度文件 | 查看 | 同上 |
| 产物目录 | 查看 | 音频与生成的页面所在处 |
| **📚 资料库** | ✅ 一键设为搜索路径 | EPUB 转出的 `dist/md`（按期号组织）与 `dist/print`（打印版） |
| 项目根目录 | 查看 | Echo 定位到的项目位置 |

- 数据目录的「指针」存在固定位置 `~/Library/Application Support/Echo/settings.json`，
  所以即使把数据目录改到移动硬盘，Echo 下次仍能找到。
- 设置页只在**服务模式**（`launch.command` / `Echo.app`）下可用；静态打开的 html 会自动隐藏该按钮。

## 产物目录（dist/）

```
dist/
├── playlist.html             ← 播放列表（多篇入口）
├── md/<期号>/                 ← EPUB 转出的资料库（Echo 数据源 + 可打印）
│   └── 008_Leaders_-_Title.md
├── print/<期号>.html          ← 整期合并的可打印 HTML
├── The_Economist_..._Britain_1/
│   ├── player.html           ← 单篇播放器
│   └── sent_0000.mp3 …       ← 逐句音频
└── …
```

## 数据目录（data/）

```
data/
├── config.json     ← 搜索路径
├── vocab.json      ← 生词本
└── progress.json   ← 学习进度
```

## 说明

- **推荐数据源**是 EPUB 转出的 md（`dist/md`）；PDF 转出的老 md 仍可直接播放，两种格式并存无冲突。
- 内置词典 `src/dictionary.json`（511 词：中文 / 音标 / 难度）用于原文生词红标与词汇速查表；
  由 `tools/extract_dict.py` 从历史脚本抽取，源脚本只读。
- md 文档中的 OCR 连字（如 `ﬁ`、`ﬂ`）会被自动规范化为 `fi`、`fl`；EPUB 转换的 md 不会出现该问题。
- 音质与音量取决于 edge-tts 服务；离线时可用系统 `say` 命令作为替代（本项目默认使用 edge-tts）。
- 查词与中文翻译使用免费的 Datamuse + MyMemory 接口，机器翻译偶尔不够精准，可交叉参考侧栏生词表（来自文档）。
