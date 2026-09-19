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

# 核心模块优先(用本目录的副本),再补项目与 vendor
for p in (CORE, PROJECT, os.path.join(PROJECT, "vendor_py")):
    if p not in sys.path:
        sys.path.insert(0, p)

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

# 备用模型:config.py 里的模型若已失效(OpenRouter 常下架模型),
# 自动依次回退。实测这几个在 2026-09-19 可用。
AI_MODEL_FALLBACKS = [
    "deepseek/deepseek-v3.2",
    "deepseek/deepseek-v4-pro",
    "deepseek/deepseek-chat-v3.1",
]
_working_model = {"id": None}

# ---- 要自动回复的会话 -----------------------------------------------------
#   key   = 微信会话标识(filehelper 或 wxid_xxx,用 --list 查看)
#   value = {"name": 显示名, "row": 会话列表第几行(0起), "persona": 角色设定}
#
#   ⚠️⚠️ row 是"会话列表从上往下的行号",**会随聊天活跃度变化**!
#       发错人的风险由此而来。降低风险:
#         1) 把要自动回复的对象【置顶】(置顶会话在最前,行号稳定)
#         2) 改动后跑 `python voice_bot.py --list` 核对
#         3) 发送前脚本会截图存档(voice_bot_shots/),可回查
CHATS = {
    'filehelper': {
        "name": '文件传输助手',
        "row": 1,
        "persona": '你是一个名叫小玲的猫娘助手,性格活泼可爱。回答要简短口语化,不超过50字。',
    },
    'wxid_你的测试号': {
        "name": '测试号B',
        "row": 0,
        "persona": '你是一个名叫小玲的猫娘助手,性格活泼可爱。回答要简短口语化,不超过50字。',
    },
}

# ---- 运行参数 -------------------------------------------------------------
POLL_INTERVAL = 3.0      # 轮询间隔(秒)
MAX_REPLY_CHARS = 60     # 超过此长度不转语音(语音条不适合太长)
SEND_TEXT_TOO = False    # 是否同时发一条文字(False = 只发语音条)
SNAPSHOT_DIR = os.path.join(ROOT, "05_文档", "发送截图")

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


def fetch_new(db, chat_key, state, self_name=None):
    """取该会话的新消息(只取对方发来的文本)。

    注意 sender_id 语义:实测 1 = 自己。这里只用它做粗过滤,
    同时用发送者昵称二次确认(防止把自己发的当成对方消息 → 自问自答死循环)。
    """
    try:
        msgs = db.get_messages(chat_key, limit=20)
    except Exception as e:
        print("  [读取失败] %s: %s" % (chat_key, e))
        return []

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
    for mid in [AI_MODEL] + AI_MODEL_FALLBACKS:
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
    raise RuntimeError("所有模型均不可用,请检查 AI 配置或额度")


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


def send_voice(chat_key, text, row, expect_name=None):
    """向指定会话行发送语音条。

    expect_name 会用于 OCR 标题校验 —— 防止因行号漂移而发错人,
    并避免对已打开的会话重复点击(微信会 toggle 关闭)。
    """
    print("  [发送] 目标「%s」第 %d 行 -> %s" % (expect_name or "?", row, text))
    res = vS.send_voice_by_row(
        text, row, verbose=True, expect_name=expect_name,
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
        fresh = fetch_new(db, chat_key, state)
        if not fresh:
            continue
        for item in fresh:
            acted = True
            user_text = item["content"]
            print()
            print("=" * 70)
            print("[%s] 收到: %s" % (name, user_text[:60]))
            print("=" * 70)

            # AI 回复
            try:
                reply = ai_reply(cfg["persona"], user_text)
            except Exception as e:
                print("  [AI 失败] %s" % e)
                continue
            print("  AI 回复: %s" % reply)

            voice_text = clean_for_voice(reply)
            if not voice_text:
                print("  [跳过] 清理后为空")
                continue

            if live:
                r = send_voice(chat_key, voice_text, cfg["row"], expect_name=name)
                if r.get("ok"):
                    print("  ✅ 语音条已发送")
                    _sent_counter["n"] += 1
                    maybe_cleanup(_sent_counter["n"])
                else:
                    print("  ❌ 发送失败: %s" % r.get("error"))
            else:
                print("  [DRY-RUN] 将发送语音: %s" % voice_text)

            # 推进水位(无论成功失败都推进,避免重复轰炸)
            state.setdefault(chat_key, {})["last_create_time"] = item["time"]
            save_state(state)
            time.sleep(1.5)

    return acted


def list_chats(db):
    """列出所有会话,方便填写 CHATS 配置。"""
    print()
    print("=" * 78)
    print("会话列表(把 username 填到 CHATS 的 key)")
    print("=" * 78)
    print("  %-3s %-32s %-13s %-6s %s" % ("行", "username", "昵称", "消息", "已配置"))
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
    print()
    print("  提示:")
    print("    · row 就是上面的「行」号;建议把常聊对象【置顶】以固定行号")
    print("    · 用 Web UI 配置角色更直观:python character_webui.py")
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
    args = ap.parse_args()

    print("=" * 74)
    print("  微信语音自动回复")
    print("  模式: %s" % ("【实际发送】" if args.live else "【DRY-RUN 只演练】"))
    print("=" * 74)

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
            if args.once:
                break
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print()
        print("已停止。")
    finally:
        save_state(state)


if __name__ == "__main__":
    main()
