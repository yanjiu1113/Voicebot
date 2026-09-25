# -*- coding: utf-8 -*-
"""
win32_proto.py —— 统一给 Win32 函数声明 argtypes / restype

【为什么必须要这个】
    `ctypes.windll.user32` / `ctypes.windll.gdi32` 是**进程内共享对象**:
    任何被 import 的库只要给某个函数设过 restype,这个设置对我们**也生效**。

    实测踩到的坑:
        vendor_py\\uiautomation\\uiautomation.py 里有
            ctypes.windll.user32.GetWindowDC.restype       = ctypes.c_void_p
            ctypes.windll.gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
            ctypes.windll.gdi32.SelectObject.restype       = ctypes.c_void_p
        于是这些函数返回的是**完整 64 位句柄**(Python int)。
        而本项目的调用**没有声明 argtypes**,ctypes 会把整数参数按 32 位
        `c_int` 转换 —— 句柄一旦 >= 2**31 就抛:

            ArgumentError: argument 1: OverflowError: int too long to convert

    后果(两个真实故障):
        · 截图/录音检测偶发崩溃 -> 整轮中止,**发送键那一下根本没执行**
        · 崩溃发生在"推进消息水位"之前 -> 水位永不前进 ->
          下一轮又读到同一条旧消息 -> **一直用旧对话回答**
        · 截到空白图(坏句柄)-> 会话检测误判"会话框空白"

    解决办法:自己把用到的函数全部声明清楚(句柄一律用 HANDLE = c_void_p),
    并且在 import 时调用 apply() —— 覆盖别人设过的、也保证我们自己一致。

【用法】
    import ctypes
    _user32 = ctypes.windll.user32
    _gdi32 = ctypes.windll.gdi32
    import win32_proto
    win32_proto.apply(_user32, _gdi32)

    apply() 是幂等的,可以放心反复调用。
"""
import ctypes
from ctypes import wintypes

HANDLE = wintypes.HANDLE          # = c_void_p,64 位下就是完整指针
BOOL = wintypes.BOOL
DWORD = wintypes.DWORD
UINT = ctypes.c_uint
INT = ctypes.c_int
LPCWSTR = ctypes.c_wchar_p
LPVOID = ctypes.c_void_p


def apply(user32, gdi32, kernel32=None):
    """给用到的 Win32 函数声明原型。幂等。"""
    if user32 is not None:
        _user32(user32)
    if gdi32 is not None:
        _gdi32(gdi32)
    if kernel32 is not None:
        _kernel32(kernel32)


def _user32(u):
    # ---- 基础 ----
    u.SetProcessDPIAware.argtypes = []
    u.SetProcessDPIAware.restype = BOOL

    # ---- 找窗口 / 读窗口信息(句柄一律 HANDLE)----
    u.FindWindowW.argtypes = [LPCWSTR, LPCWSTR]
    u.FindWindowW.restype = HANDLE
    u.FindWindowExW.argtypes = [HANDLE, HANDLE, LPCWSTR, LPCWSTR]
    u.FindWindowExW.restype = HANDLE
    u.GetWindowTextLengthW.argtypes = [HANDLE]
    u.GetWindowTextLengthW.restype = INT
    u.GetWindowTextW.argtypes = [HANDLE, ctypes.POINTER(ctypes.c_wchar), INT]
    u.GetWindowTextW.restype = INT
    u.GetClassNameW.argtypes = [HANDLE, ctypes.POINTER(ctypes.c_wchar), INT]
    u.GetClassNameW.restype = INT
    u.GetWindowRect.argtypes = [HANDLE, ctypes.c_void_p]
    u.GetWindowRect.restype = BOOL
    u.IsWindowVisible.argtypes = [HANDLE]
    u.IsWindowVisible.restype = BOOL
    u.IsIconic.argtypes = [HANDLE]
    u.IsIconic.restype = BOOL
    u.IsZoomed.argtypes = [HANDLE]
    u.IsZoomed.restype = BOOL
    u.GetAncestor.argtypes = [HANDLE, UINT]
    u.GetAncestor.restype = HANDLE
    u.WindowFromPoint.argtypes = [wintypes.POINT]
    u.WindowFromPoint.restype = HANDLE

    # ---- 前台/焦点 ----
    u.GetForegroundWindow.argtypes = []
    u.GetForegroundWindow.restype = HANDLE
    u.SetForegroundWindow.argtypes = [HANDLE]
    u.SetForegroundWindow.restype = BOOL
    u.BringWindowToTop.argtypes = [HANDLE]
    u.BringWindowToTop.restype = BOOL
    u.SetActiveWindow.argtypes = [HANDLE]
    u.SetActiveWindow.restype = HANDLE
    u.ShowWindow.argtypes = [HANDLE, INT]
    u.ShowWindow.restype = BOOL
    u.GetWindowThreadProcessId.argtypes = [HANDLE, ctypes.c_void_p]
    u.GetWindowThreadProcessId.restype = DWORD
    u.AttachThreadInput.argtypes = [DWORD, DWORD, BOOL]
    u.AttachThreadInput.restype = BOOL

    # ---- 鼠标 ----
    u.GetCursorPos.argtypes = [ctypes.c_void_p]
    u.GetCursorPos.restype = BOOL
    u.SetCursorPos.argtypes = [INT, INT]
    u.SetCursorPos.restype = BOOL
    u.SendInput.argtypes = [UINT, LPVOID, INT]
    u.SendInput.restype = UINT
    u.mouse_event.argtypes = [DWORD, DWORD, DWORD, DWORD, ctypes.c_size_t]
    u.mouse_event.restype = None
    u.GetSystemMetrics.argtypes = [INT]
    u.GetSystemMetrics.restype = INT

    # ---- 设备上下文(句柄是本项目踩坑的重灾区)----
    u.GetDC.argtypes = [HANDLE]
    u.GetDC.restype = HANDLE
    u.GetWindowDC.argtypes = [HANDLE]
    u.GetWindowDC.restype = HANDLE
    u.ReleaseDC.argtypes = [HANDLE, HANDLE]
    u.ReleaseDC.restype = INT
    u.PrintWindow.argtypes = [HANDLE, HANDLE, UINT]
    u.PrintWindow.restype = BOOL
    # 注意:EnumWindows / EnumChildWindows 走回调,不声明原型更稳


def _gdi32(g):
    g.CreateCompatibleDC.argtypes = [HANDLE]
    g.CreateCompatibleDC.restype = HANDLE
    g.CreateCompatibleBitmap.argtypes = [HANDLE, INT, INT]
    g.CreateCompatibleBitmap.restype = HANDLE
    g.SelectObject.argtypes = [HANDLE, HANDLE]
    g.SelectObject.restype = HANDLE
    g.DeleteObject.argtypes = [HANDLE]
    g.DeleteObject.restype = BOOL
    g.DeleteDC.argtypes = [HANDLE]
    g.DeleteDC.restype = BOOL
    g.BitBlt.argtypes = [HANDLE, INT, INT, INT, INT, HANDLE, INT, INT, DWORD]
    g.BitBlt.restype = BOOL
    g.GetDIBits.argtypes = [HANDLE, HANDLE, UINT, UINT,
                            ctypes.c_void_p, ctypes.c_void_p, UINT]
    g.GetDIBits.restype = INT
    g.GetPixel.argtypes = [HANDLE, INT, INT]
    g.GetPixel.restype = DWORD


def _kernel32(k):
    k.GetCurrentThreadId.argtypes = []
    k.GetCurrentThreadId.restype = DWORD


def self_check(user32=None, gdi32=None):
    """自检:确认关键函数已被声明(供排错用)。返回 (ok, 报告行列表)。"""
    user32 = user32 or ctypes.windll.user32
    gdi32 = gdi32 or ctypes.windll.gdi32
    rows, ok = [], True
    for mod, name in ((user32, "GetWindowDC"), (user32, "SetCursorPos"),
                      (gdi32, "CreateCompatibleDC"), (gdi32, "CreateCompatibleBitmap"),
                      (gdi32, "SelectObject"), (gdi32, "GetDIBits")):
        has = getattr(mod, name).argtypes is not None
        rows.append("  %-24s argtypes %s" % (name, "已声明 ✅" if has else "未声明 ⚠"))
        ok = ok and has
    return ok, rows


if __name__ == "__main__":
    print("=" * 70)
    print("win32_proto 自检")
    print("=" * 70)
    u = ctypes.windll.user32
    g = ctypes.windll.gdi32
    print("自检前:")
    for line in self_check(u, g)[1]:
        print(line)
    apply(u, g, ctypes.windll.kernel32)
    print()
    print("apply() 之后:")
    ok, rows = self_check(u, g)
    for line in rows:
        print(line)
    print()
    print("结论:", "✅ 关键函数原型已就绪" if ok else "⚠ 有函数仍未声明")
    # 验证句柄能正常往返(句柄为大值也不应报错)
    hwnd = u.FindWindowW("Qt51514QWindowIcon", "微信")
    print("微信 hwnd =", hwnd)
    if hwnd:
        hdc = u.GetWindowDC(hwnd)
        mem = g.CreateCompatibleDC(hdc)
        bm = g.CreateCompatibleBitmap(hdc, 100, 100)
        print("句柄往返: hdc=%s mem=%s bm=%s" % (hdc, mem, bm))
        g.DeleteObject(bm)
        g.DeleteDC(mem)
        u.ReleaseDC(hwnd, hdc)
        print("✅ 句柄在 GDI 链路里正常往返(不再受 2**31 限制)")
