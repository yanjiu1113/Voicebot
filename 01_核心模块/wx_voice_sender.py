# -*- coding: utf-8 -*-
"""
wx_voice_sender.py —— 通过「桌面微信 + 虚拟声卡」发送 GPT-SoVITS 语音条

原理(全部为官方能力,不含任何协议劫持或内存改写):
    1. 文本 -> GPT-SoVITS 合成 WAV
    2. WAV 播到虚拟线缆的【播放端】(如 Line 1)
    3. 微信的默认麦克风设为该线缆的【录音端】-> 就"听到"了这段音频
    4. 鼠标模拟:点语音条按钮开始录音 -> 播放 -> 点发送

为什么可行(实测依据):
    - 微信 4.x 聊天区自绘,UIA 拿不到控件,但**录音仍走系统默认输入设备**
    - 虚拟线缆的 播放端->录音端 直通实测 RMS=4721 峰值=30656
    - 静音时该总线峰值仅 =1(无环境音混入)

使用前提(缺一不可):
    1. 微信桌面版已登录,且窗口**最大化**(坐标按最大化标定)
    2. Windows 默认录音设备 = 虚拟线缆的录音端
       (默认播放设备保持扬声器,否则系统声音会灌进线缆)
    3. GPT-SoVITS 的 api_v2.py 已在 127.0.0.1:9880 运行
    4. 桌面未被锁屏;操作期间本脚本会抢占前台焦点

坐标标定说明:
    下列比例以「微信最大化 + 屏幕 2560x1600」实测标定。
    换分辨率/DPI 或微信未最大化时,用 calibrate.py 重新标定。
"""

import ctypes
import json
import os
import sys
import threading
import time
import wave
from ctypes import wintypes

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "vendor_py"))

# ---------------------------------------------------------------------------
# 可调配置
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 虚拟线缆配置
#
# ⚠️ 已从 Line 1 (Virtual Audio Cable) 切换到 VoiceMeeter:
#    Line 1 上存在一个**来源不明的 0.6 秒音频**(低沉女声,周期性播放,
#    峰值恒为 15958),会混进语音条开头。实测对照:
#         Line 1       : RMS=959  峰值=15956  事件数=2   ❌
#         VoiceMeeter  : RMS=0.3  峰值=1      事件数=0   ✅ (三种驱动全干净)
#
# ⚠️⚠️ 设备号会变! 用户改动默认音频设备后,sounddevice 的索引会整体重排
#      (实测 [15] 从 VoiceMeeter Output 变成了 Steam Streaming Microphone)。
#      因此这里**按名称自动查找**,不要写死索引。
# ---------------------------------------------------------------------------
CABLE_NAME = "VoiceMeeter"

# 名称匹配关键词(播放端 / 录音端)
CABLE_PLAY_KEY = "voicemeeter input"
CABLE_REC_KEY = "voicemeeter output"

# 驱动优先级:
#   播放 —— WASAPI 延迟最低
#   录音 —— DirectSound 更宽容(实测 WASAPI 录音端探测能过、实开却报
#           Invalid sample rate;DirectSound 支持 48k/44.1k/32k 多档)
_CABLE_API_PREFER_PLAY = ("Windows WASAPI", "Windows DirectSound", "MME")
_CABLE_API_PREFER_REC = ("Windows DirectSound", "Windows WASAPI", "MME")

# 旧的手工索引(仅作参考,实际由 _find_cable_devices() 决定)
CABLE_PLAY_DEVICE = None
CABLE_REC_DEVICE = None
CABLE_RATE = 48000
# 诊断:播放时同步采集线缆,存成 *_cable.wav
#
# ⚠️ 它需要**打开与微信相同的录音端点**。两个程序同时占用一个
#    DirectSound 录音端点是可疑的 —— 怀疑会干扰微信取音,
#    表现为"发出空语音条"。
#
#    因此**默认关闭**。排查问题时再改 True(会生成 *_cable.wav,
#    能直接看到线缆上到底有没有声音)。
DIAG_RECORD = False

# 发送前的音频校验/处理参数
# 实测:线缆底噪峰值=1,正常语音峰值 20000+,
#       所以峰值 < 300 基本可断定是静音(会发出空语音条)。
CABLE_SILENT_PEAK = 300     # 低于此峰值判为"静音",拒发
CABLE_TARGET_PEAK = 20000   # 归一化目标(提升过轻的音频,避免听着发闷)


class SilentAudio(Exception):
    """TTS 返回的音频为空/极弱 —— 不应该发出去。"""


_cable_cache = {}


def _pick_by_api(cands, prefer):
    """从候选中按驱动优先级挑一个。cands = [(idx, api, channels)]"""
    for want in prefer:
        for idx, api, ch in cands:
            if want.lower() in api.lower():
                return idx
    return cands[0][0] if cands else None


def _find_cable_devices(rate=CABLE_RATE, refresh=False):
    """按名称查找 VoiceMeeter 播放端/录音端,并验证采样率可用。

    只返回**真正能打开**的设备(实测:探测能过不代表实开能过)。
    返回 (play_idx, rec_idx, info_dict)。找不到则对应项为 None。
    """
    import sounddevice as sd
    if _cable_cache and not refresh:
        return (_cable_cache.get("play"), _cable_cache.get("rec"),
                _cable_cache.get("info", {}))

    plays, recs = [], []
    for i, d in enumerate(sd.query_devices()):
        n = d["name"].lower()
        api = sd.query_hostapis(d["hostapi"])["name"]
        if CABLE_PLAY_KEY in n and d["max_output_channels"] > 0:
            plays.append((i, api, d["max_output_channels"]))
        if CABLE_REC_KEY in n and d["max_input_channels"] > 0:
            recs.append((i, api, d["max_input_channels"]))

    def open_ok(idx, r, want_input):
        try:
            if want_input:
                s = sd.InputStream(device=idx, samplerate=r, channels=2, dtype="int16")
            else:
                s = sd.OutputStream(device=idx, samplerate=r, channels=2, dtype="int16")
            s.close()
            return True
        except Exception:
            return False

    def choose(cands, want_input, prefer):
        ordered = []
        for want in prefer:
            ordered += [c for c in cands if want.lower() in c[1].lower()]
        ordered += [c for c in cands if c not in ordered]
        for idx, api, ch in ordered:
            for r in (CABLE_RATE, 44100, 48000, 32000):
                if open_ok(idx, r, want_input):
                    return idx, api, r
        return None, None, None

    play, papi, prate = choose(plays, False, _CABLE_API_PREFER_PLAY)
    rec, rapi, rrate = choose(recs, True, _CABLE_API_PREFER_REC)
    # 统一采样率:优先播放端的,但要确保录音端也能开
    rate_use = prate or rrate or CABLE_RATE
    if rec is not None and not open_ok(rec, rate_use, True):
        for r in (44100, 48000, 32000):
            if open_ok(rec, r, True):
                rate_use = r
                break

    info = {"play": play, "play_api": papi, "play_rate": prate,
            "rec": rec, "rec_api": rapi, "rec_rate": rrate,
            "rate": rate_use}
    _cable_cache.update({"play": play, "rec": rec, "info": info, "rate": rate_use})
    return play, rec, info


def cable_devices(refresh=False):
    """对外接口:返回 (play_idx, rec_idx)。会自动查找并缓存。"""
    p, r, info = _find_cable_devices(refresh=refresh)
    return p, r

# 界面坐标(窗口内比例,0~1;相对整个窗口,非渲染区)
RATIO_SESSION_X = 0.117     # 会话列表行的 x(实测点 (303, y) 能命中)
RATIO_SESSION_Y0 = 0.135    # 第 0 行(文件传输助手)中心 y —— 实测 208/1540
RATIO_SESSION_DY = 0.0435   # 行间距 —— 实测 67/1540 ≈ 0.0435

# ---------------------------------------------------------------------------
# 语音模式按钮坐标 —— 实测标定(不要再用比例推算!)
#
# ⚠️ 教训:微信输入区**不是等比缩放**。窗口从 1564 宽变成 2584 宽后,
#          按比例算出的位置偏移了 900+ 像素。
#          (曾经还用过"距右/下边缘偏移"的算法,同样会因为基准窗口不同而
#           整体偏移 —— 实测偏差 12 像素就足以点空。已彻底废弃。)
#
# 现在的做法(见 anchor_pos):
#   ① 窗口尺寸与 MEASURED_WINDOW 相差 ≤4px -> **直接用实测绝对坐标**;
#   ② 否则按窗口内**相对位置等比换算**。
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 按钮坐标
#
# ⚠️ 教训:之前用"距右/下边缘偏移"推算,但基准窗口尺寸一旦不同就会整体偏移。
#    实测偏差 12 像素就足以点空(按钮很小)。
#    所以这里改为:
#      · 以**用户实测的绝对坐标**为基准(最可靠);
#      · 记录测量时的窗口尺寸;
#      · 若实际窗口尺寸不同,按比例换算。
# ---------------------------------------------------------------------------
BUTTONS_MEASURED = {
    # 用户用「采集按钮坐标.py」实测(窗口最大化时)
    "voice_btn": (2347, 1450),   # 语音条按钮(点它开始录音)
    "send_btn": (2483, 1443),    # 录音中的 ⬆ 发送
    "cancel_btn": (2129, 1444),  # 录音中的 ✕ 取消
}
# 测量时的窗口(原点与尺寸)。用户测量时窗口最大化。
MEASURED_WINDOW = {"x": -12, "y": -12, "w": 2584, "h": 1540}

# 坐标覆盖文件(便于不改代码就换坐标)
COORD_FILE = os.path.join(_HERE, "voice_button_coords.json")


def _load_coords():
    """读用户覆盖坐标(若有)。"""
    if not os.path.isfile(COORD_FILE):
        return {}, None
    try:
        with open(COORD_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        pts = {k: tuple(v) for k, v in (d.get("buttons") or {}).items()
               if isinstance(v, (list, tuple)) and len(v) == 2}
        win = d.get("window")
        return pts, win
    except Exception:
        return {}, None


_USER_PTS, _USER_WIN = _load_coords()
if _USER_PTS:
    BUTTONS_MEASURED.update(_USER_PTS)
    if _USER_WIN:
        MEASURED_WINDOW = _USER_WIN

# 兼容旧名
ANCHOR = BUTTONS_MEASURED

# 录制状态判据:录音时工具栏会出现绿色声波条
REC_GREEN_MIN = 800         # 绿色像素数超过此值视为"正在录制"
TOOLBAR_Y_RATIO = 0.88      # 工具栏区起始 y 比例

# 时序(秒)
T_RECORD_SETTLE = 3.0       # ★ 轮询"是否已进入录音态"的**预算上限**
                            #   ⚠️ 不能调小!实测微信录音工具栏变绿有
                            #   **1.6~2.2 秒延迟**。预算不足会误判成"没进入录音",
                            #   进而补点语音条按钮 —— 而补点会把刚开始的录音
                            #   **直接取消掉**(实测:补点后 0.55 秒录音态消失)。
                            #   检测快时不会等满,所以留大没有代价。
T_REC_POLL = 0.12           # 轮询间隔
T_REC_CONFIRM = 0.8         # (保留)补点前的确认观察时长
REC_VERIFY_WINDOW = 2.5     # 播放后确认录音态的观察窗口
                            # 绿色工具栏有 1~2 秒延迟,单次采样会误判
T_REC_SETTLE_AFTER = 0.7    # 确认进入录音态后,再等这么久才播放
                            # 实测:立刻播放会让开头一段丢失
                            # ⚠️ 这一条**不能随便调小** —— 微信的录音管线
                            #    需要时间真正开始取音,提前播放会吃掉字头。
                            #    (想缩短语音条时长,应该从别处省,不要动这里)
T_TAIL = 0.5                # 播完后留的尾巴(避免截断;太长会变成尾部静音)
T_TAIL_COMPACT = 0.35       # 紧凑模式下的尾巴
T_AFTER_SEND = 2.0

# ---------------------------------------------------------------------------
# ★ 兜底看门狗:到点无条件点一次发送键
#
# 为什么需要:
#     微信的语音录音上限是 60 秒。如果因为任何原因(异常、卡住、点击被吞、
#     判定出错……)我们没点成发送键,录音会一直走到 60 秒 —— 那条语音就废了。
#     所以这里设一个**不依赖任何状态检测**的兜底:从开始录音算起,
#     到 REC_WATCHDOG_AT 秒时,只要还没成功发送过,就无条件点一次发送键。
#
# 两个刻意的设计:
#   · 与主流程的点击共用 _CLICK_LOCK 互斥 —— 保证两次点击不会撞在一起
#     (也就是"与检测/点击的时间错开")。
#   · 等待用 0.37 秒这种不整齐的间隔轮询,避免刚好和主流程的检测节奏同拍。
#
# 注:"无条件"指的是**不依赖录音状态检测**;仍然保留"坐标必须属于微信"
#     这道校验 —— 否则可能点到别的程序上,那是更危险的事。
# ---------------------------------------------------------------------------
REC_LIMIT_S = 60.0          # 微信语音条时长上限
REC_WATCHDOG_AT = 58.0      # 兜底点发送的时刻(略早于上限,确保录音仍然有效)

_CLICK_LOCK = threading.Lock()      # 主流程点击 与 兜底点击 互斥
_WD = {"stop": None, "thread": None}
_WD_LOCK = threading.Lock()


def defuse_send_watchdog(reason="", log=None):
    """撤销兜底看门狗(成功发送 / 取消录音 / 开始新的发送时调用)。"""
    with _WD_LOCK:
        ev = _WD.get("stop")
        _WD["stop"] = None
        _WD["thread"] = None
    if ev is not None:
        ev.set()
        if log:
            log("       [兜底] 已撤销(%s)" % (reason or "结束"))
        return True
    return False


def arm_send_watchdog(hwnd, sx, sy, t_start, log, res):
    """武装兜底:到 t_start+REC_WATCHDOG_AT 秒时无条件点发送键。"""
    defuse_send_watchdog("有新的发送开始", log)
    stop = threading.Event()
    # ⚠️ 必须自己取一次 sender:ws 是 send_voice_by_row 的**局部**变量,
    #    模块级函数里引用不到 —— 否则看门狗一触发就 NameError。
    _ws = _import_sender()

    def _run():
        # 不整齐的轮询间隔,避免与主流程检测同拍
        while not stop.is_set():
            left = (t_start + REC_WATCHDOG_AT) - time.time()
            if left <= 0:
                break
            stop.wait(min(left, 0.37))
        if stop.is_set():
            return
        elapsed = time.time() - t_start
        log("")
        log("   >>> [兜底] 距开始录音已 %.1fs(上限 %.0fs),无条件点击发送键 <<<"
            % (elapsed, REC_LIMIT_S))
        log("       (正常流程未成功发送;这与录音状态检测无关,是最后一道保险)")
        with _CLICK_LOCK:                    # ★ 与主流程点击互斥,防按键冲突
            try:
                _ws.bring_to_front(hwnd)
            except Exception:
                pass
            ok = _ws.click(sx, sy, expect_hwnd=hwnd)
        log("       [兜底] 点击返回: %s" % ok)
        res["watchdog_fired"] = True
        time.sleep(1.2)
        try:
            left_rec, grew = is_recording(hwnd)
            log("       [兜底] 之后录音态: %s (绿色像素=%d)"
                % ("仍在录音" if left_rec else "已结束(发送成功)", grew))
            if left_rec:
                log("       [兜底] ⚠ 仍在录音 —— 请检查微信是否被遮挡/未最大化")
        except Exception as e:
            log("       [兜底] 复查录音态失败: %s" % str(e)[:60])

    t = threading.Thread(target=_run, daemon=True, name="send-watchdog")
    with _WD_LOCK:
        _WD["stop"] = stop
        _WD["thread"] = t
    t.start()
    log("       [兜底] 已武装:%.0fs 后若仍未成功发送,将无条件点发送键"
        % REC_WATCHDOG_AT)


# ---------------------------------------------------------------------------
# 诊断开关
#
# ★ 微信的录音是"从点语音条按钮开始,到点发送结束",中间本程序做的每一件
#   事(等待、截图、状态检测)都会被录进去,变成语音条**首尾的静音**。
#   所以默认走**紧凑模式**:不做诊断截图、用轮询代替固定等待。
#   排查问题时加 --debug(或把 DEBUG_DIAG 改 True)恢复完整诊断。
# ---------------------------------------------------------------------------
DEBUG_DIAG = False


def _is_debug(debug=None):
    return DEBUG_DIAG if debug is None else bool(debug)



# ---------------------------------------------------------------------------
# 窗口与输入
# ---------------------------------------------------------------------------

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
# ★ 必须声明原型 —— 否则句柄 >= 2**31 时会抛
#   "ArgumentError: argument 1: OverflowError: int too long to convert"
#   (windll.user32/gdi32 是进程内共享对象,vendor_py 的 uiautomation
#    给 GetWindowDC/CreateCompatibleDC/SelectObject 设过 restype=c_void_p)
#   详见 win32_proto.py 顶部说明。
import win32_proto
win32_proto.apply(_user32, _gdi32)
_user32.SetProcessDPIAware()


def _import_sender():
    import wx_sender
    return wx_sender


def find_window():
    ws = _import_sender()
    return ws.find_main_window()


def _win_rect(hwnd):
    r = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _abs(hwnd, fx, fy):
    x, y, w, h = _win_rect(hwnd)
    return (x + fx * w, y + fy * h)


def session_row_pos(hwnd, index):
    """会话列表第 index 行(0 起)的屏幕坐标。"""
    return _abs(hwnd, RATIO_SESSION_X,
                RATIO_SESSION_Y0 + index * RATIO_SESSION_DY)


def _snap(hwnd, path):
    img, _ = _grab_image(hwnd)
    img.save(path)
    return path


def _grab_image(hwnd):
    """PrintWindow 截图,返回 (PIL.Image, (x,y,w,h))。"""
    from PIL import Image
    x, y, w, h = _win_rect(hwnd)
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


def anchor_pos(hwnd, key, verbose=False):
    """算按钮的屏幕绝对坐标。

    策略(按可靠性排序):
      1. 若当前窗口尺寸与**测量时**一致 -> 直接用实测绝对坐标(最准);
      2. 否则按窗口尺寸比例换算(保持按钮在窗口中的相对位置);
      3. 再退一步:按"距右/下边缘偏移"换算。

    早期版本只用方案 3,结果基准窗口不同就整体偏移 12 像素 -> 点空。
    """
    mx, my = BUTTONS_MEASURED[key]
    x, y, w, h = _win_rect(hwnd)
    mw = MEASURED_WINDOW.get("w") or 2584
    mh = MEASURED_WINDOW.get("h") or 1540
    mx0 = MEASURED_WINDOW.get("x", 0)
    my0 = MEASURED_WINDOW.get("y", 0)

    # 方案 1:尺寸一致,直接用实测值
    if abs(w - mw) <= 4 and abs(h - mh) <= 4:
        if verbose:
            print("       [坐标] 用实测值 %s" % ((mx, my),))
        return mx, my

    # 方案 2:窗口内相对位置等比换算
    fx = (mx - mx0) / float(mw)
    fy = (my - my0) / float(mh)
    px = x + int(round(fx * w))
    py = y + int(round(fy * h))
    if verbose:
        print("       [坐标] 窗口 %dx%d != 测量时 %dx%d -> 等比换算 (%d,%d)"
              % (w, h, mw, mh, px, py))
    return px, py


def is_recording(hwnd, verbose=False):
    """检测微信是否处于"正在录制语音"状态(工具栏出现绿色声波条)。

    Returns: (是否在录制, 绿色像素数)
    """
    import numpy as np
    img, (_, _, w, h) = _grab_image(hwnd)
    a = np.array(img)
    H, W, _ = a.shape
    m = np.zeros((H, W), bool)
    for (r, g, b) in ((7, 193, 96), (0, 195, 117), (21, 172, 112), (6, 193, 95)):
        m |= (np.abs(a[:, :, 0].astype(int) - r) < 30) & \
             (np.abs(a[:, :, 1].astype(int) - g) < 30) & \
             (np.abs(a[:, :, 2].astype(int) - b) < 30)
    band = m[int(H * TOOLBAR_Y_RATIO):, :]
    n = int(band.sum())
    if verbose:
        print("       [录制检测] 工具栏绿色像素 = %d (阈值 %d)" % (n, REC_GREEN_MIN))
    return n >= REC_GREEN_MIN, n


def wait_recording(hwnd, timeout=None, interval=T_REC_POLL, verbose=False):
    """轮询等待"进入录音态",一检测到就立刻返回。

    为什么不用固定 sleep:
        固定 T_RECORD_SETTLE=0.8 秒意味着**每次都要等满 0.8 秒**,
        而这段时间微信在录音 —— 直接变成语音条开头的静音。
        实测通常 0.2~0.3 秒就已经进入录音态了。

    Returns: (是否进入录音, 绿色像素数, 实际等待秒数)
    """
    if timeout is None:
        timeout = T_RECORD_SETTLE
    t0 = time.time()
    rec, g = is_recording(hwnd, verbose=False)
    while not rec and (time.time() - t0) < timeout:
        time.sleep(interval)
        rec, g = is_recording(hwnd, verbose=False)
    if verbose:
        print("       [录制检测] 绿色像素 = %d,用时 %.2fs" % (g, time.time() - t0))
    return rec, g, time.time() - t0


# ---------------------------------------------------------------------------
# 音频
# ---------------------------------------------------------------------------

def _resample(x, src, dst):
    import numpy as np
    if src == dst:
        return x
    n = int(round(len(x) * dst / float(src)))
    idx = np.linspace(0, len(x) - 1, n)
    lo = np.floor(idx).astype(np.int64)
    hi = np.minimum(lo + 1, len(x) - 1)
    fr = idx - lo
    return (x[lo] * (1 - fr) + x[hi] * fr).astype(np.int16)


def load_for_cable(wav_path, trim_silence=True, verbose=False):
    """读 WAV -> 校验 -> 修剪首尾静音 -> 转成线缆需要的 48k 立体声。

    返回 (stereo, 时长秒, 信息dict);音频为空则抛 SilentAudio 异常。

    为什么需要校验:
        实测 TTS 偶发返回静音/极弱音频(线缆录音峰值=1),
        旧代码照单全收 -> 发出**空语音条**。
        这里先判空,宁可本次不发,也不发空语音。
    """
    import numpy as np
    with wave.open(wav_path, "rb") as w:
        rate, ch = w.getframerate(), w.getnchannels()
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if raw.size == 0:
        raise SilentAudio("TTS 返回空音频(0 采样)")
    if ch > 1:
        raw = raw.reshape(-1, ch).mean(axis=1).astype(np.int16)

    peak = int(np.max(np.abs(raw)))
    rms = float(np.sqrt(np.mean(raw.astype(np.float64) ** 2)))

    if peak < CABLE_SILENT_PEAK:
        raise SilentAudio(
            "TTS 音频几乎无声(峰值 %d, RMS %.1f)—— 不发空语音" % (peak, rms))

    # 修剪首尾静音:去掉开头那段空白(实测会占 1 秒以上)
    trimmed = 0.0
    if trim_silence and peak > 0:
        thr = max(peak * 0.04, 60)
        loud = np.abs(raw) > thr
        idx = np.where(loud)[0]
        if len(idx):
            # 留 0.05 秒余量,避免削掉字头
            pad = int(rate * 0.05)
            a0 = max(0, idx[0] - pad)
            a1 = min(len(raw), idx[-1] + pad)
            trimmed = a0 / float(rate)
            raw = raw[a0:a1]
        else:
            raise SilentAudio("音频无有效语音段")

    if raw.size == 0:
        raise SilentAudio("修剪后无音频")

    a = _resample(raw.astype(np.float64), rate, CABLE_RATE)

    # 音量归一化:提升过轻的音频,避免微信端听起来发闷
    p2 = float(np.max(np.abs(a))) if a.size else 0.0
    if p2 > 0 and p2 < CABLE_TARGET_PEAK * 0.7:
        gain = min(CABLE_TARGET_PEAK / p2, 4.0)
        a = a * gain
        if verbose:
            print("       [音频] 增益 x%.2f (原峰值 %.0f)" % (gain, p2))
    a = np.clip(a, -32768, 32767).astype(np.int16)

    info = {"peak_in": peak, "rms_in": rms, "trimmed_head": trimmed,
            "peak_out": int(np.max(np.abs(a))) if a.size else 0}
    if verbose:
        print("       [音频] 原峰值 %d RMS %.0f -> 修剪掉开头 %.2fs, 输出峰值 %d" % (
            peak, rms, trimmed, info["peak_out"]))
    return np.repeat(a.reshape(-1, 1), 2, axis=1), len(a) / float(CABLE_RATE), info


def open_cable_stream(verbose=False):
    """提前打开线缆播放流。

    ★ 为什么要提前开:
        WASAPI 打开 VoiceMeeter 播放端要 **约 0.7 秒**。如果等到该播放时
        才开,这 0.7 秒微信已经在录音了 —— 直接变成语音条开头的静音。
        在**点录音按钮之前**开好,播放时就没有这段延迟。

    返回流对象(失败返回 None,调用方回退到普通播放)。
    """
    import sounddevice as sd
    try:
        play_idx, _rec_idx, info = _find_cable_devices()
        rate = info.get("rate") or CABLE_RATE
        if play_idx is None:
            return None
        s = sd.OutputStream(device=play_idx, samplerate=rate,
                            channels=2, dtype="int16")
        s.start()
        if verbose:
            print("       [音频] 已预开线缆播放流(设备 %s @ %dHz)" % (play_idx, rate))
        return s
    except Exception as e:
        if verbose:
            print("       [音频] 预开播放流失败(%s),回退普通播放" % str(e)[:50])
        return None


def close_cable_stream(stream):
    if stream is None:
        return
    try:
        stream.stop()
    except Exception:
        pass
    try:
        stream.close()
    except Exception:
        pass


def play_to_cable(stereo, record_to=None, stream=None):
    """把音频播到虚拟线缆的播放端。

    record_to: 若给出路径,则**同时从录音端采集**,把线缆上
               实际流过的信号存成 WAV —— 用于诊断"微信到底录到了什么"。
               ⚠️ 它会占用微信正在用的录音端点,实测会让录音中断,
                  所以只有 debug 排查时才用。
    stream:    预先打开的播放流(见 open_cable_stream)。给了就直接写,
               省掉 ~0.7 秒的设备打开时间。
    """
    import sounddevice as sd
    import numpy as np
    import wave as _wave

    play_idx, rec_idx, info = _find_cable_devices()
    rate = info.get("rate") or CABLE_RATE
    if play_idx is None:
        raise RuntimeError("找不到 VoiceMeeter 播放端设备")

    if not record_to or rec_idx is None:
        if stream is not None:
            stream.write(stereo)
            return None
        with sd.OutputStream(device=play_idx, samplerate=rate,
                             channels=2, dtype="int16") as o:
            o.write(stereo)
        return None

    buf = []

    def cb(indata, frames, t, status):
        buf.append(indata.copy())

    with sd.InputStream(device=rec_idx, samplerate=rate,
                        channels=2, dtype="int16", callback=cb):
        time.sleep(1.2)          # 等录音流真正就绪(实测短延时会漏信号)
        with sd.OutputStream(device=play_idx, samplerate=rate,
                             channels=2, dtype="int16") as o:
            o.write(stereo)
        time.sleep(0.8)

    if not buf:
        return None
    data = np.concatenate(buf).astype(np.int16)
    with _wave.open(record_to, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(data.tobytes())
    rms = float(np.sqrt(np.mean(data.astype(np.float64) ** 2)))
    peak = int(np.max(np.abs(data)))
    return {"path": record_to, "rms": rms, "peak": peak,
            "duration": len(data) / float(rate),
            "play_dev": play_idx, "rec_dev": rec_idx, "rate": rate}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _safe_cancel_recording(hwnd, ws, log=None):
    """尽最大努力把微信里正在进行的录音取消掉(出错也不抛)。"""
    # 既然要取消录音,兜底看门狗也必须撤销,否则它稍后还会去点发送
    try:
        defuse_send_watchdog("已取消录音", log)
    except Exception:
        pass
    try:
        cx, cy = anchor_pos(hwnd, "cancel_btn")
        ws.click(cx, cy, expect_hwnd=hwnd)
        if log:
            log("       (已尝试点取消键 %d,%d)" % (cx, cy))
    except Exception as e:
        if log:
            log("       (取消录音失败,可手动点微信的 ✕: %s)" % str(e)[:50])


def send_voice_by_row(text, row_index, out_dir=None, keep_wav=False,
                      verbose=True, snapshot_prefix=None, expect_name=None,
                      wait_tts_ready=True, tts_wait=180,
                      diag_record=None, rec_settle=None, debug=None):
    """向会话列表第 row_index 行(0 起)发送一条语音条。

    Args:
        text: 要朗读的文本(交给 GPT-SoVITS 合成)
        row_index: 会话列表行号(0 起)
        expect_name: 期望的会话名(如"测试号B")。提供时会用它做
                     OCR 标题校验,避免因行号漂移而发错人。
        out_dir: 临时文件目录
        wait_tts_ready: 合成前先等 TTS **真正就绪**(端口+模型都可用)。
                        设为 False 可跳过(不推荐)。
        diag_record: 是否在播放时同步采集线缆(诊断用)。
                     ⚠️ 它会和微信**同时打开同一个录音端点**,
                     实测怀疑会干扰微信取音 -> 发出空语音条。
                     不确定时设为 False 更保险;默认取 DIAG_RECORD。
        rec_settle: 点录音按钮后、开始播放前等待的秒数。
                    微信需要时间真正启动录音;太短会漏掉开头。
        debug: True = 完整诊断模式(每步存截图、播放时同步采集线缆)。
               默认取 DEBUG_DIAG(False),即**紧凑模式** ——
               微信会把本程序等待/截图的时间也录成首尾静音,
               紧凑模式能显著缩短语音条时长。
        keep_wav: 是否保留合成的 WAV
        verbose: 打印过程

    Returns:
        dict: {"ok": bool, "wav": path, "duration": float, "error": str|None,
               "session": 会话判定结果, "step": 失败时卡在哪一步}
    """
    ws = _import_sender()
    import gptsovits_tts as tts

    # ★ 先撤销上一次遗留的兜底看门狗 —— 否则它可能在下一条语音录音期间
    #   突然点一下发送键,把新录音提前发出去。
    defuse_send_watchdog("新的发送开始", None)

    out_dir = out_dir or _HERE
    res = {"ok": False, "wav": None, "duration": None, "error": None,
           "step": "开始"}
    _t0 = time.time()
    _dbg = _is_debug(debug)
    res["debug"] = _dbg

    def log(*a):
        if verbose:
            print(*a)

    def mark(step, note=""):
        """记录当前步骤 —— 失败时能一眼看出卡在哪一步。"""
        res["step"] = step
        res["step_elapsed"] = round(time.time() - _t0, 1)
        if note:
            res["step_note"] = note
        if verbose:
            print("       [计时] %-16s 累计 %.2fs" % (step, time.time() - _t0))
        return step

    # 0. 前置检查:微信窗口必须存在、而且能拉到最前面。
    #
    # ★ 用户实测坑:微信被别的窗口(浏览器/控制面板/任何程序)盖住时,
    #   本模块的点击是"屏幕绝对坐标",会**点到盖在上面的那个程序**上。
    #   表现正是用户看到的:
    #       · 语音"录"了,但发送键**压根没被点到**
    #       · 鼠标一直停在语音条按钮上,位置不变
    #   所以开跑前先确认微信能被拉到前台,不能就立刻报错退出,
    #   绝不带着"会被挡住"的状态去点。
    mark("预检-微信窗口")
    _hwnd0 = find_window()
    if not _hwnd0:
        res["error"] = "未找到微信窗口(请确认微信已登录且没有最小化)"
        log("   ✗ " + res["error"])
        return res
    if not ws.bring_to_front(_hwnd0):
        log("       ⚠ 微信未能成为前台窗口,尝试继续(点击时会再做归属校验)")
    else:
        log("       微信已置于前台 ✓")
    if not ws.point_belongs_to(_hwnd0, *anchor_pos(_hwnd0, "voice_btn")):
        who = ws.window_at(*anchor_pos(_hwnd0, "voice_btn"))
        res["error"] = ("微信窗口被 %r 遮挡,语音条按钮位置不属于微信。"
                        "已中止以免误点别的程序 —— 请把微信最大化并点一下微信窗口再试。"
                        % (ws.window_title(who)[:28] or who))
        log("   ✗ " + res["error"])
        return res

    # 1. 合成
    mark("1-合成")
    wav = os.path.join(out_dir, "_voice_send_%d.wav" % int(time.time()))
    log("[1/5] 合成语音 …")

    # 先确认 TTS **真正就绪**再合成。
    # 实测坑:api_v2.py 端口先通、模型后加载完;此时合成会失败,
    # 表现就是"没有播放音频、没有点发送"(因为流程在第 1 步就退出了)。
    if wait_tts_ready:
        try:
            if not tts.is_server_alive():
                log("       TTS 未响应,等待启动(最多 %.0f 秒)…" % tts_wait)
            ready, rinfo = tts.wait_until_ready(timeout=tts_wait, verbose=False)
            if not ready:
                res["error"] = ("TTS 服务未就绪:%s\n"
                                "       请先启动 api_v2.py(控制面板 ⑥ 或 启动TTS服务.cmd),\n"
                                "       等出现 'Uvicorn running on http://127.0.0.1:9880' 再操作。"
                                % rinfo)
                log("       ✗ " + res["error"].split("\n")[0])
                return res
        except Exception as e:
            log("       (就绪检查跳过: %s)" % str(e)[:60])

    try:
        tts.synthesize(text, wav)
    except Exception as e:
        res["error"] = "合成失败: %s" % e
        log("   ", res["error"])
        return res
    try:
        stereo, dur, ainfo = load_for_cable(wav, verbose=verbose)
    except SilentAudio as e:
        res["error"] = "音频为空,已中止(不发空语音): %s" % e
        log("       ✗", res["error"])
        log("       排查:1) 参考音频是否正常  2) TTS 服务是否刚重启未加载完")
        log("             3) 文本是否过短/全是符号")
        return res
    except Exception as e:
        res["error"] = "音频处理失败: %s" % e
        log("   ", res["error"])
        return res
    res["wav"] = wav
    res["duration"] = dur
    res["audio"] = ainfo
    mark("1-合成完成", "时长%.2fs" % dur)
    log("      时长 %.2fs(已修剪开头 %.2fs)" % (dur, ainfo.get("trimmed_head", 0)))

    hwnd = find_window()
    if not hwnd:
        res["error"] = "未找到微信窗口"
        log("   ", res["error"])
        return res

    # 2. 确保目标会话已打开(防 toggle 关闭 + 防发错人)
    mark("2-打开会话")
    if expect_name:
        log("[2/5] 确保会话「%s」处于打开状态 …" % expect_name)
    else:
        log("[2/5] 确保第 %d 行会话已打开 …" % row_index)
    ws.bring_to_front(hwnd)
    time.sleep(0.5)
    try:
        import session_guard
        g = session_guard.ensure_chat_open(
            hwnd, row_index, expect_name or "",
            verbose=verbose, click_fn=lambda cx, cy: ws.click(cx, cy, expect_hwnd=hwnd),
            strict=True)
        if not g.get("ok"):
            res["error"] = ("会话状态无法确认,已中止发送(避免发错人): %s"
                            % g.get("note"))
            res["session"] = g
            log("       ✗ " + res["error"])
            log("       排查建议:")
            log("         1) 把目标会话在微信里【置顶】,并重新核对 row 行号")
            log("         2) 确认微信窗口是【最大化】的")
            log("         3) 用 python voice_bot.py --list 查看当前真实行号")
            return res
        res["session"] = g
        log("       结果: %s(判定依据 %s)" % (g.get("action"), g.get("by")))
    except Exception as e:
        # session_guard 不可用时,退回原来的直接点击
        log("       [警告] 会话检测不可用(%s),退回直接点击" % str(e)[:60])
        import traceback
        for _ln in traceback.format_exc().rstrip().split("\n")[-6:]:
            log("         %s" % _ln)
        x, y = session_row_pos(hwnd, row_index)
        ws.click(x, y, expect_hwnd=hwnd)
        time.sleep(1.2)
    if snapshot_prefix:
        _snap(hwnd, "%s_opened.png" % snapshot_prefix)

    # 3. 点语音条按钮(开始录音)
    mark("3-开始录音")
    log("[3/5] 点击语音条按钮 -> 录音")
    vx, vy = anchor_pos(hwnd, "voice_btn")
    log("       坐标 (%d,%d)" % (vx, vy))

    # 3z. ★ 在点录音按钮**之前**就把线缆播放流开好。
    #     WASAPI 打开 VoiceMeeter 播放端要 ~0.7 秒;若等到该播放时才开,
    #     这 0.7 秒微信已经在录音 -> 变成语音条开头的静音。
    _diag = DIAG_RECORD if diag_record is None else bool(diag_record)
    _pre_stream = None if _diag else open_cable_stream(verbose=verbose)

    # 3a. 关键前置检查:微信必须在最前面,且按钮坐标必须真的属于微信。
    #     ★ 用户实测坑:微信被别的窗口(浏览器/控制面板)盖住时,
    #       盲点绝对坐标会把点击送到**盖在上面的那个程序**上,
    #       表现就是"录了音但发送键压根没被点到 / 鼠标停着不动"。
    if not ws.point_belongs_to(hwnd, vx, vy):
        who = ws.window_at(vx, vy)
        log("       ⚠ 按钮坐标当前被别的窗口占用(%s %r),尝试把微信提到前台…"
            % (who, ws.window_title(who)[:28]))
        ws.bring_to_front(hwnd)
        time.sleep(0.4)
    if not ws.point_belongs_to(hwnd, vx, vy):
        who = ws.window_at(vx, vy)
        res["error"] = ("微信窗口没有在最前面,按钮坐标被 %r 挡住 —— 已中止,"
                        "避免点到别的程序。请把微信窗口点一下/最大化后重试。"
                        % (ws.window_title(who)[:28] or who))
        log("   ✗ " + res["error"])
        if snapshot_prefix:
            _snap(hwnd, "%s_fail_blocked.png" % snapshot_prefix)
        close_cable_stream(_pre_stream)
        return res

    t_rec_start = time.time()          # ★ 兜底计时起点 = 开始录音这一刻
    ok_click = ws.click(vx, vy, expect_hwnd=hwnd)
    if not ok_click:
        log("       ⚠ 点击未确认送达,再试一次")
        try:
            ws.bring_to_front(hwnd)
            time.sleep(0.3)
        except Exception:
            pass
        ws.click(vx, vy, expect_hwnd=hwnd)

    # 3b. 确认真的进入录音态(避免后面点了"发送"却发不出去)
    #
    # ⚠️⚠️ 这里是本项目最凶的坑,用户的"没有发送点击 / 没录上语音"就是它:
    #     微信的"录音工具栏变绿"有 **约 1.6 秒延迟**。
    #     如果轮询预算设短了(旧值 0.8 秒),就会误判成"没进入录音",
    #     然后**再点一次语音条按钮** —— 而这次补点会把刚刚开始的录音
    #     **直接取消掉**。实测:
    #         第1次 click -> 1.1 秒后 录音=True (其实已经录上了)
    #         第2次 click -> 0.55 秒后 录音=False(被自己点没了)
    #     于是表现就是:鼠标停在语音条按钮上不动、没有发送点击、
    #     微信里也没有录到语音。
    #
    # ★ 所以这里**不再"等绿色工具栏出现"再播放**:
    #     绿色出现要 1.0~2.3 秒(时快时慢),而这段时间录音已经在跑,
    #     等它就是白白往语音条开头塞静音。
    #   改成:先固定等 T_REC_SETTLE_AFTER 让录音管线就绪(实测够用),
    #         立刻播放;"到底有没有录上"放到播放之后判定(见 [4b]),
    #         失败时先取消 → 再安全重试一次。
    if rec_settle is None:
        rec_settle = T_REC_SETTLE_AFTER
    if rec_settle > 0:
        log("       等待录音管线就绪 %.2fs" % rec_settle)
        time.sleep(rec_settle)

    # 只瞄一眼做提示(仅 debug;每次采样要 0.25 秒,录音正在跑)
    if _dbg:
        _peek, _pg = is_recording(hwnd, verbose=verbose)
        if not _peek:
            log("       (绿色工具栏还没出现 —— 正常,它本身有 1~2 秒延迟)")

    # ★ 诊断截图:**只在 debug 模式**做(PrintWindow 会占 0.3~0.4 秒)
    if _dbg and snapshot_prefix:
        _snap(hwnd, "%s_recording_start.png" % snapshot_prefix)

    # 4. 播放(可选同步录音,便于诊断)
    mark("4-播放音频")
    log("[4/5] 播放到虚拟线缆 …")
    diag_path = None
    # ⚠️ 注意:诊断录音**不跟随 debug** ——
    #    play_to_cable 一旦要录音,就会打开 sd.InputStream(线缆录音端点),
    #    而那正是微信正在用的录音端点。实测后果:
    #        播放期间微信录音直接中断 -> "播放后录音已中断"
    #    所以它必须独立开关,而且默认关闭。真要抓线缆信号请显式传
    #    diag_record=True(会牺牲发送成功率,只用于排查"是不是没声音")。
    if _diag and snapshot_prefix:
        diag_path = "%s_cable.wav" % snapshot_prefix
    elif not _diag:
        log("       (已关闭诊断录音 —— 避免与微信抢录音设备)")
    try:
        info = play_to_cable(stereo, record_to=diag_path, stream=_pre_stream)
        if info:
            res["cable_probe"] = info
            log("       实采信号: RMS=%.0f 峰值=%d 时长=%.2fs" % (
                info["rms"], info["peak"], info["duration"]))
            if info["rms"] < 50:
                log("       ⚠ 线缆上信号极弱,微信可能录不到声音")
    except Exception as e:
        res["error"] = "播放失败: %s" % e
        log("   ✗ " + res["error"])
        if snapshot_prefix:
            _snap(hwnd, "%s_fail_play.png" % snapshot_prefix)
        # 播放失败要取消录音,避免发出空白语音
        try:
            cx, cy = anchor_pos(hwnd, "cancel_btn")
            ws.click(cx, cy, expect_hwnd=hwnd)
        except Exception:
            pass
        close_cable_stream(_pre_stream)
        return res
    close_cable_stream(_pre_stream)
    _pre_stream = None
    time.sleep(T_TAIL if _dbg else T_TAIL_COMPACT)

    # 4b. 播放后再确认"确实在录音"
    #
    # ⚠️ 不能用单次采样就判定失败 —— 绿色工具栏本身有 1~2 秒延迟,
    #    单次采样正是旧代码的误判来源。这里给它一段观察窗口。
    # ★ 这里必须包异常:历史上正是这一句(is_recording -> PrintWindow -> GDI)
    #   因句柄参数溢出而抛异常,异常冒出去导致"整轮中止、发送键没点到",
    #   而且微信会一直留在录音状态。现在出错就取消录音并作为本次失败返回。
    try:
        still, g2, _w = wait_recording(hwnd, timeout=REC_VERIFY_WINDOW,
                                      verbose=verbose)
    except Exception as e:
        import traceback
        res["error"] = "校验录音态时出错: %s: %s" % (type(e).__name__, e)
        log("   ✗ " + res["error"])
        for _ln in traceback.format_exc().rstrip().split("\n")[-4:]:
            log("       %s" % _ln)
        _safe_cancel_recording(hwnd, ws, log)
        return res

    if not still:
        # 播放完了却没录上 —— 最可能是语音条按钮那一下被吞了。
        # 此刻已确认**不在录音态**,所以补点不会误伤正在进行的录音,
        # 可以安全地重试一次(补点 + 重播)。
        log("       ⚠ 未确认到录音(绿色像素=%d),补点语音条按钮并重播一次" % g2)
        if snapshot_prefix:
            _snap(hwnd, "%s_retry_norecord.png" % snapshot_prefix)
        try:
            ws.bring_to_front(hwnd)
        except Exception:
            pass
        ws.click(vx, vy, expect_hwnd=hwnd)
        time.sleep(T_REC_SETTLE_AFTER)
        try:
            play_to_cable(stereo, record_to=None)
        except Exception as e:
            log("       ⚠ 重播失败: %s" % str(e)[:60])
        time.sleep(T_TAIL if _dbg else T_TAIL_COMPACT)
        try:
            still, g2, _w = wait_recording(hwnd, timeout=REC_VERIFY_WINDOW,
                                           verbose=verbose)
        except Exception as e:
            res["error"] = "重试后校验录音态出错: %s: %s" % (type(e).__name__, e)
            log("   ✗ " + res["error"])
            _safe_cancel_recording(hwnd, ws, log)
            return res
        res["retried"] = True

    if not still:
        res["error"] = ("播放后仍未录上(绿色像素=%d)—— 语音条按钮没点中或点击没送到微信。"
                        "请确认微信窗口在最前面且已最大化。" % g2)
        log("   ✗ " + res["error"])
        if snapshot_prefix:
            _snap(hwnd, "%s_fail_recbroken.png" % snapshot_prefix)
        # 取消,避免留下一条长录音
        try:
            cx, cy = anchor_pos(hwnd, "cancel_btn")
            ws.click(cx, cy, expect_hwnd=hwnd)
        except Exception:
            pass
        return res
    log("       ✅ 已确认在录音态(绿色像素=%d)" % g2)

    # ★ 已确认真的在录音 -> 武装兜底看门狗
    _sx, _sy = anchor_pos(hwnd, "send_btn")
    arm_send_watchdog(hwnd, _sx, _sy, t_rec_start, log, res)
    # 注意:这里**不再**截图。PrintWindow 要 0.3s 左右,而录音还在继续,
    #       这段等待会变成语音条**结尾的静音**。

    # 5. 发送
    #
    # ⚠️ 实测坑:SendInput 可能**静默失败**(前台锁定被拒/焦点被抢),
    #    表现就是"鼠标动了但没点下去"。这里如果不管,录音会一直持续到
    #    微信的 60 秒上限 —— 所以必须:
    #      ① 点击前确保微信在前台**且发送键坐标真的属于微信**;
    #      ② 检查 click() 的返回值;
    #      ③ 点完确认真的退出了录音态,失败则重试。
    log("[5/5] 点击发送")
    mark("5-点击发送")
    sx, sy = anchor_pos(hwnd, "send_btn")
    log("       坐标 (%d,%d)" % (sx, sy))

    sent = False
    for attempt in range(3):
        # 不再无脑抢前台:抢前台本身要 ~0.5s,而录音还在走,
        # 这段等待会变成语音条结尾的静音。click() 内部的归属校验
        # 会按需自动抢前台(不需要时立刻返回,几乎不耗时)。
        if not _user32.GetForegroundWindow() == hwnd:
            log("       (微信不在前台,点击校验会自动抢回前台)")

        # 归属校验:发送键坐标必须属于微信,否则这一下会点到别的程序
        if not ws.point_belongs_to(hwnd, sx, sy):
            who = ws.window_at(sx, sy)
            log("       ⚠ 第%d次:发送键坐标被 %r 挡住,重试提前台"
                % (attempt + 1, ws.window_title(who)[:26] or who))
            ws.bring_to_front(hwnd)
            time.sleep(0.3)
            continue

        with _CLICK_LOCK:                # ★ 与兜底点击互斥,防按键冲突
            ok_click = ws.click(sx, sy, expect_hwnd=hwnd)
        if not ok_click:
            log("       ⚠ 第%d次点击被阻止或未确认送达" % (attempt + 1))

        time.sleep(T_AFTER_SEND)
        left, g = is_recording(hwnd, verbose=verbose)
        if not left:
            log("       ✅ 已退出录音态,发送生效")
            sent = True
            defuse_send_watchdog("已成功发送", log)
            break
        log("       ⚠ 仍在录音(绿色像素=%d),第%d次重试" % (g, attempt + 2))

    if not sent:
        # 三次都没成功。★ 这里**不再取消录音**:保留录音,让兜底看门狗在
        # REC_WATCHDOG_AT 秒时再无条件试一次 —— 宁可发出一条尾部带静音的
        # 语音,也不要把这条回复整个丢掉。
        # (若想改回"失败就取消",把下面三行注释掉、恢复点取消键即可)
        res["error"] = ("点击发送失败(3 次)—— 发送键没点中或点击没送到微信。"
                        "已保留录音,等 %.0fs 兜底再试一次" % REC_WATCHDOG_AT)
        log("   ✗ " + res["error"])
        if snapshot_prefix:
            _snap(hwnd, "%s_fail_send.png" % snapshot_prefix)
        res["ok"] = False
        res["step"] = res.get("step") or "5-点击发送"
        return res

    if snapshot_prefix:
        _snap(hwnd, "%s_sent.png" % snapshot_prefix)

    if not keep_wav:
        try:
            os.remove(wav)
        except Exception:
            pass
    res["ok"] = True
    mark("完成", "全程 %.1fs" % (time.time() - _t0))
    log("完成 ✅")
    return res


def check_prerequisites(verbose=True, need_tts=True):
    """检查前提条件,返回 (ok, 报告列表)。

    need_tts=False 时跳过 TTS 服务检查 —— 用于 --list 这类
    只需要读取数据库、还没配 AI/TTS 的场景。
    """
    lines = []
    ok = True

    # 微信窗口
    hwnd = find_window()
    if hwnd:
        x, y, w, h = _win_rect(hwnd)
        lines.append("微信窗口: hwnd=%s %dx%d @(%d,%d)" % (hwnd, w, h, x, y))
        if w < 2000:
            lines.append("  ⚠ 窗口未最大化,坐标可能失准(当前宽 %d)" % w)
    else:
        lines.append("✗ 未找到微信窗口")
        ok = False

    # TTS
    if need_tts:
        try:
            import gptsovits_tts as tts
            alive = tts.is_server_alive()
            lines.append("GPT-SoVITS: %s" % ("在线" if alive else "✗ 离线,请启动 api_v2.py"))
            if not alive:
                ok = False
        except Exception as e:
            lines.append("✗ gptsovits_tts 导入失败: %s" % e)
            ok = False
    else:
        lines.append("GPT-SoVITS: (跳过检查)")

    # 虚拟线缆
    try:
        p, r, info = _find_cable_devices(refresh=True)
        if p is None:
            lines.append("✗ 找不到 %s 播放端(检查 VoiceMeeter 是否安装)" % CABLE_NAME)
            ok = False
        else:
            lines.append("虚拟线缆播放端 [%d] %s  (%s, %dHz)" % (
                p, CABLE_PLAY_KEY.title(), info.get("play_api"), info.get("rate")))
        if r is None:
            lines.append("✗ 找不到 %s 录音端" % CABLE_NAME)
            ok = False
        else:
            lines.append("虚拟线缆录音端 [%d] %s  (%s)" % (
                r, CABLE_REC_KEY.title(), info.get("rec_api")))
    except Exception as e:
        lines.append("✗ 虚拟线缆检测失败: %s" % e)
        ok = False

    if verbose:
        for l in lines:
            print(l)
    return ok, lines


if __name__ == "__main__":
    print("=" * 70)
    print("前提检查")
    print("=" * 70)
    good, _ = check_prerequisites()
    print()
    if len(sys.argv) > 1 and sys.argv[1] == "send":
        text = sys.argv[2] if len(sys.argv) > 2 else "这是一条测试语音。"
        row = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        print("发送到第 %d 行: %s" % (row, text))
        r = send_voice_by_row(text, row, snapshot_prefix="voice_send")
        print("结果:", r)
    else:
        print("用法: python wx_voice_sender.py send \"文本\" [行号]")
