#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Echo —— 把《经济学人》学习笔记 md 转成「逐句跟读网页播放器」。

用法：
    python player.py "/Volumes/EAGET忆捷/英语学习/Economist/2026/The_Economist_Europe_-_28_March_2026_Britain_1.md"

可选参数：
    --voice  指定发音人（默认 en-GB-SoniaNeural，英音女声）
    --rate   语速（如 "+10%" 或 "-10%"）
    --out    输出目录（默认 ./output/<文章名>）
    --open   生成后自动用浏览器打开播放器
    --workers 并发合成数（默认 6）

依赖：
    pip install edge-tts
"""

import argparse
import asyncio
import html
import json
import re
import socket
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    import edge_tts
except ImportError:
    sys.exit("缺少依赖 edge-tts，请先安装：pip install edge-tts")

# 让进度实时显示（尤其在双击 .command 启动时）
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:  # noqa: BLE001
    pass

DEFAULT_VOICE = "en-GB-SoniaNeural"

# ---- 项目布局：源码在 src/，产物在 dist/，个人数据在 data/ ----
SRC_DIR = Path(__file__).resolve().parent
ROOT_DIR = SRC_DIR.parent
DATA_DIR = ROOT_DIR / "data"      # config / vocab / progress（不入库）
DIST_DIR = ROOT_DIR / "dist"      # 音频与生成的页面（不入库）
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass

# ---- 搜索路径配置（记住用户的学习资料目录）----
CONFIG_FILE = DATA_DIR / "config.json"
DEFAULT_SEARCH_DIR = "/Volumes/EAGET忆捷/英语学习/Economist/2026"


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def get_search_dirs():
    return [d for d in (load_config().get("search_dirs") or []) if isinstance(d, str)]


def save_search_dir(d):
    """把目录记到 config.json（置顶去重）。"""
    d = str(Path(d).expanduser())
    cfg = load_config()
    dirs = [x for x in (cfg.get("search_dirs") or []) if isinstance(x, str) and x != d]
    dirs.insert(0, d)
    cfg["search_dirs"] = dirs
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dirs


# ---- 生词本（服务模式存文件，静态模式存浏览器 localStorage）----
VOCAB_FILE = CONFIG_FILE.with_name("vocab.json")
_vocab_lock = threading.Lock()


def load_vocab():
    try:
        data = json.loads(VOCAB_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def _save_vocab(items):
    VOCAB_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def vocab_add(entry):
    word = str((entry or {}).get("word", "")).strip()
    if not word:
        return load_vocab()
    entry = {k: v for k, v in entry.items() if k in
             ("word", "zh", "en", "note", "src", "a", "i")}
    entry["word"] = word
    entry["saved_at"] = datetime.now().isoformat(timespec="seconds")
    with _vocab_lock:
        items = [x for x in load_vocab()
                 if str((x or {}).get("word", "")).lower() != word.lower()]
        items.insert(0, entry)
        _save_vocab(items)
        return items


def vocab_remove(word):
    with _vocab_lock:
        items = [x for x in load_vocab()
                 if str((x or {}).get("word", "")).lower() != str(word).lower()]
        _save_vocab(items)
        return items


def vocab_clear():
    with _vocab_lock:
        _save_vocab([])
    return []


# ---- 学习进度（标记文章已完成）----
PROGRESS_FILE = CONFIG_FILE.with_name("progress.json")
_progress_lock = threading.Lock()


def load_progress():
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def progress_mark(aid, done):
    aid = str(aid or "").strip()
    if not aid:
        return load_progress()
    with _progress_lock:
        data = load_progress()
        if done:
            data[aid] = datetime.now().isoformat(timespec="seconds")
        else:
            data.pop(aid, None)
        PROGRESS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
        return data

# OCR 连字 / 特殊字符规范化
LIGATURES = {
    "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
    "ﬅ": "st", "ﬆ": "st", "œ": "oe", "æ": "ae", "Æ": "AE", "Œ": "OE",
    "▸": "", "▹": "", "▪": "", "•": "",
}

# 常见缩写，避免在其后错误断句
ABBREV = {
    "Mr", "Mrs", "Ms", "Dr", "Prof", "St", "vs", "etc",
    "e.g", "i.e", "U.S", "U.K", "No", "no", "approx", "Jr", "Sr",
}

# 该 OCR 源里常见的排版痕迹，做安全修正
OCR_FIXES = [
    (re.compile(r"\bO NE\b"), "One"),
    (re.compile(r"\bThE\b"), "The"),
]


def clean_text(s: str) -> str:
    s = html.unescape(s)
    for k, v in LIGATURES.items():
        s = s.replace(k, v)
    for pat, rep in OCR_FIXES:
        s = pat.sub(rep, s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def strip_tags(s: str) -> str:
    # 高亮 span 保留内部文字
    s = re.sub(r"<span[^>]*>(.*?)</span>", r"\1", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", "", s, flags=re.S)
    return s


def extract_paragraphs(md_text: str):
    """提取 <div class="original">…</div> 里的英文正文。"""
    paras = []
    for m in re.finditer(r'<div class="original">(.*?)</div>', md_text, re.S | re.I):
        raw = clean_text(strip_tags(m.group(1)))
        if raw:
            paras.append(raw)
    return paras


def _ends_abbrev(s: str) -> bool:
    tokens = s.split()
    if not tokens:
        return False
    last = tokens[-1].rstrip(".!?…\"”’')")
    return last in ABBREV or bool(re.search(r"[A-Z]\.$", s.rstrip()))


def split_sentences(text: str):
    """按句末标点切分，保留常见缩写。"""
    raw = re.split(r"(?<=[.!?…])\s+", text)
    sentences = []
    for piece in raw:
        piece = piece.strip()
        if not piece:
            continue
        if sentences and _ends_abbrev(sentences[-1]):
            sentences[-1] += " " + piece
        else:
            sentences.append(piece)
    return sentences


def extract_vocab(md_text: str):
    """解析「## 词汇速查表」里的表格。"""
    vocab = []
    seen = set()
    in_vocab = False
    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## 词汇速查表"):
            in_vocab = True
            continue
        if in_vocab:
            if stripped.startswith("## "):
                break
            if stripped.startswith("|"):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if (
                    len(cells) >= 4
                    and cells[0]
                    and cells[0] != "单词"
                    and not set("".join(cells)) <= {"-", " ", "|"}
                ):
                    w = cells[0]
                    if w.lower() not in seen:
                        seen.add(w.lower())
                        vocab.append({
                            "word": w,
                            "phonetic": cells[1],
                            "meaning": cells[2],
                            "example": cells[3],
                        })
    return vocab


async def _synth(text: str, voice: str, rate: str, out_path: Path):
    tts = edge_tts.Communicate(text, voice, rate=rate)
    await tts.save(str(out_path))


async def generate_audio(sentences, voice, rate, out_dir, concurrency, skip_existing=False):
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(sentences)
    sem = asyncio.Semaphore(concurrency)
    done = 0
    lock = asyncio.Lock()

    async def worker(i, text):
        nonlocal done
        path = out_dir / f"sent_{i:04d}.mp3"
        if skip_existing and path.exists() and path.stat().st_size > 0:
            async with lock:
                done += 1
                print(f"\r  🔊 合成进度 {done}/{total}", end="", flush=True)
            return i, path, None
        ok = False
        last_err = None
        for attempt in range(4):
            try:
                async with sem:
                    await _synth(text, voice, rate, path)
                ok = True
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                await asyncio.sleep(1.0 * (attempt + 1))
        async with lock:
            done += 1
            print(f"\r  🔊 合成进度 {done}/{total}", end="", flush=True)
        return i, path if ok else None, last_err if not ok else None

    tasks = [asyncio.create_task(worker(i, t)) for i, t in enumerate(sentences)]
    results = await asyncio.gather(*tasks)
    print()
    return results


def load_template():
    tpl = Path(__file__).with_name("template.html")
    return tpl.read_text(encoding="utf-8")


def build_player(title, sentences, audio_files, vocab, out_dir):
    data = [
        {"text": s, "audio": a}
        for s, a in zip(sentences, audio_files)
    ]
    tpl = load_template()
    tpl = tpl.replace("__TITLE__", html.escape(title))
    tpl = tpl.replace("__SENTENCES_JSON__", json.dumps(data, ensure_ascii=False))
    tpl = tpl.replace("__VOCAB_JSON__", json.dumps(vocab, ensure_ascii=False))
    out = out_dir / "player.html"
    out.write_text(tpl, encoding="utf-8")
    return out


def parse_md(md_path):
    """解析单个 md，返回 (title, sentences, vocab)。"""
    md_text = md_path.read_text(encoding="utf-8")
    title = md_path.stem.replace("_", " ")
    paras = extract_paragraphs(md_text)
    sentences = []
    for p in paras:
        sentences.extend(split_sentences(p))
    sentences = [s for s in sentences if len(s) >= 2]
    vocab = extract_vocab(md_text)
    return title, sentences, vocab


def collect_md_files(root):
    """递归收集目录下所有 md（跳过隐藏文件与 ._* 苹果系统文件）。"""
    root = Path(root)
    files = []
    for p in sorted(root.rglob("*.md")):
        if p.name.startswith("."):
            continue
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts[:-1]):
            continue
        files.append(p)
    return files


def short_label(stem):
    """把文件名缩短成侧栏标签，例如 …_28_March_2026_Britain_1 -> Britain 1。"""
    parts = stem.split("_-_")
    tail = parts[-1] if len(parts) > 1 else stem
    bits = tail.split("_")
    if len(bits) >= 4 and bits[0].isdigit():
        label = " ".join(bits[3:])
        return label or tail.replace("_", " ")
    return tail.replace("_", " ")


def build_playlist(articles, out_root):
    tpl = Path(__file__).with_name("playlist_template.html").read_text(encoding="utf-8")
    tpl = tpl.replace("__ARTICLES_JSON__", json.dumps(articles, ensure_ascii=False))
    out = out_root / "playlist.html"
    out.write_text(tpl, encoding="utf-8")
    return out


def synth_article(sentences, args, out_dir, incremental=False):
    """合成/复用音频，返回与 sentences 等长的文件名列表（失败为 None）。"""
    out_dir.mkdir(parents=True, exist_ok=True)

    def _run(skip_existing):
        results = asyncio.run(
            generate_audio(sentences, args.voice, args.rate, out_dir, args.workers,
                           skip_existing=skip_existing)
        )
        files = [None] * len(sentences)
        failed = 0
        for i, path, err in results:
            if path is None:
                failed += 1
                print(f"  ⚠️ 第 {i + 1} 句合成失败：{err}")
            else:
                files[i] = path.name
        if failed:
            print(f"  ⚠️ 有 {failed} 句失败，已跳过。")
        return files

    if args.skip_tts:
        files = [f"sent_{i:04d}.mp3" for i in range(len(sentences))]
        if all((out_dir / f).exists() for f in files):
            print(f"  ♻️ 复用已有 {len(files)} 个 MP3")
            return files
        print("  ⚠️ --skip-tts：存在缺失音频，自动补合成缺失部分")
        return _run(skip_existing=True)

    if incremental:
        print("  🎙️  增量合成（自动跳过已存在的 MP3）")
    else:
        print(f"  🎙️  使用发音人 {args.voice}，语速 {args.rate}")
    return _run(skip_existing=incremental)


def run_single(args):
    md_path = Path(args.md).expanduser()
    if not md_path.exists():
        sys.exit(f"找不到文件：{md_path}")

    title, sentences, vocab = parse_md(md_path)
    if not sentences:
        sys.exit('没有在文档里找到英文正文（<div class="original">）')
    print(f"📄 解析文档：{md_path.name}")
    print(f"📖 提取英文句子 {len(sentences)} 句，生词 {len(vocab)} 个")

    out_dir = Path(args.out) if args.out else DIST_DIR / md_path.stem
    audio_files = synth_article(sentences, args, out_dir)

    good_sentences, good_files = [], []
    for s, a in zip(sentences, audio_files):
        if a:
            good_sentences.append(s)
            good_files.append(a)

    player = build_player(title, good_sentences, good_files, vocab, out_dir)

    print(f"\n✅ 完成！输出目录：{out_dir}")
    print(f"   🎧 播放器：{player}")
    print(f"   🎵 MP3 数量：{len(good_files)} 个")

    if args.open:
        subprocess.Popen(["open", str(player)])


def run_playlist(args):
    root = Path(args.dir).expanduser()
    if not root.is_dir():
        sys.exit(f"目录不存在：{root}")

    out_root = Path(args.out) if args.out else DIST_DIR
    out_root.mkdir(parents=True, exist_ok=True)

    md_files = collect_md_files(root)
    if not md_files:
        sys.exit(f"在目录里没有找到 md 文件：{root}")
    print(f"🗂️  发现 {len(md_files)} 篇 md，输出到 {out_root}")

    articles = []
    for n, md in enumerate(md_files, 1):
        title, sentences, vocab = parse_md(md)
        if not sentences:
            print(f"  ⏭️  跳过（无英文正文）：{md.name}")
            continue
        article_dir = out_root / md.stem
        audio_files = synth_article(sentences, args, article_dir, incremental=True)
        sents, audios = [], []
        for s, a in zip(sentences, audio_files):
            if a:
                sents.append(s)
                audios.append(a)
        articles.append({
            "title": title,
            "label": short_label(md.stem),
            "sentences": [{"text": s, "audio": f"{md.stem}/{a}"} for s, a in zip(sents, audios)],
            "vocab": vocab,
        })
        print(f"  ✅ [{n}/{len(md_files)}] {short_label(md.stem)}：{len(sents)} 句")

    player = build_playlist(articles, out_root)
    total_sent = sum(len(a["sentences"]) for a in articles)

    print(f"\n✅ 完成！共 {len(articles)} 篇、{total_sent} 句")
    print(f"   🎧 播放列表：{player}")

    if args.open:
        subprocess.Popen(["open", str(player)])


# ---------------------------------------------------------------------------
# 服务模式：页面秒开，音频在后台并发生成、按需即取
# ---------------------------------------------------------------------------

class TtsEngine:
    """后台线程里跑一个 asyncio 事件循环，统一控制并发合成与去重。

    - 预生成：固定工作池 + 队列（不会把任务全塞进队列抢资源）
    - 按需：走独立的高优先级通道，点哪句立刻生成哪句
    """

    def __init__(self, voice, rate, concurrency):
        self.voice, self.rate = voice, rate
        self.prio = max(2, concurrency // 4)          # 留给按需的并发
        self.prefetch_workers = max(2, concurrency - self.prio)
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._prio_sem = None
        self._queue = None
        self._inflight = {}
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self._prio_sem = asyncio.Semaphore(self.prio)
        self._queue = asyncio.Queue()
        for _ in range(self.prefetch_workers):
            self.loop.create_task(self._prefetch_worker())
        self._ready.set()
        self.loop.run_forever()

    async def _prefetch_worker(self):
        while True:
            text, out_path = await self._queue.get()
            try:
                await self._ensure(text, out_path, prio=False)
            except Exception:  # noqa: BLE001
                pass  # 失败信息已在 _generate 里打印

    @staticmethod
    def is_ready(path):
        try:
            return path.exists() and path.stat().st_size > 0
        except OSError:
            return False

    async def _generate(self, text, out_path, prio):
        if self.is_ready(out_path):
            return
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if prio:
            await self._prio_sem.acquire()
        try:
            if self.is_ready(out_path):
                return
            for attempt in range(4):
                try:
                    tts = edge_tts.Communicate(text, self.voice, rate=self.rate)
                    tmp = out_path.with_name(out_path.name + ".part")
                    await tts.save(str(tmp))
                    tmp.replace(out_path)  # 原子落盘，避免半成品被读取
                    return
                except Exception as e:  # noqa: BLE001
                    if attempt == 3:
                        print(f"  ⚠️ 合成失败：{out_path.parent.name}/{out_path.name} -> {e}")
                        raise
                    await asyncio.sleep(1.0 * (attempt + 1))
        finally:
            if prio:
                self._prio_sem.release()

    async def _ensure(self, text, out_path, prio):
        if self.is_ready(out_path):
            return
        key = str(out_path)
        fut = self._inflight.get(key)
        if fut is None:
            fut = asyncio.ensure_future(self._generate(text, out_path, prio))
            self._inflight[key] = fut
            fut.add_done_callback(lambda f, k=key: self._inflight.pop(k, None))
        await asyncio.shield(fut)  # 同一句多处请求只生成一次

    def ensure(self, text, out_path, timeout=120):
        """按需合成并等待完成（供 HTTP 请求线程调用，高优先级）。"""
        fut = asyncio.run_coroutine_threadsafe(self._ensure(text, out_path, True), self.loop)
        return fut.result(timeout=timeout)

    def schedule(self, text, out_path):
        """排入后台预生成队列，不等待。"""
        self.loop.call_soon_threadsafe(self._queue.put_nowait, (text, out_path))


def find_free_port(start, tries=30):
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("找不到空闲端口")


def scan_articles(md_files, out_root):
    """解析 md，返回内部结构（含音频目标路径，尚未合成）。"""
    articles = []
    for md in md_files:
        title, sentences, vocab = parse_md(md)
        if not sentences:
            print(f"  ⏭️  跳过（无英文正文）：{md.name}")
            continue
        article_dir = out_root / md.stem
        articles.append({
            "title": title,
            "label": short_label(md.stem),
            "stem": md.stem,
            "vocab": vocab,
            "sentences": [
                {"text": s, "path": article_dir / f"sent_{i:04d}.mp3"}
                for i, s in enumerate(sentences)
            ],
        })
    return articles


def make_handler(page_html, articles, engine, status_payload):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(body)
                except BrokenPipeError:
                    pass

        def _json_out(self, obj):
            self._send(200, json.dumps(obj, ensure_ascii=False),
                       "application/json; charset=utf-8", {"Cache-Control": "no-store"})

        def _read_json(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                return json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:  # noqa: BLE001
                return {}

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html", "/playlist.html", "/player.html"):
                self._send(200, page_html, "text/html; charset=utf-8",
                           {"Cache-Control": "no-store"})
            elif path == "/status":
                self._json_out(status_payload())
            elif path == "/vocab":
                self._json_out({"items": load_vocab()})
            elif path == "/vocab/add":
                self._json_out({"items": vocab_add(self._read_json())})
            elif path == "/vocab/remove":
                self._json_out({"items": vocab_remove(self._read_json().get("word", ""))})
            elif path == "/vocab/clear":
                self._json_out({"items": vocab_clear()})
            elif path == "/progress":
                self._json_out({"completed": load_progress()})
            elif path == "/progress/mark":
                body = self._read_json()
                self._json_out({"completed": progress_mark(body.get("id", ""),
                                                           bool(body.get("done")))})
            elif path.startswith("/audio/"):
                self._audio(path)
            else:
                self._send(404, "not found")

        do_HEAD = do_GET
        do_POST = do_GET

        def _audio(self, path):
            m = re.match(r"^/audio/(\d+)/(\d+)\.mp3$", path)
            if not m:
                return self._send(404, "bad audio path")
            ai, si = int(m.group(1)), int(m.group(2))
            if ai >= len(articles) or si >= len(articles[ai]["sentences"]):
                return self._send(404, "out of range")
            sent = articles[ai]["sentences"][si]
            fpath = sent["path"]
            try:
                if not TtsEngine.is_ready(fpath):
                    engine.ensure(sent["text"], fpath)  # 按需生成
                data = fpath.read_bytes()
            except Exception as e:  # noqa: BLE001
                return self._send(500, f"生成失败：{e}")
            self._send_bytes(data, "audio/mpeg")

        def _send_bytes(self, data, ctype):
            total = len(data)
            rng = self.headers.get("Range")
            if rng:
                m = re.match(r"bytes=(\d*)-(\d*)", rng)
                if m:
                    start = int(m.group(1)) if m.group(1) else 0
                    end = int(m.group(2)) if m.group(2) else total - 1
                    end = min(end, total - 1)
                    if start <= end:
                        chunk = data[start:end + 1]
                        self.send_response(206)
                        self.send_header("Content-Type", ctype)
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
                        self.send_header("Content-Length", str(len(chunk)))
                        self.end_headers()
                        if self.command != "HEAD":
                            try:
                                self.wfile.write(chunk)
                            except BrokenPipeError:
                                pass
                        return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(total))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(data)
                except BrokenPipeError:
                    pass

    return Handler


def run_serve(args):
    if args.dir:
        root = Path(args.dir).expanduser()
        if not root.is_dir():
            sys.exit(f"目录不存在：{root}")
        md_files = collect_md_files(root)
        if not md_files:
            sys.exit(f"在目录里没有找到 md 文件：{root}")
        kind = "playlist"
    else:
        md = Path(args.md).expanduser()
        if not md.exists():
            sys.exit(f"找不到文件：{md}")
        md_files = [md]
        kind = "single"

    out_root = Path(args.out) if args.out else DIST_DIR
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"🗂️  解析 {len(md_files)} 篇 md（不等合成）…")
    articles = scan_articles(md_files, out_root)
    if not articles:
        sys.exit("没有可用的文章")

    total = sum(len(a["sentences"]) for a in articles)
    ready0 = sum(1 for a in articles for s in a["sentences"] if TtsEngine.is_ready(s["path"]))
    print(f"📖 共 {len(articles)} 篇、{total} 句（已有 {ready0} 句音频）")

    # 生成页面（音频走 /audio 接口，按需即时生成）
    if kind == "playlist":
        page_data = [{
            "title": a["title"], "label": a["label"], "id": a["stem"], "vocab": a["vocab"],
            "sentences": [
                {"text": s["text"], "audio": f"/audio/{ai}/{si}.mp3"}
                for si, s in enumerate(a["sentences"])
            ],
        } for ai, a in enumerate(articles)]
        tpl = Path(__file__).with_name("playlist_template.html").read_text(encoding="utf-8")
        page_html = tpl.replace("__ARTICLES_JSON__", json.dumps(page_data, ensure_ascii=False))
    else:
        a = articles[0]
        page_data = [{"text": s["text"], "audio": f"/audio/0/{si}.mp3"}
                     for si, s in enumerate(a["sentences"])]
        tpl = Path(__file__).with_name("template.html").read_text(encoding="utf-8")
        page_html = (tpl
                     .replace("__TITLE__", html.escape(a["title"]))
                     .replace("__SENTENCES_JSON__", json.dumps(page_data, ensure_ascii=False))
                     .replace("__VOCAB_JSON__", json.dumps(a["vocab"], ensure_ascii=False)))

    engine = TtsEngine(args.voice, args.rate, args.workers)

    def status_payload():
        arts, tot, rdy = [], 0, 0
        for a in articles:
            mask, r = [], 0
            for s in a["sentences"]:
                ok = TtsEngine.is_ready(s["path"])
                mask.append("1" if ok else "0")
                r += 1 if ok else 0
            tot += len(mask)
            rdy += r
            arts.append({"ready": r, "mask": "".join(mask)})
        return {"total": tot, "ready": rdy, "done": rdy >= tot, "articles": arts}

    Handler = make_handler(page_html, articles, engine, status_payload)
    port = find_free_port(args.port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"

    # 后台预生成：按文章/句子顺序排队，先到先得
    for a in articles:
        for s in a["sentences"]:
            engine.schedule(s["text"], s["path"])

    print(f"\n▶️  已启动：{url}")
    print(f"   页面秒开；后台预生成 {engine.prefetch_workers} 路，另有 {engine.prio} 路留给「点哪句即时生成」。")
    print("   停止：在本窗口按 Ctrl+C")
    if args.open:
        subprocess.Popen(["open", url])
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 已停止")


def main():
    ap = argparse.ArgumentParser(description="Echo：md 转逐句跟读播放器")
    ap.add_argument("md", nargs="?", help="单个学习笔记 md 文件路径")
    ap.add_argument("--dir", default=None, help="扫描目录下所有 md，生成带播放列表的播放器")
    ap.add_argument("--voice", default=DEFAULT_VOICE, help="edge-tts 发音人（默认 %(default)s）")
    ap.add_argument("--rate", default="+0%", help='语速，如 "+10%%" / "-10%%"（默认 %(default)s）')
    ap.add_argument("--out", default=None, help="输出根目录（默认 dist/）")
    ap.add_argument("--workers", type=int, default=12, help="并发合成数（默认 12）")
    ap.add_argument("--serve", action="store_true", help="服务模式：页面秒开，音频后台生成、按需即取")
    ap.add_argument("--port", type=int, default=8756, help="服务端口（默认 8756，占用则自动顺延）")
    ap.add_argument("--skip-tts", action="store_true", help="复用已有 MP3，仅重建页面")
    ap.add_argument("--open", action="store_true", help="生成后自动用浏览器打开")
    ap.add_argument("--save-path", metavar="DIR", default=None, help="记录一个搜索路径到 config.json")
    ap.add_argument("--print-path", action="store_true", help="打印已记录的首个搜索路径")
    args = ap.parse_args()

    # 记录 / 查看搜索路径
    if args.save_path:
        dirs = save_search_dir(args.save_path)
        print("📌 已记录搜索路径：")
        for d in dirs:
            print(f"   • {d}")
        return
    if args.print_path:
        dirs = get_search_dirs()
        if dirs:
            print(dirs[0])
        return

    # 不带参数时：使用已记录的搜索路径，并默认走服务模式
    if not args.dir and not args.md:
        dirs = [d for d in get_search_dirs() if Path(d).is_dir()]
        if not dirs:
            ap.print_help()
            print("\n提示：可用 --save-path <目录> 记录搜索路径。")
            sys.exit(1)
        args.dir = dirs[0]
        args.serve = True
        print(f"📁 使用已记录的搜索路径：{args.dir}")

    if args.serve:
        run_serve(args)
    elif args.dir:
        run_playlist(args)
    elif args.md:
        run_single(args)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
