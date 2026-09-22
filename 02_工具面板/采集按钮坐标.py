# -*- coding: utf-8 -*-
"""
【鼠标位置实时检测 / 按钮坐标采集】

把鼠标移到微信的按钮上,窗口里**实时**显示坐标;按 F8 记录,按 F9 保存。
不用等倒数,也不用来回切窗口 —— 本窗口是**置顶**的,不会丢焦点。

什么时候需要跑它:
  · 换了分辨率 / 改了 DPI 缩放
  · 微信窗口尺寸和之前不同(不是 2584x1540)
  · 日志里出现"进入录音失败"或坐标相关的报错

用法:
    启动.cmd → [C] 采集按钮坐标
    或:  python 02_工具面板\采集按钮坐标.py

窗口里显示:
    坐标           鼠标的屏幕绝对坐标(物理像素)
    所在窗口       鼠标下面那个窗口的标题 + 类名
    是否微信       是不是微信主窗口(✓ = 可以放心记录)
    距窗口右/下    距该窗口右边缘 / 下边缘还有多少像素(这个参照系最稳)

热键(全局生效,不需要本窗口获得焦点):
    F8   记录当前鼠标位置到"待记录"的那一项
    F9   保存并退出(写入 01_核心模块/voice_button_coords.json)
    F10  退出不保存

⚠️ 采集 send_btn / cancel_btn 时,微信必须**正在录音**(只有录音时才会
   出现 ⬆ 发送 和 ✕ 取消)。所以流程是:
     ① 先记录「语音条按钮」(这一步不需要录音)
     ② 点微信的语音条按钮开始录音
     ③ 记录「录音中 ⬆ 发送」和「录音中 ✕ 取消」
     ④ 点窗口里的「结束录音」把录音取消掉,别留下空白语音

本文件放在 02_工具面板/ 或桌面都能正常工作(路径自动判断)。
"""
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

# ---------------------------------------------------------------------------
# 路径:兼容 VoiceBot/02_工具面板/ 与 桌面/ 两种摆放
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
CORE = None
for _c in (os.path.join(HERE, "..", "01_核心模块"),        # 在 02_工具面板/
           os.path.join(HERE, "VoiceBot", "01_核心模块"),   # 在桌面
           HERE):
    if os.path.isdir(_c):
        CORE = os.path.abspath(_c)
        break
OUT = os.path.join(CORE, "voice_button_coords.json")

u = ctypes.windll.user32
try:
    u.SetProcessDPIAware()          # 必须:否则 GetCursorPos 返回缩放后的逻辑坐标
except Exception:
    pass

GA_ROOT = 2
VK = {"F8": 0x77, "F9": 0x78, "F10": 0x79}
WECHAT_CLASS = "Qt51514QWindowIcon"

# 要采集的项(键名必须与 wx_voice_sender.BUTTONS_MEASURED 一致)
STEPS = [
    ("voice_btn", "语音条按钮", "点它会开始录音的那个圆形声波图标"),
    ("send_btn", "录音中 ⬆ 发送", "先点语音条开始录音,再指绿色的 ⬆ 发送键"),
    ("cancel_btn", "录音中 ✕ 取消", "指录音时灰底的 ✕ 取消键"),
]


# ---------------------------------------------------------------------------
# Win32 小工具
# ---------------------------------------------------------------------------

def cursor_pos():
    p = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def root_of(hwnd):
    return (u.GetAncestor(hwnd, GA_ROOT) or hwnd) if hwnd else 0


def window_at(x, y):
    return root_of(u.WindowFromPoint(wintypes.POINT(int(x), int(y))))


def win_title(hwnd):
    n = u.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    b = ctypes.create_unicode_buffer(n + 2)
    u.GetWindowTextW(hwnd, b, n + 2)
    return b.value


def win_class(hwnd):
    b = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(hwnd, b, 256)
    return b.value


def win_rect(hwnd):
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def find_wechat():
    """找微信主窗口(纯 ctypes,不依赖 vendor_py)。"""
    h = u.FindWindowW(WECHAT_CLASS, "微信")
    if h:
        return h
    found = []

    def cb(hwnd, lp):
        if u.IsWindowVisible(hwnd) and "微信" in win_title(hwnd):
            found.append(hwnd)
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                     wintypes.LPARAM)(cb), 0)
    return found[0] if found else 0


def key_pressed(vk):
    """全局检测按键(不需要本窗口有焦点)。"""
    return bool(u.GetAsyncKeyState(vk) & 0x8000)


# ---------------------------------------------------------------------------
# 界面
# ---------------------------------------------------------------------------

def main():
    try:
        import tkinter as tk
    except Exception as e:
        print("无法加载 tkinter:%s" % e)
        return 1

    state = {"idx": 0, "pts": {}, "saved": False}
    prev = {k: False for k in VK}

    root = tk.Tk()
    root.title("鼠标位置实时检测")
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.95)
    except Exception:
        pass
    root.resizable(False, False)

    FONT = ("Consolas", 11)
    vars_ = {}
    rows = [("pos", "坐标"), ("win", "所在窗口"), ("cls", "窗口类名"),
            ("edge", "距边缘右/下"), ("iswx", "是否微信")]
    for i, (k, label) in enumerate(rows, start=1):
        tk.Label(root, text=label, font=("Microsoft YaHei", 9), anchor="w"
                 ).grid(row=i, column=0, sticky="w", padx=(10, 4))
        v = tk.StringVar(value="-")
        vars_[k] = v
        tk.Label(root, textvariable=v, font=FONT, anchor="w",
                 fg="#0a7" if k == "iswx" else "#111"
                 ).grid(row=i, column=1, sticky="w", padx=(0, 12))

    tk.Frame(root, height=1, bg="#ccc").grid(row=6, column=0, columnspan=2,
                                             sticky="ew", pady=6)

    cur = tk.StringVar()
    tk.Label(root, textvariable=cur, font=("Microsoft YaHei", 9, "bold"),
             fg="#b60", anchor="w", wraplength=350, justify="left"
             ).grid(row=7, column=0, columnspan=2, sticky="w", padx=10)

    lst = tk.StringVar()
    tk.Label(root, textvariable=lst, font=FONT, anchor="w", justify="left"
             ).grid(row=8, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 2))

    tk.Label(root, text="F8 记录 · F9 保存并退出 · F10 退出不保存",
             font=("Microsoft YaHei", 8), fg="#777", anchor="w"
             ).grid(row=9, column=0, columnspan=2, sticky="w", padx=10)

    btns = tk.Frame(root)
    btns.grid(row=10, column=0, columnspan=2, sticky="ew", padx=10, pady=(6, 10))

    # 本工具自己的窗口句柄(用来把"鼠标在自己身上"排除掉)
    _hud = {"root": 0}

    def hud_root():
        if not _hud["root"]:
            try:
                wid = root.winfo_id()
                _hud["root"] = root_of(u.GetParent(wid) or wid)
            except Exception:
                pass
        return _hud["root"]

    def refresh_list():
        out = []
        for k, _name, _tip in STEPS:
            if k in state["pts"]:
                out.append("✓ %-11s %s" % (k, tuple(state["pts"][k])))
            else:
                out.append("· %-11s 未记录" % k)
        lst.set("\n".join(out))
        place()          # 文本长度变了,窗口宽度会变,重新贴右边

    def next_prompt():
        if state["idx"] < len(STEPS):
            _k, name, tip = STEPS[state["idx"]]
            cur.set("待记录 (%d/%d):%s\n%s"
                    % (state["idx"] + 1, len(STEPS), name, tip))
        else:
            cur.set("三项都记好了 → 按 F9 保存")
        place()          # 提示文字行数变了,重新贴右边

    def record():
        if state["idx"] >= len(STEPS):
            return False
        x, y = cursor_pos()
        if window_at(x, y) == hud_root():
            cur.set("鼠标在本工具窗口上 —— 请移到微信的按钮上再按 F8")
            return False
        k = STEPS[state["idx"]][0]
        state["pts"][k] = [x, y]
        state["idx"] += 1
        refresh_list()
        next_prompt()
        return True

    def save():
        wx = find_wechat()
        data = {"buttons": state["pts"]}
        if wx:
            x, y, w, h = win_rect(wx)
            data["window"] = {"x": x, "y": y, "w": w, "h": h}
            if not u.IsZoomed(wx):
                print("⚠ 警告:微信窗口**没有最大化**,采到的坐标只在当前尺寸有效")
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        state["saved"] = True
        print("=" * 70)
        print("已写入: %s" % OUT)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        print("=" * 70)
        return data

    def cancel_recording():
        """把微信正在进行的录音取消掉(录音时才会出现 ✕)。"""
        p = state["pts"].get("cancel_btn")
        if not p:
            print("还没记录 cancel_btn,无法自动取消。请手动点微信的 ✕")
            return
        x, y = int(p[0]), int(p[1])
        u.SetCursorPos(x, y)
        time.sleep(0.05)
        u.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
        u.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP
        print("已点击取消键 (%d, %d)" % (x, y))

    tk.Button(btns, text="记录 (F8)", width=11, command=record).pack(side="left")
    tk.Button(btns, text="保存 (F9)", width=11,
              command=lambda: (save(), root.quit())).pack(side="left", padx=4)
    tk.Button(btns, text="结束录音", width=9, command=cancel_recording
              ).pack(side="left", padx=4)
    tk.Button(btns, text="重来", width=6, command=lambda: (
        state.update(idx=0, pts={}), refresh_list(), next_prompt())
    ).pack(side="left")

    # ★ 只设**位置**,不设尺寸 —— 让窗口按内容自动撑开。
    #
    # 踩过的坑(两种都踩了):
    #   ① 提前用固定偏移算位置 -> 窗口比预留的宽,右半边被挤出屏幕外;
    #   ② 用 geometry("宽x高+..") 钉死尺寸 -> 那时标签还是空的,
    #      量到的 reqheight 偏小(371),等 refresh_list() 填上文本后
    #      实际需要 461,多出来的 90px 被裁掉 —— **按钮整排看不见**。
    #   所以:先填好文本,再 update_idletasks(),只设 "+x+y"。
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()

    def place():
        root.update_idletasks()
        w = root.winfo_reqwidth()
        root.geometry("+%d+%d" % (max(0, sw - w - 24), 60))

    if os.environ.get("VB_DUMP_LAYOUT"):
        def _dump():
            print("root: %dx%d at screen (%d,%d), req %dx%d"
                  % (root.winfo_width(), root.winfo_height(),
                     root.winfo_rootx(), root.winfo_rooty(),
                     root.winfo_reqwidth(), root.winfo_reqheight()))
            print("btns: mapped=%s rooty=%d h=%d"
                  % (btns.winfo_ismapped(), btns.winfo_rooty(), btns.winfo_height()))
            inner_bottom = root.winfo_rooty() + root.winfo_height()
            print("窗口底边 = %d;按钮底边 = %d -> %s"
                  % (inner_bottom, btns.winfo_rooty() + btns.winfo_height(),
                     "在窗口内 OK" if btns.winfo_rooty() + btns.winfo_height()
                     <= inner_bottom else "被裁掉!"))
            root.quit()
        root.after(800, _dump)

    def tick():
        # ---- 全局热键(边沿触发,避免长按重复)----
        for name, vk in VK.items():
            now = key_pressed(vk)
            if now and not prev[name]:
                if name == "F8":
                    record()
                elif name == "F9":
                    save()
                    root.quit()
                    return
                elif name == "F10":
                    root.quit()
                    return
            prev[name] = now

        # ---- 实时显示 ----
        x, y = cursor_pos()
        owner = window_at(x, y)
        vars_["pos"].set("(%d, %d)" % (x, y))
        if owner == hud_root():
            vars_["win"].set("(本工具窗口)")
            vars_["cls"].set("-")
            vars_["edge"].set("-")
            vars_["iswx"].set("鼠标移到微信上")
        else:
            vars_["win"].set((win_title(owner) or "(无标题)")[:28])
            vars_["cls"].set(win_class(owner)[:28])
            wx = find_wechat()
            if wx and owner == wx:
                vars_["iswx"].set("✓ 微信主窗口")
            else:
                vars_["iswx"].set("✗ 不是微信")
            if owner:
                ox, oy, ow, oh = win_rect(owner)
                vars_["edge"].set("%d / %d" % ((ox + ow) - x, (oy + oh) - y))
            else:
                vars_["edge"].set("-")
        root.after(50, tick)

    refresh_list()      # 先把文本填上(place() 在里面)
    next_prompt()       # 再定位 —— 顺序不能反,否则量到的尺寸偏小会把按钮裁掉
    place()
    root.after(50, tick)
    root.mainloop()

    if not state["saved"]:
        if state["pts"]:
            print("已退出,未保存。已记录的项:%s" % list(state["pts"].keys()))
        else:
            print("已退出,未记录任何坐标。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
