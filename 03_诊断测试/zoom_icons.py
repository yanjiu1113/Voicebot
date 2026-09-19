# -*- coding: utf-8 -*-
"""
放大右侧按钮区,精确辨认图标。
只截图,不点击。
"""
import os
import sys
import ctypes
from ctypes import wintypes

WD = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "WeChatBot_WXAUTO_SE-3.28")
sys.path.insert(0, WD)
import wx_sender

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

hwnd = wx_sender.find_main_window()
mx, my, mw, mh = wx_sender.get_rect(hwnd)
print("窗口 rect = (%d,%d) %dx%d" % (mx, my, mw, mh))

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

# 输入区工具栏的 y 大约在窗口高度的 0.93 附近
# 从之前分析:工具栏 y ≈ 窗口高 * (1456/1540) = 0.945
toolbar_y = int(mh * 0.945)
print("工具栏 y ≈", toolbar_y)

# 裁右侧区域:窗口内 x 从 0.78 到 1.0
x0 = int(mw * 0.78)
region = img.crop((x0, toolbar_y - 60, mw, toolbar_y + 60))
region = region.resize((region.width * 4, region.height * 4), 1)
p = os.path.join(WD, "zoom_right.png")
region.save(p)
print("右侧放大图:", os.path.basename(p), region.size, " 原区域 x0 =", x0)

# 同时裁左侧(麦克风附近)作对照
x1 = int(mw * 0.26)
region2 = img.crop((x1, toolbar_y - 60, x1 + 400, toolbar_y + 60))
region2 = region2.resize((region2.width * 4, region2.height * 4), 1)
p2 = os.path.join(WD, "zoom_left.png")
region2.save(p2)
print("左侧放大图:", os.path.basename(p2), region2.size, " 原区域 x1 =", x1)
print()
print("换算: 放大图坐标 / 4 + (x0 或 x1, toolbar_y-60) = 窗口内坐标")
print("      再 + (%d, %d) = 屏幕坐标" % (mx, my))
