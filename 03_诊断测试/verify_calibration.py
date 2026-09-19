# -*- coding: utf-8 -*-
"""
在最大化窗口下验证标定:点击麦克风进入语音模式,截图确认。
不录音(不点录音键)、不发送。

新标定比例(基于最大化截图实测):
  麦克风按钮  (0.296, 0.813)
  发送按钮    (0.957, 0.813)
  输入框      (0.15,  0.70)
"""
import os
import sys
import ctypes
import time
import hashlib
from ctypes import wintypes

WD = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "WeChatBot_WXAUTO_SE-3.28")
sys.path.insert(0, WD)
import wx_sender

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

MIC = (0.296, 0.813)


def capture(hwnd, path):
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
    img.save(path)
    gdi32.DeleteObject(bm); gdi32.DeleteDC(mem); user32.ReleaseDC(hwnd, hdc)
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


hwnd = wx_sender.find_main_window()
L = wx_sender.Layout(hwnd)
print("微信 hwnd =", hwnd, " rect =", wx_sender.get_rect(hwnd))
print("渲染 rect =", wx_sender.get_rect(L.render))

h1 = capture(hwnd, os.path.join(WD, "cal_before.png"))
print("[before] hash =", h1)

print("[1] 置前:", wx_sender.bring_to_front(hwnd))
time.sleep(0.5)

x, y = L.rel(*MIC)
print("[2] 点击麦克风 (%.0f, %.0f)   比例=%s" % (x, y, MIC))
wx_sender.click(x, y)
time.sleep(1.2)

h2 = capture(hwnd, os.path.join(WD, "cal_voice_mode.png"))
print("[after ] hash =", h2)
print()
if h1 != h2:
    print(">>> 界面已变化 -> 麦克风按钮坐标正确,已进入语音模式")
else:
    print(">>> 界面未变化 -> 点击未生效或坐标不对")
print()
print("DONE - 未录音(没点录音键)、未发送")
