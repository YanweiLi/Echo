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
import hashlib
import html
import json
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

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
DIST_DIR = ROOT_DIR / "dist"      # 音频与生成的页面（不入库）
MD_ROOT = DIST_DIR / "md"         # EPUB 转出的资料库：dist/md/<期号>/*.md
PRINT_ROOT = DIST_DIR / "print"   # 整期合并的可打印 HTML

# 数据目录可被设置页更改；指向它的「指针」放在固定位置，不随数据目录移动
BOOTSTRAP_FILE = Path.home() / "Library" / "Application Support" / "Echo" / "settings.json"


def load_bootstrap():
    try:
        data = json.loads(BOOTSTRAP_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_bootstrap(cfg):
    try:
        BOOTSTRAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        BOOTSTRAP_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
    except OSError:
        pass


def resolve_data_dir():
    d = load_bootstrap().get("data_dir")
    if isinstance(d, str) and d.strip():
        return Path(d).expanduser()
    return ROOT_DIR / "data"


DATA_DIR = resolve_data_dir()
CONFIG_FILE = DATA_DIR / "config.json"          # 搜索路径
VOCAB_FILE = DATA_DIR / "vocab.json"            # 生词本
PROGRESS_FILE = DATA_DIR / "progress.json"      # 学习进度
TRANSLATIONS_FILE = DATA_DIR / "translations.json"  # 句子中译缓存
EXPLAINS_FILE = DATA_DIR / "explains.json"          # AI 句子拆解缓存
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass


def set_data_dir(new_dir):
    """更改数据目录（传空字符串则恢复默认），并刷新全局路径。"""
    global DATA_DIR, CONFIG_FILE, VOCAB_FILE, PROGRESS_FILE, TRANSLATIONS_FILE
    global EXPLAINS_FILE
    cfg = load_bootstrap()
    new_dir = (new_dir or "").strip()
    if new_dir:
        cfg["data_dir"] = str(Path(new_dir).expanduser())
    else:
        cfg.pop("data_dir", None)
    save_bootstrap(cfg)
    DATA_DIR = resolve_data_dir()
    CONFIG_FILE = DATA_DIR / "config.json"
    VOCAB_FILE = DATA_DIR / "vocab.json"
    PROGRESS_FILE = DATA_DIR / "progress.json"
    TRANSLATIONS_FILE = DATA_DIR / "translations.json"
    EXPLAINS_FILE = DATA_DIR / "explains.json"
    # 缓存对象换到新目录并重新加载
    TR_CACHE.path = TRANSLATIONS_FILE
    EXPLAIN_CACHE.path = EXPLAINS_FILE
    TR_CACHE.reset()
    EXPLAIN_CACHE.reset()
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return DATA_DIR


# ---- 搜索路径配置（记住用户的学习资料目录）----
DEFAULT_SEARCH_DIR = "/Volumes/EAGET忆捷/英语学习/Economist/2026"


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def get_search_dirs():
    return [d for d in (load_config().get("search_dirs") or []) if isinstance(d, str)]


def _count_subdirs(p):
    try:
        return sum(1 for d in Path(p).iterdir()
                   if d.is_dir() and not d.name.startswith("."))
    except OSError:
        return 0


def _save_config(cfg):
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    except OSError:
        pass


def save_search_dir(d):
    """把目录记到 config.json（置顶去重）。"""
    d = str(Path(d).expanduser())
    cfg = load_config()
    dirs = [x for x in (cfg.get("search_dirs") or []) if isinstance(x, str) and x != d]
    dirs.insert(0, d)
    cfg["search_dirs"] = dirs
    _save_config(cfg)
    return dirs


def remove_search_dir(d):
    """从 config.json 移除一个搜索路径。"""
    d = str(Path(d).expanduser())
    cfg = load_config()
    dirs = [x for x in (cfg.get("search_dirs") or []) if isinstance(x, str) and x != d]
    cfg["search_dirs"] = dirs
    _save_config(cfg)
    return dirs


def settings_payload():
    """设置页需要的全部路径信息。"""
    return {
        "project_root": str(ROOT_DIR),
        "src_dir": str(SRC_DIR),
        "dist_dir": str(DIST_DIR),
        "md_root": str(MD_ROOT),
        "print_root": str(PRINT_ROOT),
        "md_issues": _count_subdirs(MD_ROOT),
        "data_dir": str(DATA_DIR),
        "config_file": str(CONFIG_FILE),
        "vocab_file": str(VOCAB_FILE),
        "progress_file": str(PROGRESS_FILE),
        "translations_file": str(TRANSLATIONS_FILE),
        "translated_count": len(TR_CACHE),
        "explains_file": str(EXPLAINS_FILE),
        "explain_count": len(EXPLAIN_CACHE),
        "ai_provider": ai_config()["provider"],
        "ai_base_url": ai_config()["base_url"],
        "ai_model": ai_config()["model"],
        "ai_key_masked": mask_key(ai_config()["api_key"]),
        "ai_configured": ai_configured(),
        "ai_providers": AI_PROVIDERS,
        "bootstrap_file": str(BOOTSTRAP_FILE),
        "search_dirs": get_search_dirs(),
    }


def apply_data_dir(new_dir):
    """更改数据目录，返回新的路径信息。"""
    try:
        d = set_data_dir(new_dir)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    return {"ok": True, "data_dir": str(d), "config_file": str(CONFIG_FILE),
            "vocab_file": str(VOCAB_FILE), "progress_file": str(PROGRESS_FILE),
            "translations_file": str(TRANSLATIONS_FILE)}


def reveal_path(p):
    """在 Finder 中显示路径（仅允许项目内或已登记的搜索路径）。"""
    p = str(p or "").strip()
    if not p:
        return {"ok": False, "error": "路径为空"}
    path = Path(p).expanduser()
    try:
        target = path.resolve()
    except OSError:
        return {"ok": False, "error": "路径无效"}

    allowed = [ROOT_DIR.resolve(), DATA_DIR.resolve(), DIST_DIR.resolve()]
    ok = any(target == r or r in target.parents for r in allowed)
    if not ok:
        for d in get_search_dirs():
            try:
                rd = Path(d).expanduser().resolve()
                if target == rd or rd in target.parents:
                    ok = True
                    break
            except OSError:
                continue
    if not ok:
        return {"ok": False, "error": "不允许访问该路径"}
    if not path.exists():
        return {"ok": False, "error": "路径不存在"}

    if path.is_dir():
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["open", "-R", str(path)])
    return {"ok": True}


# ---- 按需缓存：惰性加载 + 原子落盘 + 线程安全（中译 / AI 拆解共用）----
class JsonCache:
    """一个 JSON 字典缓存（键 → 值），带条数上限与原子落盘。"""

    def __init__(self, path, limit=100000, trim=0.25):
        self.path = Path(path)
        self.limit = limit
        self.trim = trim
        self._data = None
        self._lock = threading.Lock()

    def _load(self):
        if self._data is None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._data = raw if isinstance(raw, dict) else {}
            except Exception:  # noqa: BLE001
                self._data = {}
        return self._data

    def reset(self):
        with self._lock:
            self._data = None

    def get(self, key):
        with self._lock:
            return self._load().get(key)

    def all(self):
        with self._lock:
            return self._load()

    def put(self, key, value):
        with self._lock:
            data = self._load()
            data[key] = value
            if self.limit and len(data) > self.limit:
                for k in list(data)[:max(1, int(self.limit * self.trim))]:
                    data.pop(k, None)
            self._save(data)
        return value

    def clear(self):
        with self._lock:
            self._data = {}
            self._save(self._data)

    def _save(self, data):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".part")
            tmp.write_text(json.dumps(data, ensure_ascii=False) + "\n",
                           encoding="utf-8")
            tmp.replace(self.path)      # 原子落盘，避免半成品被读取
        except OSError:
            pass

    def __len__(self):
        with self._lock:
            return len(self._load())


TR_CACHE = JsonCache(TRANSLATIONS_FILE, limit=100000)
EXPLAIN_CACHE = JsonCache(EXPLAINS_FILE, limit=5000)


def load_translations():
    """中译缓存（只读，设置页统计用）。"""
    return TR_CACHE.all()


def _tr_key(text):
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:16]


def _chunk_text(text, limit=450):
    """MyMemory 单次请求 ≤ 500 字节，长句按标点切段后分别翻译再拼起来。"""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for piece in re.split(r"(?<=[;:,.])\s+", text):
        if cur and len(cur) + len(piece) + 1 > limit:
            parts.append(cur)
            cur = piece
        else:
            cur = f"{cur} {piece}".strip()
    if cur:
        parts.append(cur)
    return parts or [text]


def _mt_piece(text):
    """MyMemory 免费接口（在 config.json 加 mymemory_email 可把额度提到 50k 字符/天）。"""
    params = {"q": text, "langpair": "en|zh-CN"}
    email = str(load_config().get("mymemory_email") or "").strip()
    if email:
        params["de"] = email
    req = urllib.request.Request(
        "https://api.mymemory.translated.net/get?" + urlencode(params),
        headers={"User-Agent": "Echo/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8", "ignore"))
    if data.get("quotaFinished"):
        raise RuntimeError("翻译额度已用完（可在 data/config.json 加 mymemory_email 提到 50k/天）")
    if int(data.get("responseStatus") or 0) != 200:
        raise RuntimeError(str(data.get("responseDetails") or "翻译服务返回异常"))
    zh = html.unescape(str((data.get("responseData") or {}).get("translatedText") or ""))
    zh = zh.strip()
    if not zh:
        raise RuntimeError("翻译结果为空")
    return zh


def translate_sentence(text):
    """按需翻译一句：先查本地缓存（秒回、不耗额度），未命中才联网。"""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return {"ok": False, "error": "文本为空"}
    key = _tr_key(text)
    hit = TR_CACHE.get(key)
    if hit:
        return {"ok": True, "zh": hit, "cached": True}
    try:
        zh = " ".join(_mt_piece(p) for p in _chunk_text(text))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    TR_CACHE.put(key, zh)
    return {"ok": True, "zh": zh, "cached": False}


# ---- AI 句子拆解（OpenAI 兼容接口 + 本地永久缓存）----
PROMPT_VERSION = "e1"      # 改提示词后调大，旧缓存自动失效

AI_PROVIDERS = [
    {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
     "model": "deepseek-chat"},
    {"id": "zhipu", "name": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "model": "glm-4-flash"},
    {"id": "moonshot", "name": "Kimi（Moonshot）", "base_url": "https://api.moonshot.cn/v1",
     "model": "moonshot-v1-8k"},
    {"id": "dashscope", "name": "通义千问（DashScope 兼容模式）",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "model": "qwen-plus"},
    {"id": "siliconflow", "name": "硅基流动 SiliconFlow",
     "base_url": "https://api.siliconflow.cn/v1",
     "model": "Qwen/Qwen2.5-7B-Instruct"},
    {"id": "ollama", "name": "本机 Ollama（离线）", "base_url": "http://127.0.0.1:11434/v1",
     "model": "qwen2.5:7b"},
    {"id": "custom", "name": "自定义", "base_url": "", "model": ""},
]

EXPLAIN_KEYS = ("skeleton", "grammar", "phrases", "reference", "translation", "pattern")

EXPLAIN_SYSTEM = (
    "你是一位面向中文母语者的英语精读老师，学生正在读《经济学人》。"
    "讲解要短、准、有用：说清「作者为什么这样写」，而不是罗列词典义。"
    "只输出 JSON 对象，不要任何解释文字、不要代码块标记。"
)


def ai_config():
    cfg = load_config()
    return {
        "provider": str(cfg.get("ai_provider") or "deepseek"),
        "base_url": str(cfg.get("ai_base_url") or "").strip().rstrip("/"),
        "api_key": str(cfg.get("ai_api_key") or "").strip(),
        "model": str(cfg.get("ai_model") or "").strip(),
    }


def ai_configured():
    c = ai_config()
    return bool(c["base_url"] and c["model"])


def mask_key(k):
    """只回打码后的 Key，前端永远拿不到明文。"""
    k = str(k or "")
    if not k:
        return ""
    return (k[:5] + "…" + k[-4:]) if len(k) > 12 else "…" + k[-4:]


def _ai_chat(messages, json_mode=True, timeout=90, max_tokens=1400):
    """调 OpenAI 兼容的 /chat/completions（国内厂商 + Ollama 通吃）。"""
    c = ai_config()
    if not c["base_url"] or not c["model"]:
        raise RuntimeError("未配置 AI：请在设置页填写接口地址与模型名")
    payload = {"model": c["model"], "messages": messages, "temperature": 0.2,
               "max_tokens": max_tokens, "stream": False}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json", "User-Agent": "Echo/1.0"}
    if c["api_key"]:
        headers["Authorization"] = "Bearer " + c["api_key"]
    req = urllib.request.Request(c["base_url"] + "/chat/completions",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = str(((json.loads(e.read().decode("utf-8", "ignore")) or {})
                          .get("error") or {}).get("message") or "")[:160]
        except Exception:  # noqa: BLE001
            pass
        if e.code in (401, 403):
            raise RuntimeError("API Key 无效或没有权限" + (f"：{detail}" if detail else ""))
        if e.code == 404:
            raise RuntimeError("接口地址不对（404），检查 base_url 是否以 /v1 结尾")
        if e.code in (402, 429):
            raise RuntimeError("额度不足或请求过于频繁" + (f"：{detail}" if detail else ""))
        raise RuntimeError(f"接口返回 {e.code}" + (f"：{detail}" if detail else ""))
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), socket.timeout):
            raise RuntimeError("请求超时（模型响应太慢）")
        raise RuntimeError(f"连不上接口：{getattr(e, 'reason', e)}")
    except socket.timeout:
        raise RuntimeError("请求超时（模型响应太慢）")
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("接口返回里没有 choices 内容")
    return str((choices[0].get("message") or {}).get("content") or "").strip()


def _explain_messages(sentence, ctx):
    """带上下文提问：单句孤立时指代、语气根本没法讲。"""
    ctx = ctx or {}
    lines = [f"标题：{ctx.get('title') or '（无）'}"]
    if ctx.get("section"):
        lines.append(f"栏目：{ctx['section']}")
    if ctx.get("prev"):
        lines.append(f"上一句：{ctx['prev']}")
    lines.append(f"【要拆解的句子】{sentence}")
    if ctx.get("next"):
        lines.append(f"下一句：{ctx['next']}")
    user = "\n".join(lines) + "\n\n" + (
        "请拆解【要拆解的句子】，输出严格 JSON（六个字段）：\n"
        "{\n"
        '  "skeleton": "句子骨架：主干（主谓宾/表）+ 各从句与修饰成分的层次，中文说明，必要时保留英文关键词",\n'
        '  "grammar": [{"point": "语法点名，如 虚拟语气/倒装/分词独立结构/省略/长定语后置",'
        ' "explain": "这里为什么这样用、对意思有什么影响"}],\n'
        '  "phrases": [{"text": "词或短语", "mean": "在本句中的意思",'
        ' "why": "为什么用这个词：语气/搭配/言外之意"}],\n'
        '  "reference": [{"word": "it/they/this/that 等", "refers": "具体指谁或什么（用英文原词）"}],\n'
        '  "translation": "地道中文翻译，不要逐字直译",\n'
        '  "pattern": "可仿写的句型模板（英文）+ 一句中文说明怎么用"\n'
        "}\n"
        "规则：grammar / phrases / reference 若本句没有就返回空数组；每项说明不超过 2 句；"
        "reference 必须指名（例：it → the affordability crisis）；句子有歧义时只给最合理的一种解读。"
    )
    return [{"role": "system", "content": EXPLAIN_SYSTEM},
            {"role": "user", "content": user}]


def _parse_explain(raw):
    """把模型输出解析成六段结构；解析失败时退回纯文本。"""
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    data = None
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                data = None
    if not isinstance(data, dict):
        return {"raw": text or "（模型没有返回内容）"}
    out = {}
    for k in EXPLAIN_KEYS:
        v = data.get(k)
        if k in ("grammar", "phrases", "reference"):
            out[k] = [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []
        else:
            out[k] = str(v or "").strip()
    if not any(out.values()):
        out["raw"] = text
    return out


_EXPLAIN_LOCK = threading.Lock()
_EXPLAIN_KEY_LOCKS = {}


def _explain_key_lock(key):
    """同一句的并发请求串行化：第二个请求等到第一个写进缓存后直接命中。"""
    with _EXPLAIN_LOCK:
        if len(_EXPLAIN_KEY_LOCKS) > 2000:
            _EXPLAIN_KEY_LOCKS.clear()
        lk = _EXPLAIN_KEY_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _EXPLAIN_KEY_LOCKS[key] = lk
        return lk


def explain_sentence(sentence, ctx=None, force=False):
    """按需拆解一句：先查缓存；未命中才调 AI（同句并发只调一次）。"""
    sentence = re.sub(r"\s+", " ", str(sentence or "")).strip()
    if not sentence:
        return {"ok": False, "error": "文本为空"}
    ctx = ctx or {}
    c = ai_config()
    ctx_brief = " | ".join(str(ctx.get(k) or "") for k in ("title", "section", "prev", "next"))
    key = hashlib.sha1("\u0000".join(
        [sentence, ctx_brief, PROMPT_VERSION, c["model"]]).encode("utf-8")).hexdigest()[:16]

    if not force:
        hit = EXPLAIN_CACHE.get(key)
        if hit:
            return {"ok": True, "cached": True, "model": hit.get("model", ""),
                    "data": hit.get("data")}
    if not ai_configured():
        return {"ok": False, "need_config": True,
                "error": "未配置 AI：请在设置页选择厂并填写 API Key 与模型名"}

    with _explain_key_lock(key):
        hit = EXPLAIN_CACHE.get(key)          # 双重检查：并发时第二个直接命中
        if hit and not force:
            return {"ok": True, "cached": True, "model": hit.get("model", ""),
                    "data": hit.get("data")}
        try:
            raw = _ai_chat(_explain_messages(sentence, ctx))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        data = _parse_explain(raw)
        EXPLAIN_CACHE.put(key, {"at": datetime.now().isoformat(timespec="seconds"),
                                "model": c["model"], "data": data})
        return {"ok": True, "cached": False, "model": c["model"], "data": data}


def save_ai_settings(body):
    """保存 AI 配置；api_key 传空字符串表示「保持原值不变」。"""
    body = body or {}
    cfg = load_config()
    for src, dst in (("provider", "ai_provider"), ("base_url", "ai_base_url"),
                     ("model", "ai_model")):
        v = body.get(src)
        if isinstance(v, str) and v.strip():
            cfg[dst] = v.strip()
    if isinstance(body.get("api_key"), str) and body["api_key"].strip():
        cfg["ai_api_key"] = body["api_key"].strip()
    if body.get("clear_key"):
        cfg.pop("ai_api_key", None)
    _save_config(cfg)
    c = ai_config()
    return {"ok": True, "configured": ai_configured(), "provider": c["provider"],
            "base_url": c["base_url"], "model": c["model"],
            "key_masked": mask_key(c["api_key"])}


def ai_test():
    """用最小请求验证 Key / 地址 / 模型是否可用。"""
    if not ai_configured():
        return {"ok": False, "error": "请先填写接口地址与模型名"}
    t0 = time.time()
    try:
        out = _ai_chat([{"role": "user", "content": "Reply with exactly: ok"}],
                       json_mode=False, timeout=40, max_tokens=24)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    return {"ok": True, "ms": int((time.time() - t0) * 1000),
            "model": ai_config()["model"], "reply": out[:60]}


# ---- 生词本（服务模式存文件，静态模式存浏览器 localStorage）----
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
    m = re.match(r"\s*#\s+(.+)", md_text)
    h1 = m.group(1).strip() if m else ""
    # 老数据的 H1 就是文件名，仍用「去下划线」版；新数据用真正的文章标题
    title = h1 if (h1 and h1 != md_path.stem) else md_path.stem.replace("_", " ")
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


# ---------------------------------------------------------------------------
# 「期 → 文章」索引库：索引只读文件名（启动快），正文按需解析后缓存
# ---------------------------------------------------------------------------

def art_audio_dir(out_root, issue, stem):
    """音频目录：多期时按期号分folder，避免同名文件互相覆盖。"""
    return (Path(out_root) / issue / stem) if issue else (Path(out_root) / stem)


def art_payload(art):
    """给前端的文章数据（audio 走全局文章号 ai，切期号也不会乱）。"""
    return {
        "title": art["title"], "label": art["label"], "id": art["id"],
        "vocab": art["vocab"], "ai": art["ai"],
        "sentences": [
            {"text": s["text"], "audio": f"/audio/{art['ai']}/{si}.mp3"}
            for si, s in enumerate(art["sentences"])
        ],
    }


def issue_status(arts):
    """某一期的就绪掩码（供 /status?issue=… 使用）。"""
    rows, tot, rdy = [], 0, 0
    for art in arts:
        mask, r = [], 0
        for s in art["sentences"]:
            ok = TtsEngine.is_ready(s["path"])
            mask.append("1" if ok else "0")
            r += 1 if ok else 0
        tot += len(mask)
        rdy += r
        rows.append({"ready": r, "mask": "".join(mask)})
    return {"total": tot, "ready": rdy, "done": tot > 0 and rdy >= tot, "articles": rows}


class Library:
    """多期目录：含子目录且子目录里有 md 时，按子目录分期。"""

    def __init__(self, root, out_root):
        self.root = Path(root)
        self.out_root = Path(out_root)
        self.issues = []          # [{"id","label","count"}]
        self.flat = []            # 全局顺序（ai 即下标）
        self._by_issue = {}
        self._arts = {}
        self._lock = threading.Lock()
        self._scan()

    def _scan(self):
        groups = []
        # 只看「目录本身」的 md（非递归）；有则按单期处理，与老数据行为一致
        root_mds = [p for p in sorted(self.root.glob("*.md"))
                    if not p.name.startswith(".")]
        if root_mds:
            files = collect_md_files(self.root)      # 递归，老行为：含子目录 md
            groups = [("", files)]
            if len(files) > len(root_mds):
                print(f"📂 目录本身含 md → 按单期处理"
                      f"（连同子目录共 {len(files)} 篇）")
        else:
            for d in sorted(self.root.iterdir()):
                if d.is_dir() and not d.name.startswith("."):
                    mds = collect_md_files(d)
                    if mds:
                        groups.append((d.name, mds))
        if not groups or not groups[0][1]:
            sys.exit(f"在目录里没有找到 md 文件：{self.root}")
        for iid, mds in groups:
            bucket = []
            for md in mds:
                meta = {"ai": len(self.flat), "issue": iid, "stem": md.stem,
                        "path": md, "label": short_label(md.stem)}
                self.flat.append(meta)
                bucket.append(meta)
            self._by_issue[iid] = bucket
            self.issues.append({"id": iid, "label": iid or self.root.name,
                                "count": len(mds)})

    def ensure(self, ai):
        """解析（并缓存）第 ai 篇。"""
        art = self._arts.get(ai)
        if art is not None:
            return art
        with self._lock:
            art = self._arts.get(ai)
            if art is not None:
                return art
            meta = self.flat[ai]
            title, sentences, vocab = parse_md(meta["path"])
            adir = art_audio_dir(self.out_root, meta["issue"], meta["stem"])
            art = {
                "ai": ai, "issue": meta["issue"], "stem": meta["stem"],
                "title": title or meta["label"], "label": meta["label"],
                "id": f"{meta['issue']}/{meta['stem']}" if meta["issue"] else meta["stem"],
                "vocab": vocab,
                "sentences": [{"text": s, "path": adir / f"sent_{i:04d}.mp3"}
                              for i, s in enumerate(sentences)],
            }
            self._arts[ai] = art
            return art

    def ensure_issue(self, issue_id):
        return [self.ensure(m["ai"]) for m in self._by_issue.get(issue_id, [])]

    def payload(self, issue_id):
        return [art_payload(a) for a in self.ensure_issue(issue_id)]

    def status(self, issue_id):
        return issue_status(self.ensure_issue(issue_id))


class SingleArticle:
    """单文件模式：接口与 Library 保持一致。"""

    def __init__(self, art):
        self.art = art
        self.issues = []
        self.flat = [art]

    def ensure(self, ai=0):
        return self.art

    def ensure_issue(self, issue_id=""):
        return [self.art]

    def payload(self, issue_id=""):
        return [art_payload(self.art)]

    def status(self, issue_id=""):
        return issue_status([self.art])


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


def make_handler(page_html, lib, engine, status_payload, on_issue_loaded=None):
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
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/", "/index.html", "/playlist.html", "/player.html"):
                self._send(200, page_html, "text/html; charset=utf-8",
                           {"Cache-Control": "no-store"})
            elif path == "/status":
                self._json_out(status_payload(
                    parse_qs(parsed.query).get("issue", [""])[0]))
            elif path == "/issues":
                self._json_out({"issues": lib.issues})
            elif path.startswith("/issue/"):
                # 按需加载某一期（首次会解析该期 md，并后台预热其音频）
                iid = unquote(path[len("/issue/"):])
                try:
                    payload = lib.payload(iid)
                except Exception as e:  # noqa: BLE001
                    return self._send(500, f"加载失败：{e}")
                if on_issue_loaded:
                    threading.Thread(target=on_issue_loaded, args=(iid,),
                                     daemon=True).start()
                self._json_out(payload)
            elif path == "/translate":
                self._json_out(translate_sentence(
                    parse_qs(parsed.query).get("text", [""])[0]))
            elif path == "/explain":
                q = parse_qs(parsed.query)
                ctx = {k: q.get(k, [""])[0] for k in ("title", "section", "prev", "next")}
                self._json_out(explain_sentence(q.get("text", [""])[0], ctx,
                                                q.get("force", ["0"])[0] == "1"))
            elif path == "/ai/status":
                c = ai_config()
                self._json_out({"configured": ai_configured(), "provider": c["provider"],
                                "base_url": c["base_url"], "model": c["model"],
                                "key_masked": mask_key(c["api_key"]),
                                "providers": AI_PROVIDERS,
                                "explains_file": str(EXPLAINS_FILE),
                                "explain_count": len(EXPLAIN_CACHE)})
            elif path == "/ai/settings":
                self._json_out(save_ai_settings(self._read_json()))
            elif path == "/ai/test":
                self._json_out(ai_test())
            elif path == "/ai/clear_cache":
                EXPLAIN_CACHE.clear()
                self._json_out({"ok": True, "explain_count": 0})
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
            elif path == "/settings":
                self._json_out(settings_payload())
            elif path == "/settings/add_search_dir":
                d = self._read_json().get("dir", "")
                if str(d or "").strip():
                    self._json_out({"ok": True, "search_dirs": save_search_dir(d)})
                else:
                    self._json_out({"ok": False, "error": "路径为空",
                                    "search_dirs": get_search_dirs()})
            elif path == "/settings/remove_search_dir":
                self._json_out({"ok": True, "search_dirs":
                                remove_search_dir(self._read_json().get("dir", ""))})
            elif path == "/settings/data_dir":
                self._json_out(apply_data_dir(self._read_json().get("dir", "")))
            elif path == "/settings/reveal":
                self._json_out(reveal_path(self._read_json().get("path", "")))
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
            if ai >= len(lib.flat):
                return self._send(404, "out of range")
            try:
                art = lib.ensure(ai)      # 首次访问该篇时才解析
            except Exception as e:  # noqa: BLE001
                return self._send(500, f"解析失败：{e}")
            if si >= len(art["sentences"]):
                return self._send(404, "out of range")
            sent = art["sentences"][si]
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
    out_root = Path(args.out) if args.out else DIST_DIR
    out_root.mkdir(parents=True, exist_ok=True)

    if args.dir:
        root = Path(args.dir).expanduser()
        if not root.is_dir():
            sys.exit(f"目录不存在：{root}")
        lib = Library(root, out_root)
        kind = "playlist"
        cur_issue = lib.issues[0]["id"]
        print(f"🗂️  索引 {len(lib.issues)} 期 / {len(lib.flat)} 篇（正文与音频按需解析）")
        arts = lib.ensure_issue(cur_issue)
        ready0 = sum(1 for a in arts for s in a["sentences"] if TtsEngine.is_ready(s["path"]))
        print(f"📖 当前期「{cur_issue or root.name}」：{len(arts)} 篇、"
              f"{sum(len(a['sentences']) for a in arts)} 句（已有 {ready0} 句音频）")
    else:
        md = Path(args.md).expanduser()
        if not md.exists():
            sys.exit(f"找不到文件：{md}")
        title, sentences, vocab = parse_md(md)
        if not sentences:
            sys.exit('md 里没有可朗读的英文（需要 <div class="original"> 原文段）')
        art = {
            "ai": 0, "issue": "", "stem": md.stem, "title": title,
            "label": short_label(md.stem), "id": md.stem, "vocab": vocab,
            "sentences": [
                {"text": s,
                 "path": art_audio_dir(out_root, "", md.stem) / f"sent_{i:04d}.mp3"}
                for i, s in enumerate(sentences)
            ],
        }
        lib = SingleArticle(art)
        kind, cur_issue = "single", ""
        print(f"📖 {art['title']}：{len(sentences)} 句")

    # 生成页面（音频走 /audio 接口，按需即时生成）
    if kind == "playlist":
        tpl = Path(__file__).with_name("playlist_template.html").read_text(encoding="utf-8")
        page_html = (tpl
                     .replace("__ISSUES_JSON__", json.dumps(lib.issues, ensure_ascii=False))
                     .replace("__CURRENT_ISSUE__", json.dumps(cur_issue, ensure_ascii=False))
                     .replace("__ARTICLES_JSON__",
                              json.dumps(lib.payload(cur_issue), ensure_ascii=False)))
    else:
        a = lib.art
        page_data = [{"text": s["text"], "audio": f"/audio/0/{si}.mp3"}
                     for si, s in enumerate(a["sentences"])]
        tpl = Path(__file__).with_name("template.html").read_text(encoding="utf-8")
        page_html = (tpl
                     .replace("__TITLE__", html.escape(a["title"]))
                     .replace("__SENTENCES_JSON__", json.dumps(page_data, ensure_ascii=False))
                     .replace("__VOCAB_JSON__", json.dumps(a["vocab"], ensure_ascii=False)))

    engine = TtsEngine(args.voice, args.rate, args.workers)

    def prefetch_issue(issue_id):
        """把该期句子排入后台预生成队列（切期号时也会自动预热）。"""
        for art in lib.ensure_issue(issue_id):
            for s in art["sentences"]:
                engine.schedule(s["text"], s["path"])

    Handler = make_handler(page_html, lib, engine, lib.status,
                           on_issue_loaded=prefetch_issue)
    port = find_free_port(args.port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"

    # 先排队「当前期」，其余期号在切过去时再预热
    prefetch_issue(cur_issue)

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
