# -*- coding: utf-8 -*-
"""
实测语音模式(录音中)的按钮位置。
流程: 点语音条按钮 -> 录 3 秒 -> 截图 -> 取消(不发)
只用于标定,不会发出语音条。
"""
import os
import sys
import time
import ctypes
from ctypes import wintypes

WD = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "WeChatBot_WXAUTO_SE-3.28")
sys.path.insert(0, WD)
sys.path.insert(0, os.path.join(WD, "vendor_py"))
import wxauto_bootstrap
wxauto_bootstrap.setup()
import wx_sender
import sounddevice as sd
import numpy as np

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

VOICE_BTN = (2349, 1455)     # 已验证:语音条按钮
CANCEL_GUESS = (2133, 1452)  # 待验证:取消
OUT_IDX, RATE = 26, 48000


def grab(hwnd):
    mx, my, mw, mh = wx_sender.get_rect(hwnd)
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bm = gdi32.CreateCompatibleBitmap(hdc, mw, mh)
    gdi32.SelectObject(mem, bm)
    user32.PrintWindow(hwnd, mem, 0x00000002)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class BI(ctypes.Structure):
        _fields_ = [("bmiHeader", BIH), ("bmiColors", wintypes.DWORD * 3)]

    bi = BI()
    bi.bmiHeader.biSize = ctypes.sizeof(BIH)
    bi.bmiHeader.biWidth = mw
    bi.bmiHeader.biHeight = -mh
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(mw * mh * 4)
    gdi32.GetDIBits(mem, bm, 0, mh, buf, ctypes.byref(bi), 0)
    from PIL import Image
    img = Image.frombuffer("RGBA", (mw, mh), buf, "raw", "BGRA", 0, 1).convert("RGB")
    gdi32.DeleteObject(bm); gdi32.DeleteDC(mem); user32.ReleaseDC(hwnd, hdc)
    return img, (mx, my, mw, mh)


hwnd = wx_sender.find_main_window()
print("微信 hwnd =", hwnd, wx_sender.get_rect(hwnd))
print("[1] 置前:", wx_sender.bring_to_front(hwnd))
time.sleep(0.6)

print("[2] 点语音条按钮 (开始录音) %s" % (VOICE_BTN,))
wx_sender.click(*VOICE_BTN)
time.sleep(0.9)

print("[3] 播 2 秒音频(进入录音态)")
try:
    t = np.linspace(0, 2, int(RATE * 2), endpoint=False)
    tone = (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16)
    st = np.repeat(tone.reshape(-1, 1), 2, axis=1)
    with sd.OutputStream(device=OUT_IDX, samplerate=RATE, channels=2, dtype="int16") as o:
        o.write(st)
except Exception as e:
    print("    播放失败:", e)
time.sleep(0.4)

print("[4] 录音中截图 …")
img, (mx, my, mw, mh) = grab(hwnd)
p = os.path.join(WD, "rec_state_full.png")
img.save(p)
# 裁底部工具栏,放大
toolbar = img.crop((0, int(mh * 0.90), mw, mh))
toolbar = toolbar.resize((toolbar.width, toolbar.height * 2), 1)
p2 = os.path.join(WD, "rec_state_toolbar.png")
toolbar.save(p2)
print("    全图:", os.path.basename(p), img.size)
print("    工具栏:", os.path.basename(p2), toolbar.size)

print()
print("[5] 立即取消录音(点 ✕) 以避免发出")
wx_sender.click(*CANCEL_GUESS)
time.sleep(1.0)
img2, _ = grab(hwnd)
img2.save(os.path.join(WD, "rec_after_cancel.png"))
print("    取消后截图已存")
print()
print("DONE")
print("窗口信息: rect=(%d,%d) %dx%d" % (mx, my, mw, mh))
