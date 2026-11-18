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
│   ├── printing.py                 A4 讲义排版 + PDF 导出（App 与 CLI 共用）
│   ├── vocab.py                    生词标注：四级 / 六级 / 超纲分层
│   ├── wordbank.json               词库（约 1.4 MB，由 build_wordbank.py 生成）
│   ├── dictionary.json             人工校订层（511 词：中文 / 音标）
│   └── macos/                      macOS 原生外壳（Swift + WKWebView）
├── tools/                      ← 🛠 转换与校验工具
│   ├── epub2md.py                  EPUB → 中间 md（含整期可打印 HTML）
│   ├── verify_md.py                md ↔ EPUB 逐句回查校验
│   ├── backup_library.py           打包 dist/md 到 ~/Echo-backup
│   ├── build_wordbank.py           从 ECDICT 构建四六级词库
│   ├── extract_dict.py             从历史脚本抽取内置词典（校订层）
│   └── make_icon.swift             应用图标生成器
├── launch.command              ← ⭐ 双击启动（网页版）
├── build-app.command           ← ⭐ 编译 macOS App
├── choose-folder.command       ← 更换搜索路径
├── backup-library.command      ← 备份资料库（双击）
├── requirements.txt
├── Echo.app                    ← 🖥 编译出的 App（不入库）
├── dist/                       ← 🎵 产物（不入库）
│   ├── md/<期号>/*.md              资料库（Echo 输入 + 可打印）
│   ├── print/<期号>.html           整期合并的可打印 HTML
│   └── <文章>/sent_XXXX.mp3        逐句音频
├── data/                       ← 🔒 个人数据（config / vocab / progress / exports，不入库）
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

支持两种 EPUB 排版，自动识别：

| 格式 | 特征 | 识别方式 |
| --- | --- | --- |
| **新版**（官方/2024-04 之后） | `te_article_title` / `te_section_title` 等类名 | 有 `te_*` 元数据 |
| **旧版**（calibre 抓网页 / 2024-04 之前） | `feed_13/article_4/index_u2.html`，`calibre7/8/9` 类名 | 无 `te_*` 时回退 |

- 新版：**栏目**取自 `<span class="te_section_title">`，**标题**取自 `<h1 class="te_article_title">`，
  副标题取 `te_article_rubric`，日期取 `te_article_datePublished`
- 旧版：`<h4>`=小标题、`<h1>`=标题、`<h3>`=导语/日期，**栏目**取同级 `feed_N/index_*.html` 的 `<title>`
- 路径判定：`feed_N/article_M/index_uNN.html` 属于文章页（不能因为含 `index` 就被当成目录页跳过）
- 自动剔除：封面 / 目录 / 广告页 / 纯图片页 / 「This article was downloaded by …」水印段 /
  「For subscribers only …」「Read more from …」「Stay on top of …」推销段 / 文末 ■ 装饰符
- 命名 `<NNN>_<栏目>_-_<标题>.md` —— 刻意匹配 Echo 的侧栏标签规则，**播放器零改动**
- 断句直接复用 `player.py` 的 `split_sentences()`（含缩写表），与老数据保持一致

实测（252 期 / 18445 篇，2022.01 – 2026.11）：**逐句回查 0 丢句**，正文覆盖率 ~97%
（差额正是被剔除的水印 / 推销 / 元数据段），连字残留与断词粘连均为 0。

### md 格式契约（Echo 依赖，勿改）

1. 一句一个块：`<div class="container">` 内含 `.original`（原文，生词包 `<span style="color:red">`）
   + `.vocab`（`词 /ipa/ 释义<br>`）
2. `## 词汇速查表` 必须是 **4 列表格** `| 单词 | 音标 | 释义 | 示例 |`
   （`extract_vocab()` 要求 `len(cells) >= 4`，3 列会读不出侧栏生词）
3. 难度分组 `### CET-4 四级 / CET-6 六级 / 超纲`（Echo 忽略，打印时有用）
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

## 精读标记 ⭐

《经济学人》每期最值得精读的是 **Leaders（社论）**：短（30–60 句）、观点密集、
句式最讲究，且一期只有 5 篇左右，适合逐句抠。

- 播放列表里 Leaders 篇目左侧带 **「精读」** 徽标
- 点击列表标题栏的 **「精读」** 按钮 → 只显示本期 Leaders（计数变成 `精读 5 / 74 篇`），
  再点恢复全部；当前正在读的那篇始终保留在列表里，不会因为过滤而消失
- 其余判断依据只看栏目：`section == "Leaders"`（来自 md 的 `> Leaders · …` 那一行）

## 打印学习资料 🖨

### 在 App 里一键导出（推荐）

顶栏的 **🖨** 按钮可直接导出 A4 讲义 PDF（生成在 `data/exports/`，随后「在 Finder 中显示」或「用预览打开」）：

- **本周精选**：Leaders 优先 + 各栏目首篇，6 篇左右 ≈ 16 页（8 张纸双面）
- **当前这篇**：只导出正在读的那一篇，1–3 页
- **整期全部**：本期所有文章（约 200+ 页，只在需要时用）

排版与命令行版一致：封面目录 + 每篇从新页开始 + 句号编号 + 生词加粗点线 + 篇末词汇速查。
PDF 由本机 Chrome/Edge 无头模式生成；两者都没装时会退化为可打印 HTML（用浏览器 ⌘P 另存 PDF）。

### 命令行

`--print` 会为每期生成一份自包含 HTML（`dist/print/<期号>.html`）：
目录 + 每篇强制分页 + 原文与生词双栏 + 词汇速查表。

- 浏览器打开 → `⌘P` → 选 A4、双面，即可当纸质资料
- 单篇也可以直接打印对应的 md（VS Code 预览 / Typora 等）

### 每周精选 A4 版（推荐）

整期约 300 页，不适合日常打印。`--week` 只挑 6–8 篇（Leaders 优先 + 各栏目首篇），
每篇从新页开始，约 **16 页 A4**，可直接双面装订：

```bash
# 生成每周精选 HTML + PDF（PDF 需要本机装有 Chrome 或 Edge）
.venv/bin/python tools/epub2md.py "<EPUB>" --week --pdf

# 先看可选篇号（篇号即文件名前缀）
.venv/bin/python tools/epub2md.py "<EPUB>" --list-picks

# 手动指定篇目
.venv/bin/python tools/epub2md.py "<EPUB>" --week --pick 009,010,011 --pdf

# 只标注中级/高级词（默认含初级）
.venv/bin/python tools/epub2md.py "<EPUB>" --week --tiers 4,6 --pdf
```

- 输出：`dist/print/<期号>_week.html`、`dist/pdf/<期号>_week.pdf`
- 正文每句带编号，生词加粗 + 点状下划线（黑白打印也清楚），篇末 3 栏词汇速查
- 打印：A4 · 100% · 双面长边翻转 · 关闭页眉页脚

## 功能

- **📊 库规模常显**：标题右侧显示「252 期 · 18,445 篇 · 92 万句」，悬停看精确数字
- **⭐ 精读标记**：播放列表里社论（Leaders）带「精读」徽标，点标题栏的「精读」只看这些篇目
- **🎤 录音跟读对比**：每句左侧 🎤 录音，可与原音 A/B 播放（输出闭环）
- **🧩 语块本**：划选正文收藏可复用表达，按 1/2/4/8/15 天间隔复习（中文提示 → 你说 → 揭晓）
- **🌙 深色模式**：顶栏 ☀️/🌙 一键切换，首次自动跟随系统（`prefers-color-scheme`），选择记在 localStorage
- **🖨 一键导出 A4 讲义**：顶栏按钮选「本周精选 / 当前这篇 / 整期」，直接生成打印用 PDF（见「打印学习资料」）
- **全屏应用式布局**：顶栏 + 三栏（播放列表 / 正文 / 生词）铺满整个窗口，各栏内部滚动，页面本身不滚动；
  底部播放条始终可见且不会遮挡内容（窄屏自动退化为单列滚动）
- 解析 md 中 `<div class="original">…</div>` 的英文正文，自动断句
- 用 **edge-tts**（微软神经网络语音，免费）逐句合成 MP3
- 单篇播放器 + **多篇播放列表**：
  - 正在朗读的那一句在列表里**原地放大到 28px**（普通句 15.5px）并高亮，自动滚到视野中央；0.5× – 2× 变速
    （字号集中在 `--sent-size` / `--sent-active-size` 两个变量，想再调只改一处）
  - 每句左侧有独立的 **▶ / ⏸** 按钮：只播这一句、可暂停续播（见「播放设置」）
  - **播放设置**：单句循环 · 自动连播 · 循环模式（不循环 / **单篇循环** / **列表循环**）
  - 上一句/下一句，可跨文章跳转
  - 左侧播放列表切换文章，右侧生词速查（音标 / 释义 / 例句）
  - **点击正文任意单词即可查词**：弹出卡片显示中文翻译 + 英文释义，可一键朗读
  - **📒 生词本**：查词时点「☆ 生词本」收藏，可集中复习、朗读、跳回原句、导出
  - 键盘快捷键：`空格` 播放/暂停，`←` `→` 切换句子，`T` 显示/隐藏中译，`E` AI 拆解当前句，`Esc` 关闭查词卡片/抽屉/弹窗
  - **中文翻译默认不显示**：点句子右侧的 **「译」** 才展开，再点收起（见下）
  - **AI 精读拆解**：点句子右侧的 **「析」**，右侧抽屉给出六段式讲解（见下）

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
- 想**只听某一句**：点句子最左边的 **▶** → 只播这一句，播完就停，不会往后走；
  再点 **⏸** 暂停，第三次点从暂停处继续（不受「连播 / 循环」设置影响）

## 深色模式 🌙

顶栏的 **🌙 / ☀️** 按钮切换深色 / 浅色，选择会记住：

- 第一次打开时按系统外观自动选（macOS「深色」→ Echo 也是深色）
- 之后以你手动选过的为准，存在浏览器 `localStorage`（键 `echo_theme`）
- 主题在 `<head>` 里先行应用，刷新时不会白闪
- 深色配色统一走 CSS 变量：背景 `#14130f`、卡片 `#1c1b17`、强调色提亮为 `#e2674f`，
  生词档位徽标、查词卡片、AI 抽屉、设置/生词本弹窗都已适配

## 录音跟读 & 语块本 🎤🧩

### 录音对比（练「说」）

- 每句左侧 **🎤**：点一下开始录音（再点结束，60 秒自动停）
- 录完在句下出现一条：`🎧 1.2s  ▶ 原音  ▶ 我的  原音→我的  🗑`
  - **原音→我的**：先播 edge-tts 那句，播完自动接你的录音，最接近跟读对比
  - 播放速度跟随顶栏的变速设置（1.25× 对比也一致）
- 录音只存在当前页面内存里（换篇自动清空），不会写磁盘——避免占空间，也避免你还没准备好就被存档

> 需要浏览器麦克风权限。macOS 上 Echo.app 首次使用会弹系统授权。

### 语块本（练「自动冒出」）

语块 = **可复用的结构**（`Some fear ___ is coming`），不是单词。

- **怎么加**：在正文里划选 3–8 个词 → 浮出 **＋ 语块** → 填「中文提示」→ 保存
- **怎么复习**：顶栏 **🧩** → 「开始复习」→ 只显示中文提示（没填就显示挖空 `S··· f··· X i· c·····`）
  → 先自己说 → 点「揭晓」核对 → 选「忘了 / 模糊 / 记得」
- **间隔**：档位 1–5，间隔 1 / 2 / 4 / 8 / 15 天；「记得」升档，「忘了」掉回 1 档并当天重现
- **存储**：`data/chunks.json`（`{id, en, zh, src, book, box, due, seen, ok}`），跟随数据目录

> 顶栏 🧩 上的数字 = 今天待复习条数。

## 学习进度 ✅

一篇一篇学，学完就标记：

- 句子面板标题栏右侧有 **「✓ 标记完成」**按钮；标记后变为「✓ 已完成（点击取消）」
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
- **误点「清空」也能找回**：清空前会自动存一份到 `data/backup/vocab-<时间>.json`（保留最近 10 份）

## 阅读位置记忆（接着上次读） 🔖

一篇读不完没关系，Echo 会记住每篇读到第几句：

- 朗读/点击句子时自动记录（防抖 600 ms，关标签页时也会立刻落盘）
- 左侧列表显示已读进度：**`12/57 句`**（没读过的不显示，读完打 ✓）
- 再点开这篇 → **停在上次那一句**，不自动播放，句子下方提示「↩ 上次读到第 12 句」
- 列表标题栏的 **「继续上次」** → 直接跳到最近一次读到的位置（跨文章、跨期号）
- 已标记完成的文章不参与「继续上次」

存储位置：`data/positions.json`（`{篇 id: {i, n, at}}`，i 从 0 开始），
跟随「数据目录」设置；静态模式（直接打开 html）退化为 `localStorage`。

## 中文翻译（点击才显示） 🈶

默认**不显示任何中文**，避免边听边「偷看」：

- **逐句**：句子列表每行右侧有个淡色 **「译」** 按钮，点一下在句子下方展开中文，再点收起；多句可同时展开
- **快捷键 `T`**：展开/收起当前句译文，效果与点那一行的「译」相同（译文跟着句子显示）
- 首次点击需联网（约 2–4 秒，长句会按标点分段翻译再拼接）；**结果会永久缓存**，同一句以后再点秒回
- 缓存位置：`data/translations.json`（键是句子哈希，可在设置页看到文件路径与已缓存句数）

翻译引擎：**MyMemory** 免费接口（英文 → 简体中文）。

> ⚠️ 是**机器翻译**，适合「听不懂时对照一下」，不适合当精读依据（习语、双关容易翻错）。
> 免费额度约 5000 字符/天（约 40 句），用完会提示「额度已用完」，第二天恢复；
> 想提高额度：在 `data/config.json` 里加一行 `"mymemory_email": "你的邮箱"`（可提到 50k 字符/天）。
> 由于结果永久缓存，额度只会被「第一次翻译某句」消耗。

## AI 句子拆解（精读讲解） 🤖

遇到读不懂的长句，点句子右侧（或面板标题栏的 **「析」**，作用于当前句）的按钮，AI 会给出六段式精读讲解：

| 分区 | 内容 |
| --- | --- |
| 句子骨架 | 主干（主谓宾/表）+ 从句与修饰成分的层次 |
| 语法点 | 虚拟语气 / 倒装 / 分词独立结构 / 省略 / 长定语后置…，以及「为什么这样用」|
| 难词与短语 | 在本句中的意思 + 为什么用这个词（语气、搭配、言外之意） |
| 指代解析 | it / they / this 到底指谁，直接指名（如 `it → the affordability crisis`） |
| 地道翻译 | 比机翻自然的中文翻译 |
| 可仿写句型 | 句型模板 + 怎么用，方便自己造句 |

用法与行为：

- 结果在**右侧抽屉**里展示，不遮住原文；`Esc` 关闭，`E` 打开当前句拆解，抽屉顶部句子可点击重听
- 提问时**带上上下文**（文章标题 + 前后各一句）——单句孤立时指代和语气根本讲不准
- **结果永久缓存**（`data/explains.json`），同一句只请求一次；同一句并发点两次也只调一次上游
- 抽屉开着时切句 → **不会自动拆解**，只显示「拆解第 N 句」按钮（点了才花钱）
- 模型输出一律**转义后渲染**，不会执行模型返回的任何 HTML

### 配置（只需两项）

点顶栏 **⚙️** → **🤖 AI 拆解** → 填 **模型名** + **API Key** → **保存** → **测试连接**。

接口地址会**按模型名自动识别**，不用手填：

| 模型名示例 | 自动使用的接口 |
| --- | --- |
| `deepseek-chat` / `deepseek-reasoner` | `https://api.deepseek.com/v1` |
| `glm-4-flash` / `glm-4-plus` | `https://open.bigmodel.cn/api/paas/v4` |
| `moonshot-v1-8k` / `kimi-k2-…` | `https://api.moonshot.cn/v1` |
| `qwen-plus` / `qwen-max` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `Qwen/Qwen2.5-7B-Instruct`（含 `/`） | `https://api.siliconflow.cn/v1` |
| `qwen2.5:7b` / `llama3.1:8b`（含 `:`） | 本机 Ollama `http://127.0.0.1:11434/v1` |
| `gpt-4o-mini` / `o3-mini` | `https://api.openai.com/v1` |

- 认不出来（自建 / 中转 / 公司网关）→ 点「**高级：自定义接口地址**」手填即可；清空该框就回到自动识别
- 凡是 **OpenAI 兼容**的 `/chat/completions` 都能用
- API Key 只存在本机 `data/config.json`，界面上只显示打码值（如 `sk-…abcd`）

### 费用

- 按句按需调用，**同一句永远只调用一次**（永久缓存），基本可以忽略不计
- 参考量级：DeepSeek `deepseek-chat` 一句约 1000 token，单价约 ¥1–2/百万 token → **一句不到 1 分钱**
- 抽屉里的「重新生成」会忽略缓存重新请求（会再花一次），不确定时不要乱点

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

### 备份资料库 💾

`dist/` 不进版本库，误删就只能从 EPUB 重转（254 期约需 1 小时）。建议定期打包：

```bash
.venv/bin/python tools/backup_library.py            # → ~/Echo-backup/echo-md-<时间>.zip
.venv/bin/python tools/backup_library.py --keep 12  # 保留最近 12 份
.venv/bin/python tools/backup_library.py --dest /Volumes/xxx/Echo-backup
```

或直接双击根目录的 **backup-library.command**（可用 `BACKUP_DIR=` 指定位置）。

- 252 期 / 18445 篇 → 压缩后约 **103 MB**，耗时约 6 秒
- 恢复：`unzip ~/Echo-backup/echo-md-<时间>.zip -d dist/md`
- 默认保留最近 8 份，旧的自动删除

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
├── config.json        ← 搜索路径，以及可选的 AI 配置（ai_model / ai_api_key / 可选 ai_base_url）与 mymemory_email
├── vocab.json         ← 生词本
├── backup/            ← 清空生词本前的自动备份（保留最近 10 份）
├── progress.json      ← 学习进度（是否读完）
├── positions.json     ← 每篇读到第几句（接着上次读）
├── chunks.json        ← 语块本（可复用表达 + 复习档位）
├── exports/           ← 🖨 导出的 A4 讲义（HTML + PDF）
├── translations.json  ← 句子中译缓存（按需生成）
└── explains.json      ← AI 拆解缓存（按需生成）
```

## 生词生成算法 🔤

原文里的生词标注由 [src/vocab.py](src/vocab.py) 统一计算（App、导出讲义、EPUB→md 共用同一套结果）。

### 词库

`src/wordbank.json` 由 [tools/build_wordbank.py](tools/build_wordbank.py) 从
[ECDICT](https://github.com/skywind3000/ECDICT)（MIT）生成：

| 档位 | 来源 | 词数 |
| --- | --- | --- |
| `4` 四级 | ECDICT `cet4` 标签（四六级都收的算四级） | 3844 |
| `6` 六级 | 仅 `cet6` 标签 | 1952 |
| `0` 超纲 | 四六级之外，且 BNC 词频排名在 2000–15000 之间 | 7188 |

重建词库（首次会下载 63 MB 的 ecdict.csv 到 `/tmp`，之后可复用）：

```bash
.venv/bin/python tools/build_wordbank.py            # 或 --csv /path/to/ecdict.csv
```

### 匹配与过滤

1. **分词**：`[A-Za-z][A-Za-z'-]*`，去掉 `'s` 等所有格后缀
2. **还原原形**：变形表（ECDICT `exchange`，约 1.6 万条）→ 直接命中 → 规则还原
   （`-ies/-ed/-ing/-er/-est/-ly`、双写辅音等）。所以 `escalated → escalate`、
   `countries → country`，词汇表里不会出现一堆 `-ing`/`-ed` 变体
3. **过滤噪声**：
   - 词频排名 ≤ 1500 视为已会 → 不标（`about` 63、`political` 292、`government` 124 …）
   - 超纲词要求 ≥4 字母，且全文出现过小写形式 → 排除 `Trump`、`Kremlin`、`Islamic` 这类专名
   - 派生词并到词根（`giving → give`、`longer → long`）；同族词同时命中时只留词根
4. **配额**：每档最多 四级 18 / 六级 14 / 超纲 10，全篇上限 40 条；
   每句最多 3 个上标，越难越优先 → 词汇表稳定排进一页

结果分三档展示：正文里生词**加粗 + 点状下划线 + 上标编号**，篇末「词汇速查」按
`CET-4 四级 → CET-6 六级 → 超纲` 分节，编号与正文上标一一对应；App 侧栏生词条带档位徽标。

只标某一档：

```bash
.venv/bin/python tools/epub2md.py "<EPUB>" --week --tiers 0    # 只看超纲词
.venv/bin/python tools/epub2md.py "<EPUB>" --tiers 4,6        # 只看四六级
```

> 说明：ECDICT 是众包数据，个别音标/释义会有偏差（如 `liberty`），
> 这部分由 `src/dictionary.json` 的人工条目覆盖；仍不准确的可在该文件里补条目。

## 说明

- **推荐数据源**是 EPUB 转出的 md（`dist/md`）；PDF 转出的老 md 仍可直接播放，两种格式并存无冲突。
- **生词标注**见「生词生成算法」一节：词库 `src/wordbank.json`（约 1.4 MB，
  四级 3844 / 六级 1952 / 超纲 7188 + 1.6 万条变形），`src/dictionary.json`（511 词）
  作为人工校订层覆盖音标/释义。
- md 文档中的 OCR 连字（如 `ﬁ`、`ﬂ`）会被自动规范化为 `fi`、`fl`；EPUB 转换的 md 不会出现该问题。
- 音质与音量取决于 edge-tts 服务；离线时可用系统 `say` 命令作为替代（本项目默认使用 edge-tts）。
- 查词与中文翻译使用免费的 Datamuse + MyMemory 接口，机器翻译偶尔不够精准，可交叉参考侧栏生词表（来自文档）。
