# -*- coding: utf-8 -*-
"""
wx_sender.py —— 合规的微信发送模块(仅用官方 Win32 API)

设计原则(重要):
    只使用 Windows 官方能力:PrintWindow 截图 / SendInput 点击 /
    SetCursorPos / 剪贴板。**不读写微信进程内存**,不修改微信的任何开关。
    因此不会碰触"绕过平台风控"的操作。

坐标策略:
    全部用"渲染窗口相对比例",运行时换算为屏幕绝对坐标,适配不同 DPI/窗口尺寸。

发送流程:
    open_chat_by_search(name)  ->  聚焦输入框  ->  剪贴板粘贴文本  ->  回车发送

注意:
    点击前需要把微信窗口提到前台(Windows 不会把点击送到非前台窗口)。
    本模块提供 bring_to_front(),会短暂抢占前台焦点。
"""

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
user32.SetProcessDPIAware()

# 渲染窗口类名前缀(微信 4.1.x)
RENDER_CLASS_PREFIX = "MMUIRenderSubWindow"
MAIN_CLASS = "Qt51514QWindowIcon"


# ---------------------------------------------------------------------------
# 窗口定位
# ---------------------------------------------------------------------------

def find_main_window():
    """找到微信主窗口句柄。标题可能是 '微信' 或 '微信(3)'。"""
    h = user32.FindWindowW(MAIN_CLASS, "微信")
    if h:
        return h
    found = []

    def cb(hwnd, lparam):
        ln = user32.GetWindowTextLengthW(hwnd)
        if ln:
            b = ctypes.create_unicode_buffer(ln + 2)
            user32.GetWindowTextW(hwnd, b, ln + 2)
            if "微信" in b.value and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
        return True
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(EnumProc(cb), None)
    return found[0] if found else None


def find_render_window(hwnd):
    """找到自绘渲染子窗口(坐标基准)。找不到时回退主窗口。"""
    h = user32.FindWindowExW(hwnd, None, RENDER_CLASS_PREFIX + "HW", None)
    if h:
        return h
    kids = []

    def cb(child, lparam):
        b = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(child, b, 256)
        if RENDER_CLASS_PREFIX in b.value:
            r = wintypes.RECT()
            user32.GetWindowRect(child, ctypes.byref(r))
            kids.append(((r.right - r.left) * (r.bottom - r.top), child))
        return True
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumChildWindows(hwnd, EnumProc(cb), None)
    if kids:
        kids.sort(reverse=True)
        return kids[0][1]
    return hwnd


def get_rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def bring_to_front(hwnd):
    """把窗口提到前台。会短暂抢占焦点。

    Windows 有「前台锁定」:后台进程不能直接抢前台,SetForegroundWindow
    会被忽略。这里用 AttachThreadInput 把当前线程附加到前台窗口线程,
    从而获得合法的设前台权限(标准 Win32 做法)。
    """
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)   # SW_RESTORE
    time.sleep(0.2)

    if user32.GetForegroundWindow() == hwnd:
        return True

    fg = user32.GetForegroundWindow()
    cur = kernel32.GetCurrentThreadId()
    fg_t = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tg_t = user32.GetWindowThreadProcessId(hwnd, None)

    attached = []
    try:
        for t in (fg_t, tg_t):
            if t and t != cur and t not in attached:
                if user32.AttachThreadInput(cur, t, True):
                    attached.append(t)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        time.sleep(0.25)
    finally:
        for t in attached:
            try:
                user32.AttachThreadInput(cur, t, False)
            except Exception:
                pass

    time.sleep(0.3)
    return user32.GetForegroundWindow() == hwnd


# ---------------------------------------------------------------------------
# 输入注入(官方 SendInput)
# ---------------------------------------------------------------------------

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
VK_RETURN = 0x0D
VK_CONTROL = 0x11
VK_V = 0x56


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]


def _send(*inputs):
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    return user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(INPUT))


def click(x, y):
    """在屏幕绝对坐标点击(移动 + 左键按下抬起)。"""
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.15)
    down = INPUT(type=INPUT_MOUSE,
                 u=INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None)))
    up = INPUT(type=INPUT_MOUSE,
               u=INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None)))
    _send(down, up)


def _key(vk, up=False):
    flags = KEYEVENTF_KEYUP if up else 0
    return INPUT(type=INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(vk, 0, flags, 0, None)))


def press_enter():
    _send(_key(VK_RETURN), _key(VK_RETURN, up=True))


def paste():
    """Ctrl+V。"""
    _send(_key(VK_CONTROL), _key(VK_V), _key(VK_V, up=True), _key(VK_CONTROL, up=True))


def type_text(text):
    """用剪贴板粘贴文本(避开中文输入法)。"""
    import win32clipboard
    for _ in range(3):
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
            break
        except Exception:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
            time.sleep(0.3)
    else:
        raise RuntimeError("写剪贴板失败")
    time.sleep(0.15)
    paste()


# ---------------------------------------------------------------------------
# 坐标计算(全部基于渲染窗口比例)
# ---------------------------------------------------------------------------

class Layout:
    """渲染窗口内的布局比例。

    本组比例基于微信 4.1.9.57 / 窗口 1564x1132 的**实测截图**标定:
        输入框(提示文字处)  显示(545,587) / 预览 940x680
        语音按钮(麦克风)     显示(525,645)
        发送按钮(「发送」)    显示(872,645)
    换算为渲染窗口(1540x1120)相对比例。
    """

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.render = find_render_window(hwnd)
        self.rx, self.ry, self.rw, self.rh = get_rect(self.render)
        self.mx, self.my, self.mw, self.mh = get_rect(hwnd)

    def rel(self, fx, fy):
        """渲染窗口内的相对比例 -> 屏幕绝对坐标。"""
        return (self.rx + fx * self.rw, self.ry + fy * self.rh)

    def chat_input(self):
        """聊天输入框(点击以聚焦)。实测:输入区中上部空白处。"""
        # 显示(600,600) / 940x680 -> 归一化 0.638, 0.882
        return self.rel(0.638, 0.882)

    def send_button(self):
        """右下角「发送」按钮。实测 显示(872,645) -> 0.928, 0.949。"""
        return self.rel(0.928, 0.949)

    def voice_button(self):
        """输入区左侧麦克风按钮(语音输入/录音入口)。"""
        # 显示(525,645) -> 0.559, 0.949
        return self.rel(0.559, 0.949)

    def search_box(self):
        """左上搜索框。实测 显示(185,59) -> 0.197, 0.087。"""
        return self.rel(0.197, 0.087)

    def first_session(self):
        """会话列表第一条。实测可命中「文件传输助手」。"""
        return self.rel(0.126, 0.155)


def session_row_is_active(hwnd, rel_x=0.126, rel_y=0.155):
    """检测会话列表某一行是否为「当前已打开」(微信活动行背景为绿色)。

    直接点已打开的会话会把它 toggle 关闭 —— 这是二次发送失败的常见根因。
    返回 True 表示已打开,应跳过点击。
    """
    L = Layout(hwnd)
    # 取该行左侧一小块区域的颜色
    x, y = L.rel(rel_x - 0.06, rel_y)
    hdc = user32.GetWindowDC(hwnd)
    try:
        # GetPixel 在 gdi32 里
        px = gdi32.GetPixel(hdc, int(x), int(y))
        if px == 0xFFFFFFFF:      # CLR_INVALID
            return False
        r = px & 0xFF
        g = (px >> 8) & 0xFF
        b = (px >> 16) & 0xFF
        # 微信活动会话行:绿色 (约 21,172,112)
        return g > 120 and g > r + 30 and g > b + 20
    finally:
        user32.ReleaseDC(hwnd, hdc)


def describe(hwnd=None):
    """打印当前几何与推算坐标,便于排查。"""
    hwnd = hwnd or find_main_window()
    if not hwnd:
        return "未找到微信窗口"
    L = Layout(hwnd)
    lines = []
    lines.append("主窗口 hwnd=%s rect=%s" % (hwnd, get_rect(hwnd)))
    lines.append("渲染窗口 hwnd=%s rect=%s" % (L.render, get_rect(L.render)))
    lines.append("  搜索框   -> %s" % (L.search_box(),))
    lines.append("  首条会话 -> %s" % (L.first_session(),))
    lines.append("  输入框   -> %s" % (L.chat_input(),))
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
