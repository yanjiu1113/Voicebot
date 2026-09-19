# -*- coding: utf-8 -*-
"""
【坐标采集工具】—— 把鼠标移到目标按钮上,自动写入坐标文件

什么时候需要跑它:
  · 换了分辨率 / 改了 DPI 缩放
  · 微信窗口尺寸和之前不同(不是 2584x1540)
  · 日志里出现"进入录音失败"或坐标相关的报错

用法:
  1. 先让微信窗口处于【最大化】(和平时发语音时一样)
  2. 运行:  启动.cmd → [C] 采集按钮坐标
     或:     python 02_工具面板\采集按钮坐标.py
  3. 看到倒计时后,把鼠标移到目标按钮上【停住不动】

它会写入 VoiceBot\01_核心模块\voice_button_coords.json,
同时记录当时的窗口尺寸 —— 以后窗口尺寸变了会自动等比换算。

本文件放在 02_工具面板/ 或桌面都能正常工作(路径自动判断)。
"""
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

# ---------------------------------------------------------------------------
# 路径:兼容两种摆放位置
#   A) VoiceBot/02_工具面板/采集按钮坐标.py
#   B) 桌面/采集按钮坐标.py   (同级有 VoiceBot 目录)
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
CORE = None
for _c in (os.path.join(HERE, "..", "01_核心模块"),      # 情况 A
           os.path.join(HERE, "VoiceBot", "01_核心模块"),  # 情况 B
           HERE):
    if os.path.isdir(_c):
        CORE = os.path.abspath(_c)
        break
ROOT = os.path.dirname(CORE)                 # VoiceBot/
DESKTOP = os.path.dirname(ROOT)
PROJECT = os.path.join(DESKTOP, "WeChatBot_WXAUTO_SE-3.28")
OUT = os.path.join(CORE, "voice_button_coords.json")

u = ctypes.windll.user32
u.SetProcessDPIAware()

STEPS = [
    ("voice_btn", "语音条按钮",
     "把鼠标移到【点它会开始录音的那个图标】上,停住不动"),
    ("send_btn", "录音中的发送键 ⬆",
     "先点语音条开始录音;把鼠标移到右侧的【⬆ 发送键】上,停住不动"),
    ("cancel_btn", "录音中的取消键 ✕",
     "把鼠标移到录音时的【✕ 取消键】上,停住不动"),
]

print("=" * 74)
print("  微信语音按钮 坐标采集")
print("=" * 74)
print()
print("  请确认:微信窗口已【最大化】,输入框为空,没在用语音输入法")
print("  目标目录: %s" % CORE)
print()
input("  准备好按【回车】开始 …")

buttons = {}
for i, (key, name, hint) in enumerate(STEPS, 1):
    print()
    print("-" * 74)
    print("  第 %d/%d 个:%s" % (i, len(STEPS), name))
    print("  %s" % hint)
    print("-" * 74)
    for s in range(6, 0, -1):
        print("      %d 秒后读取 …" % s, end="\r")
        time.sleep(1)
    pt = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(pt))
    print("      OK %s = (%d, %d)                    " % (name, pt.x, pt.y))
    buttons[key] = [pt.x, pt.y]
    if i < len(STEPS):
        input("      按【回车】继续下一个 …")

# 顺带记录当时的窗口尺寸(以后窗口尺寸变了会自动等比换算)
win = None
try:
    sys.path.insert(0, CORE)
    sys.path.insert(0, os.path.join(PROJECT, "vendor_py"))
    import wxauto_bootstrap
    wxauto_bootstrap.setup()
    import wx_sender as ws
    h = ws.find_main_window()
    if h:
        r = wintypes.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        win = {"x": r.left, "y": r.top,
               "w": r.right - r.left, "h": r.bottom - r.top}
        print()
        print("  窗口 rect: %s" % win)
        if not u.IsZoomed(h):
            print("  ⚠️ 警告:微信窗口**没有最大化**!采集出来的坐标只在当前尺寸有效。")
    else:
        print("  (没找到微信窗口,跳过窗口尺寸记录)")
except Exception as e:
    print("  (窗口尺寸读取失败: %s)" % str(e)[:60])

data = {"buttons": buttons}
if win:
    data["window"] = win

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print()
print("=" * 74)
print("  已写入: %s" % OUT)
print("=" * 74)
print(json.dumps(data, ensure_ascii=False, indent=2))
print()
print("  程序下次运行会自动使用这些坐标。")
print("  (这个文件被 .gitignore 忽略,不会进仓库)")
input("  按【回车】关闭 …")
