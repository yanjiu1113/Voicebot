# -*- coding: utf-8 -*-
"""
点击【语音条按钮】(旋转wifi图标,发送键左边)。

从 zoom_right.png 换算(放大4倍):
  图标中心 显示(1057,220) -> 全图(1383,288) -> 窗口内(2015+346, 1395+72)
  = 窗口内 (2361, 1467)
  屏幕 = 窗口内 + (-12,-12) = (2349, 1455)

点击后截图验证。若进入录音,立即给出取消/发送坐标。
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

VOICE_BTN = (2349, 1455)


def capture(hwnd, path, zoom_right=False):
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
    if zoom_right:
        tool_y = int(mh * 0.945)
        x0 = int(mw * 0.78)
        c = img.crop((x0, tool_y - 60, mw, tool_y + 60))
        c = c.resize((c.width * 4, c.height * 4), 1)
        c.save(path.replace(".png", "_zoom.png"))
    gdi32.DeleteObject(bm); gdi32.DeleteDC(mem); user32.ReleaseDC(hwnd, hdc)
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


hwnd = wx_sender.find_main_window()
print("窗口 rect =", wx_sender.get_rect(hwnd))
print("语音条按钮坐标 =", VOICE_BTN)
print()

h1 = capture(hwnd, os.path.join(WD, "v2_before.png"), zoom_right=True)
print("[before] hash =", h1)

print("[1] 置前:", wx_sender.bring_to_front(hwnd))
time.sleep(0.5)

print("[2] 点击语音条按钮")
wx_sender.click(*VOICE_BTN)
time.sleep(1.5)

h2 = capture(hwnd, os.path.join(WD, "v2_after.png"), zoom_right=True)
print("[after ] hash =", h2)
print()
print(">>> 界面变化:" , "是" if h1 != h2 else "否")
print(">>> 请看 v2_after_zoom.png 判断是否进入录音状态")
print()
print("DONE")
