# -*- coding: utf-8 -*-
"""
固定微信窗口状态并重新标定。

做法:
  1. 最大化微信窗口(消除尺寸漂移)
  2. 截图,基于已知的界面比例(从 1564 宽时实测得到)推算各控件坐标
  3. 打印稳定的相对比例,便于长期复用
只读/只改窗口几何,不点击、不发送。
"""
import os
import sys
import ctypes
import time
from ctypes import wintypes

WD = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "WeChatBot_WXAUTO_SE-3.28")
sys.path.insert(0, WD)
import wx_sender

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()
SW_MAXIMIZE = 3

hwnd = wx_sender.find_main_window()
if not hwnd:
    print("未找到微信窗口"); raise SystemExit(1)
print("微信 hwnd =", hwnd)

wr = wintypes.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(wr))
print("最大化前 rect = (%d,%d) %dx%d" % (wr.left, wr.top, wr.right - wr.left, wr.bottom - wr.top))

print("[1] 最大化窗口")
user32.ShowWindow(hwnd, SW_MAXIMIZE)
time.sleep(1.0)
wr = wintypes.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(wr))
W, H = wr.right - wr.left, wr.bottom - wr.top
print("最大化后 rect = (%d,%d) %dx%d" % (wr.left, wr.top, W, H))

print("[2] 置于前台:", wx_sender.bring_to_front(hwnd))
time.sleep(0.6)

# 渲染子窗口
ch = wx_sender.find_render_window(hwnd)
rr = wintypes.RECT(); user32.GetWindowRect(ch, ctypes.byref(rr))
rx, ry, rw, rh = rr.left, rr.top, rr.right - rr.left, rr.bottom - rr.top
print("渲染 rect = (%d,%d) %dx%d" % (rx, ry, rw, rh))

# 截图
mx, my, mw, mh = wr.left, wr.top, W, H
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
p = os.path.join(WD, "maximized.png")
img.save(p)
px = list(img.convert("L").getdata())
nb = sum(1 for v in px if v > 12)
print("[3] 截图 %s  非黑 %.1f%%" % (os.path.basename(p), 100.0 * nb / len(px)))
gdi32.DeleteObject(bm); gdi32.DeleteDC(mem); user32.ReleaseDC(hwnd, hdc)

print()
print("=" * 74)
print("基于渲染区的相对比例(与窗口尺寸无关,可长期复用)")
print("=" * 74)
# 从 1564 宽窗口实测得到的关键比例(相对于渲染区)
RATIOS = {
    "搜索框":        (0.197, 0.087),
    "会话第1行":      (0.126, 0.155),
    "输入框(文字模式)": (0.275, 0.809),
    "麦克风按钮":     (0.304, 0.845),
    "语音-取消✕":     (0.395, 0.845),
    "语音-发送⬆":     (0.521, 0.845),
    "发送按钮(文字)":  (0.928, 0.949),
}
for k, (fx, fy) in RATIOS.items():
    x = rx + fx * rw
    y = ry + fy * rh
    print("  %-16s 比例=(%.3f,%.3f)  ->  屏幕=(%.0f, %.0f)" % (k, fx, fy, x, y))

print()
print("DONE - 未点击、未发送")
