# -*- coding: utf-8 -*-
"""
================================================================================
 微信语音自动回复  ——  完整流程脚本
================================================================================

流程:
    读消息(解密微信数据库) → AI 生成回复 → GPT-SoVITS 合成 → 微信发语音条

特性:
    · 读消息走本地数据库解密,不依赖 UIA(微信 4.x 自绘界面下 UIA 不可用)
    · 发送走「虚拟声卡 + 鼠标模拟」,走微信官方语音条通道
    · 不需要 Hook / 协议逆向 / 改写微信内存

--------------------------------------------------------------------------------
 使用前提(缺一不可)
--------------------------------------------------------------------------------
  1. 微信桌面版已登录,窗口【最大化】
  2. Windows 默认【录音】设备 = 虚拟线缆录音端(如 Line 1 (Virtual Audio Cable))
     默认【播放】设备 = 扬声器(必须!否则系统声音会灌进语音条)
  3. GPT-SoVITS 的 api_v2.py 已在 127.0.0.1:9880 运行
  4. 微信版本建议 4.1.8~4.1.9(4.1.12/4.1.2 已无法登录)

--------------------------------------------------------------------------------
 安全说明
--------------------------------------------------------------------------------
  · 默认 **DRY-RUN**(只打印不发送)。确认无误后加 --live 才真正发送
  · 只回复 CHATS 里显式列出的会话,其他联系人绝不触碰
  · 发送前会核对会话标题,标题不符则跳过
================================================================================
"""

import argparse
import json
import os
import sys
import time

# ---------------------------------------------------------------------------
# 路径
#
# 本文件位于  Desktop/VoiceBot/01_核心模块/voice_bot.py
#   CORE    = Desktop/VoiceBot/01_核心模块   (wx_voice_sender / session_guard 等)
#   ROOT    = Desktop/VoiceBot               (状态文件、截图目录)
#   DESKTOP = Desktop                        (WeChatBot 项目与 GPT-SoVITS)
# ---------------------------------------------------------------------------
CORE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(CORE)
DESKTOP = os.path.dirname(ROOT)
PROJECT = os.path.join(DESKTOP, "WeChatBot_WXAUTO_SE-3.28")

if not os.path.isdir(PROJECT):
    print("找不到 WeChatBot 项目目录:", PROJECT)
    print("预期结构: 桌面/WeChatBot_WXAUTO_SE-3.28")
    sys.exit(1)

# ⚠️ 路径优先级很重要:
#    insert(0) 是"插到最前",所以**后插入的优先级更高**。
#    并且 WeChatBot 项目里存在同名旧副本(如 gptsovits_tts.py),
#    若它的优先级高于 CORE,就会把新版模块覆盖掉 —— 实测踩过这个坑
#    (改动不生效,一直在用旧代码)。
#    因此这里显式按"从低到高"的顺序插入,最终 CORE 优先级最高。
for _p in (os.path.join(PROJECT, "vendor_py"), PROJECT, CORE):
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

STATE_FILE = os.path.join(ROOT, "voice_bot_state.json")

# ===========================================================================
#  配置区 —— 按需修改
# ===========================================================================

# ---- AI 接口(默认读 WeChatBot 的 config.py,也可在此硬编码覆盖)----------
def _load_ai_config():
    """从 WeChatBot 的 config.py 读取 AI 配置。"""
    cfg = {"api_key": "", "base_url": "", "model": "", "temperature": 1.0, "max_tokens": 800}
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "wcb_config", os.path.join(PROJECT, "config.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        cfg["api_key"] = getattr(m, "DEEPSEEK_API_KEY", "")
        cfg["base_url"] = getattr(m, "DEEPSEEK_BASE_URL", "")
        cfg["model"] = getattr(m, "MODEL", "")
        cfg["temperature"] = getattr(m, "TEMPERATURE", 1.0)
        cfg["max_tokens"] = getattr(m, "MAX_TOKEN", 800)
    except Exception as e:
        print("[警告] 读取 config.py 失败,将使用下面的硬编码配置:", e)
    return cfg


AI = _load_ai_config()
# 如需覆盖,直接改这三行:
AI_KEY = os.environ.get("VOICE_BOT_KEY") or AI["api_key"]
AI_BASE = AI["base_url"]
AI_MODEL = AI["model"]

# 备用模型:config.py 里的模型若失效时依次回退。
#
# ⚠️ 坑:不同服务商的**模型名格式不同**!
#     OpenRouter  ->  "deepseek/deepseek-v4-pro"   (带 vendor 前缀)
#     DeepSeek官方 ->  "deepseek-v4-pro"            (不带前缀)
#     硅基流动     ->  "deepseek-ai/DeepSeek-V3"
#   写死一份名单会在别的服务商上**全部失效**(实测:
#   把 OpenRouter 的名单用在 api.deepseek.com 上,
#   报 "The supported API model names are ..." → 所有模型不可用)。
#
# 因此改为:优先**查询服务商真实支持的模型列表**,再从中挑。
AI_MODEL_FALLBACKS = [
    # 通用兜底(不带前缀,适配 DeepSeek 官方等多数兼容端点)
    "deepseek-v4-pro",
    "deepseek-flash",
    "deepseek-chat",
    "deepseek-reasoner",
    # OpenRouter 风格(仅在该服务商上有效,失败会被跳过)
    "deepseek/deepseek-v3.2",
    "deepseek/deepseek-v4-pro",
]
_working_model = {"id": None}


def _discover_models():
    """查询服务商实际支持的模型 ID 列表;失败返回 []。

    这样就不用猜模型名 —— 直接用它告诉我们能用的。
    """
    try:
        from openai import OpenAI
        c = OpenAI(api_key=AI_KEY, base_url=AI_BASE)
        return [m.id for m in c.models.list().data]
    except Exception as e:
        print("  [AI] 无法查询模型列表(%s),将按候选名单逐个试" % str(e)[:70])
        return []


def _candidate_models():
    """组装候选模型:配置的模型 -> 服务商真实列表 -> 通用兜底。"""
    cands = []
    if AI_MODEL:
        cands.append(AI_MODEL)
    for mid in _discover_models():
        if mid not in cands:
            cands.append(mid)
    for mid in AI_MODEL_FALLBACKS:
        if mid not in cands:
            cands.append(mid)
    return cands

# ---- 要自动回复的会话 -----------------------------------------------------
# ---- 要自动回复的会话 -----------------------------------------------------
#   key   = 会话标识。**三种写法都可以,程序会自动解析**:
#             · wxid_xxx     最稳(永不变),但普通用户看不到
#             · 微信号        好友资料页可见,直观
#             · 昵称/备注      最直观,但对方改了名就失效
#           (微信数据库里消息按 wxid 存,所以内部会先解析成 wxid)
#   value = {"name": 显示名, "row": 会话列表第几行(0起), "persona": 角色设定}
#           ⚠️ name 必须与微信里显示的名字一致 —— 发送前要用它 OCR 核对标题
#
#   ⚠️⚠️ row 是"会话列表从上往下的行号",**会随聊天活跃度变化**!
#       发错人的风险由此而来。降低风险:
#         1) 把要自动回复的对象【置顶】(置顶会话在最前,行号稳定)
#         2) 用 voice_bot.py --list 看当前打开的会话,对照界面校准
#         3) 发送前脚本会截图存档(05_文档\发送截图),可回查
#       另:即使 row 填错也**不会发错人** —— 发送前会 OCR 核对标题,
#          不符就中止本次发送。
CHATS = {
    # key 可以填三种之一,程序会自动解析:
    #     wxid(如 wxid_abc123)  /  微信号  /  昵称
    # 用  python voice_bot.py --list  查看所有会话的真实 username。
    #
    # ⚠️ row 是"会话在**界面**列表里的行号"(0 起,置顶会话也算)。
    #    界面顺序 = 置顶 + 最后消息时间,和 --list 的数据库顺序**不一样**,
    #    所以 --list 的序号不能直接当 row 用!
    #    行号会随时间漂移 —— 强烈建议把要自动回复的会话【置顶】,
    #    这样行号才稳定。即使填错也不会发错人:发送前会 OCR 核对标题。
    "请填对方的微信号或昵称": {
        "name": "会话列表里显示的昵称",   # 用于 OCR 核对,防止发错人
        "row": 0,                          # 界面列表行号(0 起)
        "enabled": True,                   # False = 临时停用这个角色
        "persona": (
            "# 任务\n"
            "你需要扮演指定角色,根据角色的经历,模仿她的语气进行线上日常对话。\n\n"
            "# 角色\n"
            "在这里写角色设定……\n\n"
            "# 输出要求\n"
            "回答尽量简短,控制在 30 字以内。使用中文。\n"
            "只输出语言,不要旁白/括号动作。\n"
        ),
    },
    # 想加第二个角色就复制上面这段,改 key / name / row / persona。
}


# ---- 运行参数 -------------------------------------------------------------
POLL_INTERVAL = 3.0      # 轮询间隔(秒)
MAX_REPLY_CHARS = 60     # 超过此长度不转语音(语音条不适合太长)
SEND_TEXT_TOO = False    # 是否同时发一条文字(False = 只发语音条)
# 诊断模式(由 --debug 打开):
#   True  = 每步存截图 + 播放时同步采集线缆,排查问题用
#   False = 紧凑模式(默认)—— 微信会把等待/截图时间也录进语音条,
#           关掉诊断能明显缩短语音条首尾的静音
DEBUG_MODE = False
SNAPSHOT_DIR = os.path.join(ROOT, "05_文档", "发送截图")
LOG_DIR = os.path.join(ROOT, "05_文档", "运行日志")

# ---------------------------------------------------------------------------
# ★ 对话记忆
#
# 为什么要自己存:本项目的回复是**语音条**,微信数据库里存的是 XML
# (不含文字),所以无法从数据库还原"我说过什么"。不在本地记一份,
# AI 每次就只能看到「人设 + 当前这一句」—— 完全没有上下文。
#
# 记忆文件含聊天内容,已加入 .gitignore,不会进仓库。
# ---------------------------------------------------------------------------
MEMORY_FILE = os.path.join(ROOT, "voice_bot_memory.json")
HISTORY_TURNS = 8           # 每次最多带最近几轮(user+assistant 各算一条)
MEMORY_SEED_FROM_DB = 6     # 首次没有记忆时,取数据库里对方最近 N 条文本做种子
USER_MSG_WITH_TIME = True   # 给用户消息加时间前缀(人设里要求"以发送时间为准")
MEMORY_KEEP_TURNS = 40      # 文件里最多保留多少轮(防止无限增长)


class _Tee(object):
    """把输出同时写到控制台和日志文件。

    ★ 为什么要落盘:
        以前失败后**没有任何日志**,只能靠"哪个截图/哪个 wav 文件存在"
        去猜卡在哪一步,非常费劲。现在每轮运行都会写一份日志,
        出问题直接看日志最后几十行就知道是第几步、什么原因。
    """

    def __init__(self, *streams):
        self.streams = [s for s in streams if s is not None]

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass
        return len(data)

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self):
        return False


_LOG_PATH = None


def start_logging():
    """开始记录运行日志,返回日志文件路径(失败返回 None)。"""
    global _LOG_PATH
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        p = os.path.join(LOG_DIR, "voicebot_%s.log" % time.strftime("%Y%m%d"))
        f = open(p, "a", encoding="utf-8", buffering=1)
        f.write("\n" + "=" * 74 + "\n")
        f.write("启动 %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        f.write("=" * 74 + "\n")
        sys.stdout = _Tee(sys.stdout, f)
        sys.stderr = _Tee(sys.stderr, f)
        _LOG_PATH = p
        return p
    except Exception:
        return None

# 音频留存:每 N 次发送后自动清理一次(None/0 = 不自动清理)
AUTO_CLEAN_EVERY = 5
_sent_counter = {"n": 0}

# 语音条时序(可通过 wx_voice_sender 调整)
import wx_voice_sender as vS  # noqa: E402


# ===========================================================================
#  消息读取(数据库解密)
# ===========================================================================

def _patch_tasklist():
    """本机 tasklist 不可用,用 psutil 提供 PID。"""
    try:
        import psutil
        import wechatauto.db as _wdb

        def _pids(self):
            out = []
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    if (p.info["name"] or "").lower() == "weixin.exe":
                        out.append(p.info["pid"])
                except Exception:
                    pass
            return out

        _wdb.WeChatDB._find_weixin_pids = _pids
    except Exception as e:
        print("[警告] psutil 补丁失败:", e)


def open_db():
    """打开微信数据库(只读解密)。"""
    import wxauto_bootstrap
    wxauto_bootstrap.setup()
    _patch_tasklist()
    from wechatauto import WeChatDB
    print("[DB] 正在解密微信数据库 …")
    db = WeChatDB()
    info = db.get_self_info()
    print("[DB] 账号: %s (%s)" % (info.get("nick_name"), info.get("username")))
    return db


def load_state():
    if os.path.isfile(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(st):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[警告] 保存状态失败:", e)


_MEM = None


def get_memory():
    """读对话记忆(进程内缓存)。"""
    global _MEM
    if _MEM is None:
        _MEM = {}
        if os.path.isfile(MEMORY_FILE):
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    _MEM = json.load(f) or {}
                print("[记忆] 已加载 %d 个会话的对话记忆" % len(_MEM))
            except Exception as e:
                print("[记忆] 读取失败(按空处理): %s" % str(e)[:60])
                _MEM = {}
    return _MEM


def save_memory(mem=None):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(mem if mem is not None else get_memory(), f,
                      ensure_ascii=False, indent=2)
    except Exception as e:
        print("[警告] 保存对话记忆失败:", e)


def format_user_msg(text, ts=None):
    """给用户消息加时间前缀 —— 人设里写了"用户的消息带有消息发送时间"。"""
    if USER_MSG_WITH_TIME and ts:
        return "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M",
                                         time.localtime(int(ts))), text)
    return text


def build_history(chat_key):
    """把记忆里的 (对方, 我) 转成 OpenAI 的 messages(只取最近若干轮)。"""
    turns = get_memory().get(chat_key) or []
    hist = []
    for t in turns[-HISTORY_TURNS:]:
        u, a = (t.get("user") or "").strip(), (t.get("assistant") or "").strip()
        if u:
            hist.append({"role": "user", "content": format_user_msg(u, t.get("t"))})
        if a:
            hist.append({"role": "assistant", "content": a})
    return hist


def remember(chat_key, user_text, reply, ts=None):
    """把这一轮记进记忆。若最后一条正是本条用户消息(数据库种子),就补上回复。"""
    mem = get_memory()
    turns = mem.setdefault(chat_key, [])
    ts = int(ts or time.time())
    if (turns and (turns[-1].get("user") or "").strip() == (user_text or "").strip()
            and not (turns[-1].get("assistant") or "").strip()):
        turns[-1]["assistant"] = reply
    else:
        turns.append({"t": ts, "user": user_text, "assistant": reply})
    if len(turns) > MEMORY_KEEP_TURNS:
        del turns[:-MEMORY_KEEP_TURNS]
    save_memory(mem)


def ensure_memory_seeded(db, chat_key, name=None):
    """首次遇到某会话时,用数据库里对方的最近文本消息打底。

    这样即便还没积累自己的记忆,第一条回复也已经知道对方之前说了什么。
    种子条目只有 user、没有 assistant(因为我们无法从数据库还原语音内容)。
    """
    mem = get_memory()
    if chat_key in mem:
        return
    turns = []
    if MEMORY_SEED_FROM_DB > 0:
        try:
            real = resolve_chat_key(db, chat_key, name)
            got = []
            for m in db.get_messages(real, limit=60):
                if m.get("sender_id") == 1:
                    continue
                typ = str(m.get("type") or "")
                if "文本" not in typ and "text" not in typ.lower():
                    continue
                c = str(m.get("content") or "").strip()
                if c:
                    got.append((int(m.get("create_time") or 0), c))
            got.sort()
            for ct, c in got[-MEMORY_SEED_FROM_DB:]:
                turns.append({"t": ct, "user": c, "assistant": ""})
        except Exception as e:
            print("  [记忆] 种子读取失败(忽略): %s" % str(e)[:60])
    mem[chat_key] = turns
    save_memory(mem)
    print("  [记忆] 首次为该会话建立记忆:种子 %d 条对方消息" % len(turns))


def resolve_chat_key(db, key, name=None):
    """把 CHATS 的 key 解析成数据库真正认的 username。

    为什么需要:
        微信数据库里消息是按 **wxid**(如 wxid_xxxxxxxxxxxxxxxx)存的。
        但 wxid 普通用户看不到(要读数据库才有),所以 CHATS 的 key
        允许填**看得见的东西**:昵称、备注、或微信号。
        这里负责把它解析成真正的 wxid。

    解析顺序(逐个试,命中即返回):
        1. 本身就能查到昵称且不是自指  -> 已是有效 username
        2. username_by_nickname(name/昵称/微信号)
        3. search_contact(name/昵称/微信号) 取 username
        4. 自扫会话列表做名字匹配

    解析结果会缓存,避免重复查库。
    """
    cache = getattr(resolve_chat_key, "_cache", None)
    if cache is None:
        cache = {}
        resolve_chat_key._cache = cache
    ck = (key, name)
    if ck in cache:
        return cache[ck]

    def _ok(u):
        """确认 u 是数据库认的 username(能取到非自指昵称,或能取到消息)。"""
        if not u:
            return False
        try:
            n = db.get_nickname(u) or ""
        except Exception:
            n = ""
        if n and n != u:
            return True
        try:
            return len(db.get_messages(u, limit=1)) > 0
        except Exception:
            return False

    # 1) key 本身
    if _ok(key):
        cache[ck] = key
        return key

    cands = []
    for want in (name, key):
        if want and want not in cands:
            cands.append(want)

    # 2) 按昵称查
    for want in cands:
        try:
            u = db.username_by_nickname(want)
            if _ok(u):
                print("  [解析] %r -> %s (按昵称)" % (key, u))
                cache[ck] = u
                return u
        except Exception:
            pass

    # 3) 模糊搜联系人
    for want in cands:
        try:
            for c in (db.search_contact(want) or []):
                u = c.get("username") if isinstance(c, dict) else None
                nm = (c.get("nick_name") or c.get("remark") or "") if isinstance(c, dict) else ""
                # 名字要能对上,避免搜出无关的人
                if u and (want in (nm or "") or (nm or "") in want or _ok(u)):
                    if _ok(u):
                        print("  [解析] %r -> %s (搜联系人)" % (key, u))
                        cache[ck] = u
                        return u
        except Exception:
            pass

    # 4) 自扫会话列表
    try:
        for s in db.get_sessions(limit=50):
            u = s.get("username")
            try:
                nm = db.get_nickname(u) or ""
            except Exception:
                nm = ""
            if want_matches(nm, cands) and _ok(u):
                print("  [解析] %r -> %s (扫会话列表)" % (key, u))
                cache[ck] = u
                return u
    except Exception:
        pass

    print("  [解析] ⚠ 无法把 %r 解析成有效 username,将按原样使用" % key)
    cache[ck] = key
    return key


def want_matches(nick, wants):
    """昵称与任一候选是否匹配(去空格后互相包含)。"""
    n = "".join((nick or "").split())
    if not n:
        return False
    for w in wants:
        w2 = "".join((w or "").split())
        if w2 and (w2 in n or n in w2):
            return True
    return False


def fetch_new(db, chat_key, state, self_name=None, display_name=None):
    """取该会话的新消息(只取对方发来的文本)。

    注意 sender_id 语义:实测 1 = 自己。这里只用它做粗过滤,
    同时用发送者昵称二次确认(防止把自己发的当成对方消息 → 自问自答死循环)。

    chat_key 可以是 wxid / 昵称 / 微信号,内部会解析成数据库认的 username。
    """
    real = resolve_chat_key(db, chat_key, display_name)
    if real != chat_key:
        print("  [会话] key %r 解析为 %r" % (chat_key, real))
    try:
        msgs = db.get_messages(real, limit=20)
    except Exception as e:
        print("  [读取失败] %s: %s" % (chat_key, e))
        return []

    # 水位按 key 记(而不是解析后的 wxid),这样改 key 也不会重发
    last_ts = state.get(chat_key, {}).get("last_create_time", 0)
    fresh = []
    for m in msgs:
        ct = int(m.get("create_time") or 0)
        if ct <= last_ts:
            continue
        # 自己发的跳过(实测 sender_id=1 是自己)
        if m.get("sender_id") == 1:
            continue
        typ = str(m.get("type") or "")
        if "文本" not in typ and "text" not in typ.lower():
            continue
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        fresh.append({"time": ct, "content": content, "raw": m})
    fresh.sort(key=lambda x: x["time"])
    return fresh


# ===========================================================================
#  AI 回复
# ===========================================================================

def ai_reply(persona, user_text, history=None):
    """调用 AI 生成回复。模型失效时自动回退到备用模型。"""
    from openai import OpenAI
    client = OpenAI(api_key=AI_KEY, base_url=AI_BASE)
    msgs = [{"role": "system", "content": persona}]
    if history:
        msgs.extend(history)
    msgs.append({"role": "user", "content": user_text})

    # 已经确定可用的话直接用
    if _working_model["id"]:
        r = client.chat.completions.create(
            model=_working_model["id"], messages=msgs,
            temperature=AI["temperature"], max_tokens=AI["max_tokens"])
        out = (r.choices[0].message.content or "").strip()
        if out:
            return out

    tried = []
    for mid in _candidate_models():
        if not mid or mid in tried:
            continue
        tried.append(mid)
        try:
            r = client.chat.completions.create(
                model=mid, messages=msgs,
                temperature=AI["temperature"], max_tokens=AI["max_tokens"])
            out = (r.choices[0].message.content or "").strip()
            if out:
                if _working_model["id"] != mid:
                    print("  [AI] 使用模型: %s" % mid)
                    _working_model["id"] = mid
                return out
            print("  [AI] %s 返回空内容,换下一个" % mid)
        except Exception as e:
            print("  [AI] %s 失败: %s" % (mid, str(e)[:120]))
    raise RuntimeError(
        "所有模型均不可用(共试了 %d 个)。请检查:\n"
        "    1. API Key 是否有效/有额度\n"
        "    2. 模型名格式是否匹配服务商(OpenRouter 要 'deepseek/xxx',\n"
        "       DeepSeek 官方要 'deepseek-xxx',不能混用)\n"
        "    3. config.py 里的 DEEPSEEK_BASE_URL 是否正确" % len(tried))


def clean_for_voice(text):
    """去掉不适合朗读的内容,并压到合适长度。"""
    import re
    t = re.sub(r"https?://\S+", "", text)
    t = re.sub(r"[*#`~]", "", t)
    t = re.sub(r"[\r\n]+", "，", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    if len(t) > MAX_REPLY_CHARS:
        # 截到最后一个句末标点
        cut = max(t.rfind(p, 0, MAX_REPLY_CHARS) for p in "。！？，、；")
        t = t[:cut + 1] if cut > 10 else t[:MAX_REPLY_CHARS]
    return t


# ===========================================================================
#  发送语音条
# ===========================================================================

def verify_chat_open(name):
    """截图核对当前打开的会话标题是否为 name。

    只用 OCR 不可靠(未装依赖),这里采取:
      1. 截图存档,人工可回查是否发对人
      2. 返回截图路径
    并强烈建议:把要自动回复的对象【置顶】,使行号固定。

    返回截图路径 或 None。
    """
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    hwnd = vS.find_window()
    if not hwnd:
        return None
    p = os.path.join(SNAPSHOT_DIR, "open_%s_%s.png" % (
        name, time.strftime("%H%M%S")))
    try:
        vS._snap(hwnd, p)
        return p
    except Exception:
        return None


def send_voice(chat_key, text, row, expect_name=None, debug=False):
    """向指定会话行发送语音条。

    expect_name 会用于 OCR 标题校验 —— 防止因行号漂移而发错人,
    并避免对已打开的会话重复点击(微信会 toggle 关闭)。

    debug=True 时打开完整诊断(每步截图 + 播放时同步采集线缆),
    会明显拉长语音条首尾静音,只在排查问题时用。
    """
    print("  [发送] 目标「%s」第 %d 行 -> %s" % (expect_name or "?", row, text))
    res = vS.send_voice_by_row(
        text, row, verbose=True, expect_name=expect_name, debug=debug,
        snapshot_prefix=os.path.join(SNAPSHOT_DIR, chat_key[:12]))
    if res.get("ok"):
        g = res.get("session") or {}
        print("  [会话校验] %s / %s" % (g.get("action"), g.get("by")))
    return res


# ===========================================================================
#  主循环
# ===========================================================================

def maybe_cleanup(counter):
    """每 AUTO_CLEAN_EVERY 次发送后自动清理一次音频留存。"""
    if not AUTO_CLEAN_EVERY:
        return
    if counter % AUTO_CLEAN_EVERY != 0:
        return
    try:
        import importlib.util
        p = os.path.join(ROOT, "04_音频留存", "audio_retention.py")
        if not os.path.isfile(p):
            return
        spec = importlib.util.spec_from_file_location("audio_retention", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        r = m.cleanup(dry_run=False, verbose=False)
        if r.get("deleted"):
            print("[留存] 自动清理 %d 个文件,释放 %.1f MB" % (
                r["deleted"], r.get("freed_mb", 0)))
    except Exception as e:
        print("[留存] 自动清理失败(不影响主流程): %s" % str(e)[:80])


def run_once(db, state, live):
    """跑一轮:检查所有会话的新消息并回复。"""
    acted = False
    for chat_key, cfg in CHATS.items():
        # 支持 enabled 开关(Web UI 可停用某个角色)
        # 注意:必须用 `is False` 判断,因为默认值是"没有这个键"=启用
        if cfg.get("enabled") is False:
            continue
        name = cfg.get("name") or chat_key
        fresh = fetch_new(db, chat_key, state, display_name=name)
        if not fresh:
            continue
        for item in fresh:
            acted = True
            user_text = item["content"]
            print()
            print("=" * 70)
            print("[%s] 收到: %s" % (name, user_text[:60]))
            print("=" * 70)

            # ★ 对话记忆:首次先打底,然后取出最近若干轮历史
            ensure_memory_seeded(db, chat_key, name)
            hist = build_history(chat_key)
            if hist:
                print("  [记忆] 带上 %d 条历史(%d 条对方 / %d 条我)"
                      % (len(hist),
                         sum(1 for h in hist if h["role"] == "user"),
                         sum(1 for h in hist if h["role"] == "assistant")))
            else:
                print("  [记忆] (暂无历史,这是第一轮)")

            # AI 回复
            try:
                reply = ai_reply(cfg["persona"],
                                 format_user_msg(user_text, item["time"]),
                                 history=hist)
            except Exception as e:
                print("  [AI 失败] %s" % e)
                continue
            print("  AI 回复: %s" % reply)

            voice_text = clean_for_voice(reply)
            if not voice_text:
                print("  [跳过] 清理后为空")
                # 清空也要推进,否则会永远卡在这一条上
                state.setdefault(chat_key, {})["last_create_time"] = item["time"]
                save_state(state)
                continue

            # ★★ 关键:水位推进放进 finally ★★
            #
            # 旧写法是"发送之后再推进水位"。实测踩过的坑:
            #   发送流程里抛出**未预料的异常**(ctypes 句柄溢出),
            #   异常一路冒到轮询层 -> 那句赋值**永远走不到** ->
            #   水位不前进 -> 下一轮又读到同一条旧消息 ->
            #   **一直用同一句旧对话回答**,表象则是"没点发送键"。
            # 放进 finally 后:无论发送成功、返回失败、还是抛异常,
            # 水位都必然推进 —— 这个死循环从结构上不可能再出现。
            try:
                if live:
                    r = send_voice(chat_key, voice_text, cfg["row"],
                                   expect_name=name, debug=DEBUG_MODE)
                    if r.get("ok"):
                        print("  ✅ 语音条已发送")
                        # ★ 记进对话记忆(对方说了什么 / 我回了什么)
                        remember(chat_key, user_text, reply, item["time"])
                        _sent_counter["n"] += 1
                        maybe_cleanup(_sent_counter["n"])
                    else:
                        print("  ❌ 发送失败 [卡在: %s] %s"
                              % (r.get("step") or "?", r.get("error")))
                        print("     排查:查看 05_文档\\发送截图\\ 里最新的 _fail_*.png")
                        print("           以及 05_文档\\运行日志\\ 里当天的日志")
                else:
                    print("  [DRY-RUN] 将发送语音: %s" % voice_text)
                    remember(chat_key, user_text, reply, item["time"])
            except Exception as e:
                # 未预料的异常:记下来、打印 traceback,但不让整轮崩掉
                import traceback
                print("  ❌ 发送流程抛出未预料的异常: %s: %s" % (type(e).__name__, e))
                for _ln in traceback.format_exc().rstrip().split("\n")[-5:]:
                    print("       %s" % _ln)
                print("     已跳过这条消息(水位照常推进,避免卡在同一条上)")
            finally:
                # 推进水位(无论成功失败都推进,避免重复轰炸)
                state.setdefault(chat_key, {})["last_create_time"] = item["time"]
                save_state(state)
            time.sleep(1.5)

    return acted


def list_chats(db):
    """列出所有会话,方便填写 CHATS 配置。

    ⚠️ 重要:本列表的顺序来自**数据库**(按最后消息时间),
       而微信**界面**的顺序是「置顶 + 时间」—— 两者**并不一致**。
       所以下面这个「行」号**不能直接当成 CHATS 的 row**!
    """
    print()
    print("=" * 78)
    print("会话列表(把 username 填到 CHATS 的 key)")
    print("=" * 78)
    print("  %-3s %-32s %-13s %-6s %s" % ("序", "username", "昵称", "消息", "已配置"))
    print("  " + "-" * 74)
    try:
        sessions = db.get_sessions(limit=30)
    except Exception as e:
        print("  读取失败:", e)
        return
    for i, s in enumerate(sessions):
        u = s.get("username")
        try:
            nick = db.get_nickname(u)
        except Exception:
            nick = "?"
        try:
            n = len(db.get_messages(u, limit=5))
        except Exception:
            n = "?"
        # 标记是否已在 CHATS 里配置
        mark = ""
        if u in CHATS:
            if CHATS[u].get("enabled") is False:
                mark = "已配置(停用)"
            else:
                mark = "✅ 会回复"
        print("  %-3d %-32s %-13s %-6s %s" % (i, u, str(nick)[:13], n, mark))

    # 顺便读出当前打开的是哪个会话,帮助校准
    cur_title = ""
    try:
        import session_guard
        import wx_sender
        h = wx_sender.find_main_window()
        if h:
            _, cur_title = session_guard.read_chat_title(h)
    except Exception:
        pass

    print()
    print("  ⚠️ 注意:上面「序」号来自数据库(按最后消息时间排序),")
    print("     与微信界面的行号(置顶+时间)**不一致**,不能直接当 row 用!")
    print()
    print("  怎么确定界面行号:")
    print("    1) 把目标会话在微信里【置顶】—— 置顶后它就在列表最前")
    print("       两个目标就占第 0、1 行,顺序稳定")
    if cur_title:
        print("    2) 当前微信打开的会话是「%s」" % cur_title)
        print("       在界面上看它在第几行,那就是它的 row")
    print("    3) 拿不准就靠程序自带的【会话标题校验】兜底:")
    print("       发错行时它会 OCR 出标题不符 -> 中止发送(不会发错人)")
    print()
    print("  另:用 Web UI 配置角色更直观 → python character_webui.py")
    print("      (或控制面板 → ⑦ 角色配置 Web UI)")


def main():
    ap = argparse.ArgumentParser(description="微信语音自动回复")
    ap.add_argument("--live", action="store_true",
                    help="实际发送(默认只演练,不发送)")
    ap.add_argument("--once", action="store_true",
                    help="只跑一轮就退出(适合测试)")
    ap.add_argument("--check", action="store_true",
                    help="只做前提检查,不读消息")
    ap.add_argument("--list", dest="list_chats", action="store_true",
                    help="列出所有会话与行号(用于填写 CHATS)")
    ap.add_argument("--reset", action="store_true",
                    help="清空水位记录(下次会重新处理最近消息)")
    ap.add_argument("--debug", action="store_true",
                    help="诊断模式:每步存截图 + 播放时同步采集线缆。"
                         "排查问题用;平时不用(会拉长语音条首尾静音)")
    args = ap.parse_args()

    global DEBUG_MODE
    DEBUG_MODE = bool(args.debug)

    # 开始记录日志(所有 print 会同时写入 05_文档\运行日志\)
    _lp = start_logging()

    print("=" * 74)
    print("  微信语音自动回复")
    print("  模式: %s%s" % ("【实际发送】" if args.live else "【DRY-RUN 只演练】",
                            "  + 诊断模式(--debug)" if DEBUG_MODE else ""))
    print("=" * 74)
    if _lp:
        print("  运行日志: %s" % _lp)
        print("  (失败时把日志最后 30 行发给开发者,即可定位问题)")
    print()

    # 前提检查
    #
    # --list 是"新用户第一步"(用来查 wxid)。它只需要:
    #   · 微信进程在运行(要从内存取数据库密钥)
    #   · 虚拟线卡存在
    # 不需要:微信窗口、TTS 服务、AI Key —— 这些都不挡它。
    print()
    print("--- 前提检查 ---")
    if args.list_chats:
        # 单独做宽松检查
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
            has_wx = bool(ctypes.windll.user32.FindWindowW("Qt51514QWindowIcon", "微信"))
        except Exception:
            has_wx = False
        if has_wx:
            print("微信窗口: 已找到")
        else:
            print("微信窗口: 未找到 —— 列出会话需要**微信处于登录状态**(取数据库密钥)")

        try:
            p, r, info = vS._find_cable_devices(refresh=True)
            print("虚拟线卡: 播放[%s] 录音[%s]" % (p, r))
        except Exception as e:
            print("虚拟线卡: 检测失败(%s)" % str(e)[:50])

        if not AI_KEY:
            print("AI Key   : 未配置(不影响列出会话,但自动回复需要它)")
        else:
            print("AI Key   : 已配置")
        print()
        # 直接进入列会话,不做硬性拦截
        db = open_db()
        list_chats(db)
        return

    try:
        ok, _ = vS.check_prerequisites(verbose=True)
    except Exception as e:
        print("检查失败:", e)
        ok = False
    if not AI_KEY or not AI_BASE or not AI_MODEL:
        print("✗ AI 配置不完整: key=%s base=%s model=%s" % (
            bool(AI_KEY), AI_BASE, AI_MODEL))
        print("   请编辑 WeChatBot 的 config.py 填入 DEEPSEEK_API_KEY,")
        print("   或用控制面板 → ④ 配置面板 填写。")
        ok = False
    else:
        print("AI 接口: %s" % AI_BASE)
        print("  配置模型: %s" % AI_MODEL)
        # 启动自检:确认至少有一个可用模型
        try:
            probe = ai_reply("你是测试助手。", "只回复两个字:正常")
            print("  ✓ AI 自检通过(使用 %s): %s" % (_working_model["id"], probe[:30]))
        except Exception as e:
            print("  ✗ AI 自检失败: %s" % e)
            print("    请检查 config.py 里的 DEEPSEEK_API_KEY / BASE_URL / MODEL")
            ok = False

    # 如果 TTS 在线,主动把权重设成配置的那对
    # (api_v2.py 会把 tts_infer.yaml 的 version 改写成 v1,不能只靠配置文件)
    if ok:
        try:
            import gptsovits_tts as _tts
            if _tts.is_server_alive():
                print()
                print("TTS 权重自检:")
                w_ok, _msg = _tts.ensure_weights(verbose=True)
                if not w_ok:
                    print("  ⚠ 权重设置未完全成功 —— 音色可能不对")
        except Exception as e:
            print("  (TTS 权重自检跳过: %s)" % str(e)[:60])

    print()
    if not ok:
        print("前提不满足,退出。请先修正上面的问题。")
        sys.exit(1)
    if args.check:
        print("检查通过。")
        return

    # 打开数据库
    db = open_db()
    state = load_state()

    if args.reset:
        state = {}
        save_state(state)
        print("[状态] 已清空水位")

    print("[状态] 已记录水位: %s" % (state or "(空,首次运行会回复现有消息)"))

    if state == {} and args.live:
        print()
        print("⚠️ 首次运行且为 live 模式:会回复这些会话的最近消息。")
        print("   建议先用不加 --live 跑一次确认内容。")
        print("   5 秒后继续,Ctrl+C 可中断 …")
        time.sleep(5)

    print()
    print("开始轮询(间隔 %.1fs)。Ctrl+C 退出。" % POLL_INTERVAL)
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)

    try:
        while True:
            try:
                run_once(db, state, args.live)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print("[轮询异常] %s: %s" % (type(e).__name__, e))
                # ★ 打出完整 traceback —— 否则只知道异常类型,不知道哪一行,
                #   排查起来只能靠猜(实测吃过这个亏)。
                import traceback
                for _ln in traceback.format_exc().rstrip().split("\n")[-6:]:
                    print("    %s" % _ln)
                # 这一轮中断了,水位不会推进;提示一下免得以为它在正常工作
                print("    (本轮中止:水位未推进,该会话下轮会重来)")
            if args.once:
                break
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print()
        print("已停止。")
    finally:
        save_state(state)
        print("结束 %s" % time.strftime("%Y-%m-%d %H:%M:%S"))


if __name__ == "__main__":
    main()
