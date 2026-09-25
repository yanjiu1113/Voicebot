# -*- coding: utf-8 -*-
"""
部署路径.py — 定位两个外部程序:WeChatBot 与 GPT-SoVITS

为什么单独做成一个模块
---------------------------------------------------------------------------
VoiceBot 本身只是"插件",真正干活的两个程序在它外面:

    WeChatBot_WXAUTO_SE-3.28\   微信读写(消息解密、收发、发语音条)
    GPT-SoVITS-...\             语音合成(文字 → 音色克隆后的 wav)

这两个目录的位置允许用户自己放(盘符、目录名都可能不同),所以:

    控制面板要能"手动指定路径"        → 需要读写配置
    voice_bot / gptsovits_tts 要用同一份结果 → 需要共享

两边都从这里读,只留**一个真值来源**。否则会出现
"面板里明明选好了,机器人却还在用旧路径"这种查不出来的问题。

配置存放
---------------------------------------------------------------------------
    VoiceBot/部署路径.json     本机绝对路径,已进 .gitignore,不入库

优先级(高 → 低)
---------------------------------------------------------------------------
    1. 环境变量 WECHATBOT_DIR / GPT_SOVITS_DIR   (控制面板启动子进程时注入)
    2. 部署路径.json                            (控制面板里选的)
    3. 自动探测                                 (桌面 / VoiceBot 同级、上级)
    4. 按默认目录名拼一个猜测值                  (可能不存在,用于报"路径不正确")

对外主要接口
---------------------------------------------------------------------------
    check_wechatbot(path) -> (ok, msg)      校验目录是否是可用的 WeChatBot
    check_tts(path)       -> (ok, msg)      校验目录是否是可用的 GPT-SoVITS
    diagnose_wechatbot()  -> dict           判定 ok / not_deployed / wrong_path
    diagnose_tts()        -> dict           同上
    wechatbot_dir() / tts_dir()   -> str    解析出当前应使用的路径
    detect_wechatbot() / detect_tts() -> str
                                            忽略配置重新扫描,找不到返回 None
    same_path(a, b)       -> bool           两个路径是否指向同一位置
    save(wechatbot_dir=..., tts_dir=...)    写配置

命令行
---------------------------------------------------------------------------
    python 部署路径.py --print tts        打印 GPT-SoVITS 目录(启动脚本用)
    python 部署路径.py --print wechatbot  打印 WeChatBot 目录
    python 部署路径.py --check            打印两人的体检结果
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                 # VoiceBot/
DESKTOP = os.path.dirname(ROOT)
CONF_FILE = os.path.join(ROOT, "部署路径.json")

DEFAULT_WECHATBOT_NAME = "WeChatBot_WXAUTO_SE-3.28"
DEFAULT_TTS_NAME = "GPT-SoVITS-v2pro-20250604-nvidia50"

# 目录名关键词(小写比较) —— 用于自动探测
_WECHATBOT_KEYWORDS = ("wechatbot", "wxauto")
_TTS_KEYWORDS = ("gpt-sovits", "gpt_sovits", "gptsovits")

ENV_WECHATBOT = "WECHATBOT_DIR"
ENV_TTS = "GPT_SOVITS_DIR"


# ---------------------------------------------------------------------------
#  配置文件读写
# ---------------------------------------------------------------------------
def load():
    """读配置。任何异常都当成"没配置",绝不因为一个 json 坏了就让程序起不来。"""
    data = {}
    try:
        with open(CONF_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    out = {}
    for k in ("wechatbot_dir", "tts_dir"):
        v = data.get(k)
        out[k] = v.strip() if isinstance(v, str) else ""
    return out


def save(wechatbot_dir=None, tts_dir=None):
    """写配置(只覆盖传入的字段,其余保留)。返回写回后的完整配置。"""
    d = load()
    if wechatbot_dir is not None:
        d["wechatbot_dir"] = str(wechatbot_dir).strip()
    if tts_dir is not None:
        d["tts_dir"] = str(tts_dir).strip()
    tmp = CONF_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONF_FILE)
    except Exception as e:
        print("[部署路径] 写入配置失败: %s" % e)
    return d


# ---------------------------------------------------------------------------
#  校验:这个目录到底能不能用
#  check_* 返回 (ok, msg);ok 为 True 时 msg 可能是"提醒:xxx"这类非阻断提示。
# ---------------------------------------------------------------------------
def check_wechatbot(path):
    """能用的 WeChatBot 目录长什么样?

    voice_bot.py 实际要用的是:
        <目录>\\vendor_py   第三方库(pyweixin 等)所在,缺了直接 import 失败
        <目录>\\config.py   AI 接口配置(缺了只是要手填,不致命)
    """
    if not path:
        return False, "未设置路径"
    if not os.path.isdir(path):
        return False, "目录不存在"
    has_vendor = os.path.isdir(os.path.join(path, "vendor_py"))
    has_bot = os.path.isfile(os.path.join(path, "bot.py"))
    if not (has_vendor or has_bot):
        return False, "目录里没有 vendor_py 或 bot.py(看起来不是 WeChatBot 项目)"
    if not os.path.isfile(os.path.join(path, "config.py")):
        return True, "提醒:目录里没有 config.py,AI 接口配置需要手填"
    return True, ""


def check_tts(path):
    """能用的 GPT-SoVITS 目录长什么样?

        <目录>\\api_v2.py                  要启动的 HTTP 服务
        <目录>\\runtime\\python.exe        整合包自带的 Python 环境
        <目录>\\GPT_SoVITS\\               模型代码 + 参考音频相对路径的根
    """
    if not path:
        return False, "未设置路径"
    if not os.path.isdir(path):
        return False, "目录不存在"
    miss = []
    if not os.path.isfile(os.path.join(path, "api_v2.py")):
        miss.append("api_v2.py")
    if not os.path.isfile(os.path.join(path, "runtime", "python.exe")):
        miss.append("runtime\\python.exe")
    if not os.path.isdir(os.path.join(path, "GPT_SoVITS")):
        miss.append("GPT_SoVITS\\")
    if miss:
        return False, "缺少 " + "、".join(miss)
    return True, ""


# ---------------------------------------------------------------------------
#  自动探测
# ---------------------------------------------------------------------------
def _scan_bases():
    """探测范围:桌面、桌面的上级(用户目录)、VoiceBot 根、VoiceBot 上级。"""
    cands = [DESKTOP, os.path.dirname(DESKTOP), ROOT, os.path.dirname(ROOT),
             os.path.expanduser("~")]
    seen, out = set(), []
    for d in cands:
        if not d or not os.path.isdir(d):
            continue
        k = _norm(d)
        if k in seen:
            continue
        seen.add(k)
        out.append(d)
    return out


def _norm(p):
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(p)))
    except Exception:
        return (p or "").lower()


def same_path(a, b):
    """两个路径是否指向同一位置(忽略大小写、分隔符、相对/绝对写法的差异)。"""
    if not a or not b:
        return False
    return _norm(a) == _norm(b)


def _iter_name_matches(keywords):
    """按目录名关键词找候选目录,产出 (路径, 是否是目录)。"""
    for base in _scan_bases():
        try:
            names = os.listdir(base)
        except Exception:
            continue
        for name in names:
            low = name.lower()
            if not any(k in low for k in keywords):
                continue
            p = os.path.join(base, name)
            if os.path.isdir(p):
                yield p


def find_any(keywords):
    """名字像就返回(不校验),用于判断"用户其实部署过,只是路径写错了"。"""
    for p in _iter_name_matches(keywords):
        return p
    return None


def find_valid(keywords, checker):
    """返回第一个**校验通过**的目录,没有就 None。"""
    for p in _iter_name_matches(keywords):
        ok, _ = checker(p)
        if ok:
            return p
    return None


# ---------------------------------------------------------------------------
#  解析当前应使用的路径(给 voice_bot / gptsovits_tts 用)
# ---------------------------------------------------------------------------
def detect_wechatbot():
    """自动探测一个**可用**的 WeChatBot 目录,找不到返回 None。

    不看配置、不看环境变量 —— 专供"重新自动检测"按钮使用。
    """
    return find_valid(_WECHATBOT_KEYWORDS, check_wechatbot)


def detect_tts():
    """自动探测一个**可用**的 GPT-SoVITS 目录,找不到返回 None。"""
    return find_valid(_TTS_KEYWORDS, check_tts)


def wechatbot_dir():
    e = os.environ.get(ENV_WECHATBOT)
    if e and e.strip():
        return e.strip()
    c = load().get("wechatbot_dir")
    if c:
        return c
    v = detect_wechatbot()
    if v:
        return v
    return os.path.join(DESKTOP, DEFAULT_WECHATBOT_NAME)


def tts_dir():
    e = os.environ.get(ENV_TTS)
    if e and e.strip():
        return e.strip()
    c = load().get("tts_dir")
    if c:
        return c
    v = detect_tts()
    if v:
        return v
    return os.path.join(DESKTOP, DEFAULT_TTS_NAME)


# ---------------------------------------------------------------------------
#  体检:区分「根本没部署」和「部署了但路径不对」
#  这正是控制面板要弹的两个不同提示。
# ---------------------------------------------------------------------------
def _diagnose(path, checker, keywords, kind, default_name):
    ok, msg = checker(path)
    if ok:
        return {"state": "ok", "path": path, "issue": msg, "found": None}

    found = find_valid(keywords, checker)
    if found and _norm(found) != _norm(path):
        return {"state": "wrong_path", "path": path, "issue": msg,
                "found": found, "found_issue": ""}

    # 名字像但校验不过的目录 —— 也算"部署过,路径/内容不对"
    loose = find_any(keywords)
    if loose:
        _, lmsg = checker(loose)
        return {"state": "wrong_path", "path": path, "issue": msg,
                "found": loose, "found_issue": lmsg}

    return {"state": "not_deployed", "path": path, "issue": msg,
            "found": None, "found_issue": "", "expected_name": default_name,
            "kind": kind}


def diagnose_wechatbot():
    return _diagnose(wechatbot_dir(), check_wechatbot,
                     _WECHATBOT_KEYWORDS, "WeChatBot", DEFAULT_WECHATBOT_NAME)


def diagnose_tts():
    return _diagnose(tts_dir(), check_tts,
                     _TTS_KEYWORDS, "GPT-SoVITS", DEFAULT_TTS_NAME)


# ---------------------------------------------------------------------------
#  命令行(给 .cmd 启动脚本读同一个真值来源)
# ---------------------------------------------------------------------------
def _main(argv):
    if "--print" in argv:
        i = argv.index("--print")
        what = argv[i + 1] if i + 1 < len(argv) else ""
        if what.lower() in ("tts", "gpt", "gptsovits", "gpt-sovits"):
            print(tts_dir())
            return 0
        if what.lower() in ("wechatbot", "wx", "project"):
            print(wechatbot_dir())
            return 0
        print("用法: --print tts | --print wechatbot", file=sys.stderr)
        return 2

    for title, d in (("WeChatBot", diagnose_wechatbot()),
                     ("GPT-SoVITS", diagnose_tts())):
        state = {"ok": "✅ 正常", "wrong_path": "❌ 路径不正确",
                 "not_deployed": "❌ 未部署"}[d["state"]]
        print("%s: %s" % (title, state))
        print("   路径: %s" % d["path"])
        if d["issue"]:
            print("   问题: %s" % d["issue"])
        if d["found"]:
            print("   但检测到: %s" % d["found"])
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
