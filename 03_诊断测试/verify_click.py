# -*- coding: utf-8 -*-
"""
验证点击是否真的送达:用 GetCursorPos 确认坐标落点,并检查点击效果。
流程: 点语音条按钮 -> 播 2 秒 -> 检查是否进入录音态 -> 点发送 -> 看是否发出。

⚠️ 本次会真的发出一条语音条到当前会话(文件传输助手)。
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

u = ctypes.windll.user32
g = ctypes.windll.gdi32

VOICE_BTN = (2349, 1455)
SEND_BTN = (2486, 1452)
SEND_BTN_OLD = (2488, 1452)
OUT_IDX, RATE = 26, 48000


def cursor():
    p = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(p))
    return (p.x, p.y)


def grab(hwnd):
    mx, my, mw, mh = wx_sender.get_rect(hwnd)
    hdc = u.GetWindowDC(hwnd)
    mem = g.CreateCompatibleDC(hdc)
    bm = g.CreateCompatibleBitmap(hdc, mw, mh)
    g.SelectObject(mem, bm)
    u.PrintWindow(hwnd, mem, 0x00000002)

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
    g.GetDIBits(mem, bm, 0, mh, buf, ctypes.byref(bi), 0)
    from PIL import Image
    img = Image.frombuffer("RGBA", (mw, mh), buf, "raw", "BGRA", 0, 1).convert("RGB")
    g.DeleteObject(bm); g.DeleteDC(mem); u.ReleaseDC(hwnd, hdc)
    return img, (mx, my)


def green_count(img, win_x0, win_y0):
    """统计窗口内工具栏区的绿色像素数(判断是否在录音态)。"""
    import numpy as np
    a = np.array(img)
    H, W, _ = a.shape
    m = np.zeros((H, W), bool)
    for (r, gg, b) in [(7, 193, 96), (0, 195, 117), (21, 172, 112)]:
        m |= (abs(a[:, :, 0].astype(int) - r) < 30) & \
             (abs(a[:, :, 1].astype(int) - gg) < 30) & \
             (abs(a[:, :, 2].astype(int) - b) < 30)
    band = m[int(H * 0.88):, :]
    return int(band.sum())


hwnd = wx_sender.find_main_window()
print("微信 hwnd =", hwnd, wx_sender.get_rect(hwnd))
print("置前:", wx_sender.bring_to_front(hwnd))
time.sleep(0.8)

print()
print("=== 步骤1: 记录当前光标 ===")
print("  光标 =", cursor())

print()
print("=== 步骤2: 点击语音条按钮", VOICE_BTN, "===")
wx_sender.click(*VOICE_BTN)
print("  点击后光标 =", cursor(), " (应等于目标)")
time.sleep(1.0)
img, (wx0, wy0) = grab(hwnd)
gc = green_count(img, wx0, wy0)
print("  工具栏绿色像素 =", gc, " (>1500 表示已进入录音态)")

print()
print("=== 步骤3: 播 2 秒音频 ===")
t = np.linspace(0, 2, int(RATE * 2), endpoint=False)
tone = (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16)
st = np.repeat(tone.reshape(-1, 1), 2, axis=1)
with sd.OutputStream(device=OUT_IDX, samplerate=RATE, channels=2, dtype="int16") as o:
    o.write(st)
time.sleep(0.5)
img, _ = grab(hwnd)
gc2 = green_count(img, wx0, wy0)
print("  播放后绿色像素 =", gc2)

print()
print("=== 步骤4: 点击发送 ==")
print("  目标 =", SEND_BTN)
wx_sender.click(*SEND_BTN)
print("  点击后光标 =", cursor(), " (若不等则 SetCursorPos 被拦)")
time.sleep(2.0)
img, _ = grab(hwnd)
gc3 = green_count(img, wx0, wy0)
print("  发送后绿色像素 =", gc3, " (应回落到录音前水平)")
img.save(os.path.join(WD, "click_verify_after.png"))
print("  截图: click_verify_after.png")
print()
if gc3 < 1000:
    print(">>> 已退出录音态 -> 发送点击生效 ✅")
else:
    print(">>> 仍在录音态 -> 发送点击未生效 ❌")
