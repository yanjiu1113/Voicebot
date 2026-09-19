# -*- coding: utf-8 -*-
"""
标定步骤:把微信提到前台并截图,用于定位输入框/发送区。
本脚本【不点击、不发送】,只做窗口前置 + 截图。
"""
import ctypes
import os
import time
import sys
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wx_sender

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()

PW_RENDERFULLCONTENT = 0x00000002

hwnd = wx_sender.find_main_window()
if not hwnd:
    print("未找到微信窗口")
    raise SystemExit(1)

print("主窗口:", hwnd, wx_sender.get_rect(hwnd))
print("渲染窗口:", wx_sender.find_render_window(hwnd),
      wx_sender.get_rect(wx_sender.find_render_window(hwnd)))

print()
print("把微信提到前台(仅此一步会影响焦点)…")
ok = wx_sender.bring_to_front(hwnd)
print("  已前置:", ok)
time.sleep(0.8)

# PrintWindow 截图
rx, ry, rw, rh = wx_sender.get_rect(wx_sender.find_render_window(hwnd))
hdc_win = user32.GetWindowDC(hwnd)
hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
mx, my, mw, mh = wx_sender.get_rect(hwnd)
hbm = gdi32.CreateCompatibleBitmap(hdc_win, mw, mh)
gdi32.SelectObject(hdc_mem, hbm)
user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)


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
gdi32.GetDIBits(hdc_mem, hbm, 0, mh, buf, ctypes.byref(bi), 0)

from PIL import Image
img = Image.frombuffer("RGBA", (mw, mh), buf, "raw", "BGRA", 0, 1).convert("RGB")
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wx_front.png")
img.save(out)
print("  截图已保存:", out, img.size)

gdi32.DeleteObject(hbm)
gdi32.DeleteDC(hdc_mem)
user32.ReleaseDC(hwnd, hdc_win)

print()
print("渲染窗口相对坐标:", (rx, ry, rw, rh))
print("推算输入框绝对坐标:", wx_sender.Layout(hwnd).chat_input())
print()
print("DONE - 未点击、未发送")
