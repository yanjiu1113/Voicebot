# -*- coding: utf-8 -*-
"""
session_guard.py —— 会话状态检测 + 防 toggle

解决两个问题:
  1) 【toggle 陷阱】微信会话已打开时,再点一次会把它关闭。
     -> 发语音前先判断"目标会话是否已打开",是则跳过点击。
  2) 【发错人】无法确认当前打开的是谁。
     -> 用 Windows 自带 OCR 读聊天标题,与目标名比对。

检测手段(均已实测):
  · OCR 标题:Windows.Media.Ocr 读聊天标题,实测可读出「测试号B」
  · 高亮检测:活动会话行背景为微信绿 (21,172,112),占比 ~74%;
              普通行 (238,238,240),占比 ~0%

坐标基于「微信最大化 + 屏幕 2560x1600」实测,按窗口尺寸比例换算。
"""

import ctypes
import os
import sys
import time
from ctypes import wintypes

_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.join(_HERE, "vendor_py") not in sys.path:
    sys.path.insert(0, os.path.join(_HERE, "vendor_py"))

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
_user32.SetProcessDPIAware()

# ---------------------------------------------------------------------------
# 基准窗口(实测时的最大化尺寸)。所有像素常量按此比例换算。
# ---------------------------------------------------------------------------
BASE_W, BASE_H = 2584, 1540

# 聊天标题区(窗口内像素,基准尺寸下实测 y=90 能读出标题)
TITLE_BOX = (540, 78, 950, 132)

# 会话列表行背景采样区(x 范围取会话名所在的行背景)
ROW_SAMPLE_X = (110, 500)
# ⚠️ 行位置为**实测标定**(2026-09-19,窗口 2584x1540):
#      row0 = 文件传输助手  y≈208
#      row1 = 会话B      y≈275
#      row3 = 会话C      y≈409
#      row4 = Woo          y≈476
#    => 行距 67(不是旧的 90!),首行中心 208。
#    旧值 90 会让 row>=2 偏移 40+ 像素,点到行间空白。
ROW_CENTER_Y0 = 208          # 基准尺寸下第 0 行中心
ROW_PITCH = 67               # 行距(实测)

# 微信活动会话行绿色(实测主色 (21,172,112),另见 (0,195,117))
WX_GREENS = ((21, 172, 112), (0, 195, 117), (7, 193, 96), (26, 173, 117))
GREEN_TOL = 30
GREEN_FRAC_HIT = 0.5         # 绿色占比超过此值判为高亮

# 判断"聊天区上部有没有内容":三段唯一色之和的阈值
# 实测:有消息 ≈ 54,无消息/空白 ≈ 6。取 15 留足余量。
MSG_UNIQ_OPEN = 15

# 标题区灰度极差:低于此值说明标题栏没有文字 -> 会话框是空白的
# 实测:打开 = 225,空白 = 0。取 40 留足余量。
TITLE_RANGE_BLANK = 40


def _rect(hwnd):
    r = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _grab(hwnd):
    """PrintWindow 截图,返回 PIL Image 与窗口几何。"""
    from PIL import Image
    x, y, w, h = _rect(hwnd)
    hdc = _user32.GetWindowDC(hwnd)
    mem = _gdi32.CreateCompatibleDC(hdc)
    bm = _gdi32.CreateCompatibleBitmap(hdc, w, h)
    _gdi32.SelectObject(mem, bm)
    _user32.PrintWindow(hwnd, mem, 0x00000002)

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
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    _gdi32.GetDIBits(mem, bm, 0, h, buf, ctypes.byref(bi), 0)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    _gdi32.DeleteObject(bm); _gdi32.DeleteDC(mem); _user32.ReleaseDC(hwnd, hdc)
    return img, (x, y, w, h)


def _scale_box(box, w, h):
    """把基准像素坐标按当前窗口尺寸换算。"""
    sx, sy = w / float(BASE_W), h / float(BASE_H)
    x0, y0, x1, y1 = box
    return (int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy))


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

_ocr_tmp = None


def _ocr_image(img, tag="t"):
    """对 PIL Image 做 OCR,返回识别文本(失败返回 '')。"""
    global _ocr_tmp
    import asyncio
    tmp_dir = os.path.join(_HERE, "voice_bot_shots")
    os.makedirs(tmp_dir, exist_ok=True)
    path = os.path.join(tmp_dir, "_ocr_%s.png" % tag)
    img.save(path)

    async def _run():
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.storage import StorageFile, FileAccessMode
        f = await StorageFile.get_file_from_path_async(path)
        st = await f.open_async(FileAccessMode.READ)
        dec = await BitmapDecoder.create_async(st)
        bmp = await dec.get_software_bitmap_async()
        eng = OcrEngine.try_create_from_user_profile_languages()
        if eng is None:
            return ""
        res = await eng.recognize_async(bmp)
        return res.text or ""

    try:
        return asyncio.run(_run())
    except Exception as e:
        print("  [OCR] 失败: %s: %s" % (type(e).__name__, str(e)[:80]))
        return ""


def _norm(s):
    """归一化:去空格与常见分隔符,便于比对。"""
    if not s:
        return ""
    for ch in " \t\u3000：:、|丨·.":
        s = s.replace(ch, "")
    return s.strip()


def read_chat_title(hwnd, debug=False):
    """读当前聊天窗口标题(会话名)。返回 (文本, 归一化文本)。"""
    img, _ = _grab(hwnd)
    w, h = img.size
    box = _scale_box(TITLE_BOX, w, h)
    crop = img.crop(box)
    crop = crop.resize((crop.width * 4, crop.height * 4), 1)
    txt = _ocr_image(crop, "title")
    if debug:
        print("  [OCR标题] 裁剪=%s 原文=%r 归一化=%r" % (box, txt, _norm(txt)))
    return txt, _norm(txt)


def screen_grab(hwnd):
    """用屏幕 BitBlt 抓窗口区域,返回 (PIL.Image, (x,y,w,h))。

    为什么不用 PrintWindow:
        实测微信的聊天区是**自绘渲染**,PrintWindow 抓出来是纯色
        (唯一色只有 2 个),看不到任何消息内容;
        而屏幕 BitBlt 能正常抓到(唯一色近 300)。
        判断"会话是否打开"必须用这个。
    """
    from PIL import Image
    x, y, w, h = _rect(hwnd)
    W = _user32.GetSystemMetrics(0)
    H = _user32.GetSystemMetrics(1)
    hdc = _user32.GetDC(0)
    mem = _gdi32.CreateCompatibleDC(hdc)
    bm = _gdi32.CreateCompatibleBitmap(hdc, W, H)
    _gdi32.SelectObject(mem, bm)
    _gdi32.BitBlt(mem, 0, 0, W, H, hdc, 0, 0, 0x00CC0020)

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
    bi.bmiHeader.biWidth = W
    bi.bmiHeader.biHeight = -H
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(W * H * 4)
    _gdi32.GetDIBits(mem, bm, 0, H, buf, ctypes.byref(bi), 0)
    img = Image.frombuffer("RGBA", (W, H), buf, "raw", "BGRA", 0, 1).convert("RGB")
    _gdi32.DeleteObject(bm)
    _gdi32.DeleteDC(mem)
    _user32.ReleaseDC(0, hdc)
    return img, (x, y, w, h)


def is_chat_open(hwnd):
    """判断右侧聊天区是否**真的打开了某个会话**(不依赖 OCR)。

    原理:
        聊天区上部有消息内容时颜色丰富;没内容(空白或空会话)时近乎纯色。
        实测(窗口内坐标 y150~700, x700~2300):
            会话B(有消息)  唯一色 24 / 16 / 14
            会话C(无消息)  唯一色 1 / 1 / 4
            会话框空白         唯一色 1 / 1 / 4

        ⚠️ 注意:这个判据只能区分"有没有消息内容",
           **不能区分"空会话"和"完全空白"**。所以它只用于:
             · 确认点击后确实切换了(内容变了)
             · 避免在明显空白时盲目再点
           判定"是否为目标会话"仍然以 OCR 标题为准。

    Returns: (是否有内容, 唯一色总数)
    """
    import numpy as np
    img, (x, y, w, h) = screen_grab(hwnd)
    a = np.array(img)
    total = 0
    parts = []
    for ry0, ry1 in ((150, 300), (300, 500), (500, 700)):
        px0 = x + int(700 * w / BASE_W)
        px1 = x + int(2300 * w / BASE_W)
        py0 = y + int(ry0 * h / BASE_H)
        py1 = y + int(ry1 * h / BASE_H)
        px0, py0 = max(0, px0), max(0, py0)
        px1, py1 = min(a.shape[1], px1), min(a.shape[0], py1)
        if px1 <= px0 or py1 <= py0:
            continue
        box = (a[py0:py1, px0:px1, :3] // 16).reshape(-1, 3)
        u = int(len(np.unique(box, axis=0)))
        parts.append(u)
        total += u
    return total >= MSG_UNIQ_OPEN, total


def title_area_range(hwnd):
    """标题区的灰度极差 —— 判断"有没有打开会话"的**硬判据**。

    原理:
        会话打开时,标题栏有文字 -> 灰度极差大;
        会话框空白(未选中任何会话)时,标题区是纯色 -> 极差 ≈ 0。
        实测:打开 = 225,空白 = 0。

    ⚠️ 这是**不依赖 OCR** 的判据,比"标题读不读得出"可靠得多。
       实测 OCR 在会话打开时基本都能读出,读不出的情况几乎都是真的空白。

    返回 (是否空白, 极差)
    """
    import numpy as np
    img, _ = _grab(hwnd)          # PrintWindow 能截到标题区(常规绘制)
    w, h = img.size
    sx = w / float(BASE_W)
    sy = h / float(BASE_H)
    x0, y0, x1, y1 = TITLE_BOX
    c = img.crop((int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy)))
    a = np.array(c.convert("L")).astype(np.float32)
    rng = float(a.max() - a.min())
    return rng < TITLE_RANGE_BLANK, rng


def is_row_highlighted(hwnd, row, debug=False):
    """判断会话列表第 row 行是否为活动(高亮)行。

    ⚠️ 注意:绿色高亮标记的是"最近聊天",**不等于"当前正在打开"**。
       会话框空白时,第 0 行仍然是绿色。所以它只能当辅助判据,
       判断"是否已打开"请用 is_chat_open()。

    row 是**界面行号**(0 起),坐标以截图为基准换算,与窗口在屏幕上的
    位置无关 —— 实测窗口原点为 (-12,-12) 时依然正确。
    """
    img, _ = _grab(hwnd)
    w, h = img.size
    sy = h / float(BASE_H)
    cy = int((ROW_CENTER_Y0 + row * ROW_PITCH) * sy)
    x0 = int(ROW_SAMPLE_X[0] * (w / float(BASE_W)))
    x1 = int(ROW_SAMPLE_X[1] * (w / float(BASE_W)))
    band = img.crop((x0, cy - 18, x1, cy + 18))
    px = list(band.getdata())
    if not px:
        return False, 0.0
    hit = 0
    for p in px:
        for c in WX_GREENS:
            if abs(p[0] - c[0]) < GREEN_TOL and abs(p[1] - c[1]) < GREEN_TOL \
               and abs(p[2] - c[2]) < GREEN_TOL:
                hit += 1
                break
    frac = hit / float(len(px))
    if debug:
        print("  [高亮检测] 行%d y=%d 绿色占比=%.0f%%" % (row, cy, frac * 100))
    return frac >= GREEN_FRAC_HIT, frac


# ---------------------------------------------------------------------------
# 主入口:确保目标会话已打开(不误关)
# ---------------------------------------------------------------------------

def open_chat_by_search(hwnd, name, verbose=True, settle=1.2, max_retry=2):
    """用左上角搜索框打开会话 —— 不依赖行号,最可靠。

    为什么需要它:
        会话列表的行号会随聊天活跃度变化(而且 `--list` 的编号是按
        "最后消息时间"排的,与界面"置顶+时间"的顺序**并不一致**),
        所以只靠 row 定位有发错人的风险。

    流程: 点搜索框 -> 粘贴名字 -> 回车 -> OCR 核对标题

    Returns: dict {ok, action, by, title, note}
    """
    import wx_sender as ws

    def log(*a):
        if verbose:
            print(*a)

    res = {"ok": False, "action": None, "by": None, "title": "", "note": ""}
    target = _norm(name)
    if not target:
        res["note"] = "未提供会话名,无法搜索"
        return res

    # 已经是目标会话就不动(避免 toggle 关闭)
    _, t = read_chat_title(hwnd, debug=False)
    if t and target in t:
        log("  [搜索] 标题已匹配「%s」-> 跳过" % t)
        res.update(ok=True, action="skip", by="ocr", title=t)
        return res

    try:
        L = ws.Layout(hwnd)
        sx, sy = L.search_box()
    except Exception as e:
        res["note"] = "无法获取搜索框坐标: %s" % str(e)[:60]
        return res

    log("  [搜索] 点搜索框 (%d,%d) 并输入「%s」" % (sx, sy, name))
    for attempt in range(max_retry):
        ws.click(sx, sy)
        time.sleep(0.45)
        ws.type_text(name)           # 剪贴板粘贴,避免输入法干扰
        time.sleep(0.7)
        ws.press_enter()
        time.sleep(settle)

        _, t = read_chat_title(hwnd, debug=False)
        if t and target in t:
            log("  [搜索] 打开成功,标题确认「%s」✅" % t)
            res.update(ok=True, action="search", by="ocr", title=t)
            return res
        log("  [搜索] 第%d次尝试后标题为「%s」,与目标不符" % (attempt + 1, t or "?"))

    res["note"] = ("搜索「%s」后标题未匹配(读到「%s」)" % (name, t or "空"))
    return res


def ensure_chat_open(hwnd, row, name, verbose=True, click_fn=None,
                     settle=1.2, max_retry=2, strict=True, method="auto",
                     probe_rows=6):
    """确保「name」对应的会话处于打开状态。

    strict=True(默认):无法确认会话状态时返回 ok=False,由调用方中止发送。
                       宁可这条不发,也不能发错人。
    strict=False       :沿用旧的"保守继续"行为(不推荐)。

    核心逻辑(解决 toggle 关闭问题 + 行号漂移):
        1. 先读当前聊天标题
        2. 若已是目标会话  -> 【不点击】直接返回
        3. 逐行探测        -> 点第 0..probe_rows-1 行,用 OCR 标题找目标
                              (行号会漂移,实测文件传输助手从第0行漂到第1行)
        4. 按配置行号点击  -> 读标题确认;读不出再查该行是否高亮
        5. 仍无法确认      -> strict 时判失败

    Args:
        click_fn: 可调用对象,签名 f(x, y) 执行一次点击
        settle: 点击/切换后等待秒数
        strict: 无法确认时是否判失败(默认 True)
        method: 'auto' 先逐行探测再按行号点;'probe' 只探测;
                'search' 先用搜索框(实测搜索框不可靠);'row' 只按行号
        probe_rows: 逐行探测时最多试几行

    Returns:
        dict: {ok, action, by, title, note}
            action: 'skip'(未点击) / 'probe'(探测命中) /
                    'click'(按行号点击) / 'abort'(中止)
            by:     'ocr' / 'highlight' / 'none'
    """
    if click_fn is None:
        import wx_sender
        click_fn = wx_sender.click

    def log(*a):
        if verbose:
            print(*a)

    res = {"ok": False, "action": None, "by": None, "title": "", "note": ""}

    # --- 0. 先判断"聊天区是否真的打开了会话" ---
    #
    # 这是最关键的一步,而且是**不依赖 OCR** 的硬判据:
    #   会话打开 -> 消息区有头像/气泡/文字,量化色数 ≈ 290
    #   会话框空白 -> 大面积纯色,量化色数 ≈ 2
    # (PrintWindow 截不到微信自绘的聊天区,所以这里用屏幕 BitBlt)
    #
    # 为什么必须区分:
    #   会话框**空白**时,标题读出来是空,而且第 0 行**仍然是绿色**
    #   (绿色标记"最近聊天",不是"正在打开")。若据此认为"已打开",
    #   就会跳过点击 -> 在空白会话上录音发送 -> 鼠标动了却发不出去;
    #   反过来若盲目点击,又会把本来打开的会话 toggle 关闭。
    open_now, uniq = is_chat_open(hwnd)
    blank, t_range = title_area_range(hwnd)
    title_raw, title = read_chat_title(hwnd, debug=verbose)
    target = _norm(name)
    res["title"] = title
    res["open_pixels"] = uniq
    res["title_range"] = t_range
    log("  [会话] 标题区极差=%.0f -> %s;聊天区色数=%d" % (
        t_range, "会话框空白" if blank else "有会话", uniq))

    if not blank:
        # 标题栏有文字 -> 确实打开了某个会话。别乱点,点了会关闭它。
        if title and target and target in title:
            log("  [会话] 标题已匹配「%s」-> 跳过点击(避免 toggle 关闭)" % name)
            res.update(ok=True, action="skip", by="ocr")
            return res
        if title:
            log("  [会话] 当前标题「%s」≠ 目标「%s」-> 需要切换" % (title, target))
        else:
            # 有会话打开,但标题 OCR 读不出 —— 宁可不动(避免误关)
            log("  [会话] 有会话打开但标题读不出 -> 用活动行辅助判断")
            hl, frac = is_row_highlighted(hwnd, row, debug=verbose)
            if hl:
                log("  [会话] 第%d行是活动行(%.0f%%)-> 视为已打开,跳过" % (row, frac * 100))
                res.update(ok=True, action="skip", by="highlight-open")
                return res
            log("  [会话] 第%d行非活动行 -> 需要切换,但无法确认目标,保守中止" % row)
            res.update(ok=False, action="abort", by="none",
                       note="标题读不出且行号非活动行,无法安全切换")
            return res
    else:
        log("  [会话] 会话框是空白的 -> 必须点击打开")

    # --- 2. 高亮检测只作辅助(不再单独作为"已打开"的依据) ---
    if not open_now and strict is False:
        hl, frac = is_row_highlighted(hwnd, row, debug=verbose)
        if hl:
            log("  [会话] 第%d行高亮(%.0f%%)-> 非严格模式:认定已打开" % (row, frac * 100))
            res.update(ok=True, action="skip", by="highlight")
            return res

    # --- 3. 点击打开 ---
    #
    # 定位策略(method):
    #   'auto'(默认): 先逐行探测(可靠),再按配置行号点。
    #                  **不先用搜索框** —— 实测搜索框那条路一直失败
    #                  (点 (504,131) 后标题不变),只是白白多等近 2 秒。
    #   'probe': 只逐行探测
    #   'search': 只用搜索框(实测不可靠,保留备查)
    #   'row'  : 只按行号点
    if target and method == "search":
        s = open_chat_by_search(hwnd, name, verbose=verbose, settle=settle,
                                max_retry=max(1, max_retry - 1))
        if s.get("ok"):
            res.update(ok=True, action="search", by=s.get("by"), title=s.get("title"))
            return res
        log("  [搜索] 未成功(%s)" % s.get("note"))
        res.update(ok=False, action="abort", by="none",
                   note="搜索定位失败: %s" % s.get("note"))
        return res

    import wx_voice_sender as vS

    # --- 3a. 行号漂移自动纠正 ---
    #    配置里的 row 是"上次标定时的行号",而会话列表顺序会变
    #    (实测:文件传输助手从第 0 行漂到第 1 行)。
    #    所以这里**逐个试**若干行,用 OCR 标题确认到底哪一行是目标。
    #
    #    ⚠️ toggle 陷阱:点"已打开"的会话会把它**关掉**。
    #       为彻底避免自伤,进入探测前先确认聊天区是**空白**的
    #       (若已有会话打开,直接走 3b 按行号切换,不做逐行试探)。
    if target and method in ("auto", "probe") and not open_now:
        log("  [探测] 聊天区空白,逐行查找「%s」(最多 %d 行)…" % (name, probe_rows))
        found_row = None
        found_title = ""
        for r in range(probe_rows):
            gx, gy = vS.session_row_pos(hwnd, r)
            click_fn(gx, gy)
            time.sleep(settle)
            _, t = read_chat_title(hwnd, debug=False)

            # 点完变成空白 -> 刚才点到的是"已打开"的那一行,把它关掉了。
            # 需要重新打开它,否则会话框会一直空白。
            if not t:
                still_open, _u = is_chat_open(hwnd)
                if not still_open:
                    log("  [探测] 第%d行点击后聊天区仍空白 -> 再点一次" % r)
                    click_fn(gx, gy)
                    time.sleep(settle)
                    _, t = read_chat_title(hwnd, debug=False)
            if t and target in t:
                found_row, found_title = r, t
                log("  [探测] 第%d行 = 「%s」✅" % (r, t))
                break
            log("  [探测] 第%d行 -> 「%s」不符" % (r, t or "(读不出)"))
        if found_row is not None:
            note = ""
            if found_row != row:
                note = ("row 已漂移: 配置 %d -> 实际 %d(建议把该会话置顶以固定)"
                        % (row, found_row))
                log("  [探测] ⚠ " + note)
            res.update(ok=True, action="probe", by="ocr",
                       title=found_title, row_found=found_row, note=note)
            return res
        log("  [探测] %d 行内没找到「%s」" % (probe_rows, name))

    # --- 3b. 按配置行号点击 ---
    x, y = vS.session_row_pos(hwnd, row)
    for attempt in range(max_retry):
        log("  [会话] 点击第%d行 (%d,%d) 第%d次" % (row, x, y, attempt + 1))
        click_fn(x, y)
        time.sleep(settle)

        t_raw, t = read_chat_title(hwnd, debug=False)
        if t and target and target in t:
            log("  [会话] 点击后标题确认「%s」✅" % t)
            res.update(ok=True, action="click", by="ocr", title=t)
            return res
        if t:
            # 标题能读出但与目标不符 -> 说明行号已经漂移,重试同一行没意义
            log("  [会话] 点击后标题为「%s」,与目标不符 -> 行号可能已漂移" % t)
            break
        else:
            # 标题为空 —— 可能是 OCR 不稳,也可能会话真没打开。
            # 再用高亮辅助判断一次,但仍要**再读一次标题**做最终确认。
            hl, frac = is_row_highlighted(hwnd, row, debug=False)
            if hl:
                time.sleep(0.5)
                _, t2 = read_chat_title(hwnd, debug=False)
                if t2 and (not target or target in t2):
                    log("  [会话] 高亮+标题复核通过「%s」✅" % t2)
                    res.update(ok=True, action="click", by="ocr", title=t2)
                    return res
                log("  [会话] 第%d行高亮(%.0f%%)但标题为空/不符 -> 不算打开"
                    % (row, frac * 100))
            log("  [会话] 点击后仍无法确认(第%d次)" % (attempt + 1))

    # --- 4. 终检:再读一次标题 ---
    #
    # 走到这里说明前面没能确认。**最后一次机会**:如果此刻标题确实匹配,
    # 说明其实已经打开了(只是刚才那一次读失败),可以放行。
    time.sleep(0.4)
    _, final_title = read_chat_title(hwnd, debug=False)
    if target and final_title and target in final_title:
        log("  [会话] 终检通过「%s」✅" % final_title)
        res.update(ok=True, action="click", by="ocr-final", title=final_title)
        return res

    # --- 5. 判失败 ---
    #
    # 重要:此时我们**不知道当前打开的是哪个会话**。
    # 如果继续发语音条,可能发到错误的人那里,或者发到空白会话
    # (表现为:鼠标动了、点了发送,但什么也没发出去)。
    # 因此中止本次发送。
    #
    # (早期版本这里返回 ok=True 并"保守继续",那是错的:
    #  它把"无法确认"当成了"安全",实际是最高风险的时刻。)
    if not final_title:
        log("  [会话] ✗ 当前会话框是空白的(没有选中任何会话)")
        log("         可能是上一次点击把它 toggle 关闭了")
        note = "会话框空白(未选中任何会话),已中止发送"
    else:
        log("  [会话] ✗ 无法确认会话状态(当前标题「%s」与目标「%s」不符)"
            % (final_title, target))
        note = "无法确认会话状态(标题不符),已中止发送"
    log("         为安全起见中止本次发送,避免发错人/发空")
    res.update(ok=False, action="abort", by="none", title=final_title, note=note)
    return res


if __name__ == "__main__":
    import wx_sender
    hwnd = wx_sender.find_main_window()
    if not hwnd:
        print("未找到微信窗口")
        raise SystemExit(1)
    print("微信 hwnd =", hwnd, _rect(hwnd))
    print()
    print("--- 读标题 ---")
    read_chat_title(hwnd, debug=True)
    print()
    print("--- 各行高亮 ---")
    for r in range(6):
        is_row_highlighted(hwnd, r, debug=True)
