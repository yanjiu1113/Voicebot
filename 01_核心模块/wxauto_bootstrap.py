# -*- coding: utf-8 -*-
"""
wxauto_bootstrap.py — 在任何 pywinauto / comtypes / pyweixin 导入之前调用。

为什么需要它
------------
本机系统 Python 的 site-packages **不可写**(沙箱限制),因此:

1. pywechat127 / pywinauto / pycaw / sounddevice / emoji 被安装到
   项目内的 `vendor_py` 目录,需要手动加进 sys.path。
2. comtypes 会把 UIAutomationCore 的类型库缓存生成到
   site-packages/comtypes/gen/ ,但那里既不可写、又存在一个**过期的**
   生成文件,导入时直接抛:
       ImportError: Typelib different than module
   所以必须把生成目录重定向到本项目内可写的位置。

用法(必须最先执行):
    import wxauto_bootstrap
    wxauto_bootstrap.setup()
    import pyweixin    # 之后才能正常导入
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR_DIR = os.path.join(_HERE, "vendor_py")
GEN_DIR = os.path.join(VENDOR_DIR, "comtypes", "gen")

_ready = False


def setup(verbose=False):
    """准备好导入环境。重复调用是安全的。"""
    global _ready
    if _ready:
        return VENDOR_DIR

    # 1) 让 vendor 目录优先被搜索
    if VENDOR_DIR not in sys.path:
        sys.path.insert(0, VENDOR_DIR)

    # 2) comtypes 生成文件目录必须可写,且必须在 comtypes.gen.__path__ 最前面。
    #    直接指向 vendor 内的 comtypes/gen —— 它本身就是 comtypes.gen 的
    #    第一个 __path__ 条目,因此既"可写"又"可导入"。
    os.makedirs(GEN_DIR, exist_ok=True)

    if os.environ.get("COMTYPES_CACHE") != GEN_DIR:
        os.environ["COMTYPES_CACHE"] = GEN_DIR

    # 3) 让 comtypes.gen 解析到我们这份(影子包),并校正搜索顺序
    try:
        import comtypes.gen as _gen
        paths = [p for p in list(getattr(_gen, "__path__", []))
                 if os.path.normcase(os.path.abspath(p)) != os.path.normcase(GEN_DIR)]
        _gen.__path__ = [GEN_DIR] + paths
        if verbose:
            print("[bootstrap] comtypes.gen.__path__ =", _gen.__path__)
    except Exception as e:
        if verbose:
            print("[bootstrap] comtypes.gen adjust failed:", e)

    # 4) 重定向生成目录(必须在 pywinauto 导入前)
    try:
        import comtypes.client as _client
        _client.gen_dir = GEN_DIR
        if verbose:
            print("[bootstrap] comtypes.client.gen_dir =", _client.gen_dir)
    except Exception as e:
        if verbose:
            print("[bootstrap] comtypes.client failed:", e)

    _ready = True
    return VENDOR_DIR


def check(verbose=True):
    """自检:能否导入 pyweixin 并找到发送接口。返回 (ok, 信息字典)。"""
    setup(verbose=verbose)
    info = {}
    try:
        from pyweixin import Messages, Files, Tools, Monitor, AutoReply, Call
        info["pyweixin"] = "OK"
        info["send_audios_to_friend"] = hasattr(Messages, "send_audios_to_friend")
        info["send_messages_to_friend"] = hasattr(Messages, "send_messages_to_friend")
        info["send_files_to_friend"] = hasattr(Files, "send_files_to_friend")
        ok = True
    except Exception as e:
        info["pyweixin"] = "FAIL: %s: %s" % (type(e).__name__, e)
        ok = False

    if verbose:
        print("[bootstrap] vendor:", VENDOR_DIR)
        for k, v in info.items():
            print("[bootstrap]   %-24s %s" % (k, v))
    return ok, info


if __name__ == "__main__":
    good, _ = check(verbose=True)
    sys.exit(0 if good else 1)
