# -*- coding: utf-8 -*-
"""
测试 PrintWindow:能否在不抢焦点的情况下截取微信窗口内容。
只读 —— 不激活窗口、不点击、不写内存。
"""
import os
import sys
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()

PW_RENDERFULLCONTENT = 0x00000002

hwnd = user32.FindWindowW("Qt51514QWindowIcon", "微信")
print("微信 hwnd =", hwnd)

rect = wintypes.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(rect))
w, h = rect.right - rect.left, rect.bottom - rect.top
print("窗口 %dx%d  rect=(%d,%d)" % (w, h, rect.left, rect.top))
print("前台窗口是微信吗:", user32.GetForegroundWindow() == hwnd)

hdc_win = user32.GetWindowDC(hwnd)
hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
hbm = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
gdi32.SelectObject(hdc_mem, hbm)

print()
print("=== 尝试 PrintWindow (不抢焦点) ===")
ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
print("  PrintWindow 返回:", ok)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


bi = BITMAPINFO()
bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
bi.bmiHeader.biWidth = w
bi.bmiHeader.biHeight = -h          # 负数 = 自上而下
bi.bmiHeader.biPlanes = 1
bi.bmiHeader.biBitCount = 32
bi.bmiHeader.biCompression = 0

buf = ctypes.create_string_buffer(w * h * 4)
got = gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, ctypes.byref(bi), 0)
print("  GetDIBits 返回:", got)

try:
    from PIL import Image
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wx_printwindow.png")
    img.convert("RGB").save(out)
    # 统计非黑像素比例,判断是否真的抓到了内容
    gray = img.convert("L")
    px = list(gray.getdata())
    nonblack = sum(1 for p in px if p > 12)
    print("  已保存:", out)
    print("  非黑像素占比: %.1f%%" % (100.0 * nonblack / len(px)))
    if nonblack / len(px) < 0.02:
        print("  >>> 几乎全黑:PrintWindow 没抓到内容(微信自绘窗口常见)")
    else:
        print("  >>> 抓到内容了")
except Exception as e:
    print("  保存失败:", type(e).__name__, e)

gdi32.DeleteObject(hbm)
gdi32.DeleteDC(hdc_mem)
user32.ReleaseDC(hwnd, hdc_win)
print("\nDONE")
