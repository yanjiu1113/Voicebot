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
import os
import sys
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
# 教训:微信输入区**不是等比缩放**。窗口从 1564 宽变成 2584 宽后,
#       按比例算出的"麦克风"偏移了 900+ 像素。以下为 2584x1540
#       (物理像素,最大化)下用截图逐像素测量的结果。
#
# 更稳的参照系是【距窗口右/下边缘的偏移】(右侧按钮位置固定):
#     语音条按钮  距右 235, 距下 85
#     录音发送⬆   距右  86, 距下 76
#     录音取消✕   距右 440, 距下 76
# ---------------------------------------------------------------------------
ANCHOR = {
    "voice_btn": (223, 73),     # (距右, 距下) 语音条按钮 -> 实测 (2349,1455)
    "send_btn":  (86, 76),      # (距右, 距下) 录音中的 ⬆ 发送 -> 实测 (2486,1452)
    "cancel_btn": (440, 76),    # (距右, 距下) 录音中的 ✕ 取消 -> 实测 (2132,1452)
}

# 录制状态判据:录音时工具栏会出现绿色声波条
REC_GREEN_MIN = 800         # 绿色像素数超过此值视为"正在录制"
TOOLBAR_Y_RATIO = 0.88      # 工具栏区起始 y 比例

# 时序(秒)
T_RECORD_SETTLE = 0.8       # 点录音后等待录音真正启动(用于状态检测)
T_REC_SETTLE_AFTER = 1.2    # 确认进入录音态后,再等这么久才播放
                            # 实测:立刻播放会让开头一段丢失
T_TAIL = 1.0                # 播完后留的尾巴(避免截断)
T_AFTER_SEND = 2.0


# ---------------------------------------------------------------------------
# 窗口与输入
# ---------------------------------------------------------------------------

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
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


def anchor_pos(hwnd, key):
    """按【距右/下边缘偏移】算按钮绝对坐标(比比例更稳)。"""
    dr, db = ANCHOR[key]
    x, y, w, h = _win_rect(hwnd)
    return (x + w - dr, y + h - db)


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


def play_to_cable(stereo, record_to=None):
    """把音频播到虚拟线缆的播放端。

    record_to: 若给出路径,则**同时从录音端采集**,把线缆上
               实际流过的信号存成 WAV —— 用于诊断"微信到底录到了什么"。
    """
    import sounddevice as sd
    import numpy as np
    import wave as _wave

    play_idx, rec_idx, info = _find_cable_devices()
    rate = info.get("rate") or CABLE_RATE
    if play_idx is None:
        raise RuntimeError("找不到 VoiceMeeter 播放端设备")

    if not record_to or rec_idx is None:
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

def send_voice_by_row(text, row_index, out_dir=None, keep_wav=False,
                      verbose=True, snapshot_prefix=None, expect_name=None,
                      wait_tts_ready=True, tts_wait=180,
                      diag_record=None, rec_settle=None):
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
        keep_wav: 是否保留合成的 WAV
        verbose: 打印过程

    Returns:
        dict: {"ok": bool, "wav": path, "duration": float, "error": str|None,
               "session": 会话判定结果}
    """
    ws = _import_sender()
    import gptsovits_tts as tts

    out_dir = out_dir or _HERE
    res = {"ok": False, "wav": None, "duration": None, "error": None}

    def log(*a):
        if verbose:
            print(*a)

    # 1. 合成
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
    log("      时长 %.2fs(已修剪开头 %.2fs)" % (dur, ainfo.get("trimmed_head", 0)))

    hwnd = find_window()
    if not hwnd:
        res["error"] = "未找到微信窗口"
        log("   ", res["error"])
        return res

    # 2. 确保目标会话已打开(防 toggle 关闭 + 防发错人)
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
            verbose=verbose, click_fn=ws.click, strict=True)
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
        x, y = session_row_pos(hwnd, row_index)
        ws.click(x, y)
        time.sleep(1.2)
    if snapshot_prefix:
        _snap(hwnd, "%s_opened.png" % snapshot_prefix)

    # 3. 点语音条按钮(开始录音)
    log("[3/5] 点击语音条按钮 -> 录音")
    vx, vy = anchor_pos(hwnd, "voice_btn")
    log("       坐标 (%d,%d)" % (vx, vy))
    ok_click = ws.click(vx, vy)
    if not ok_click:
        log("       ⚠ 点击未确认送达,再试一次")
        try:
            ws.bring_to_front(hwnd)
            time.sleep(0.3)
        except Exception:
            pass
        ws.click(vx, vy)
    time.sleep(T_RECORD_SETTLE)

    # 3b. 确认真的进入录音态(避免后面点了"发送"却发不出去)
    rec, gcount = is_recording(hwnd, verbose=verbose)
    if not rec:
        log("       ⚠ 未检测到录音状态(绿色像素=%d),重试一次" % gcount)
        ws.click(vx, vy)
        time.sleep(T_RECORD_SETTLE + 0.4)
        rec, gcount = is_recording(hwnd, verbose=verbose)
    if not rec:
        res["error"] = "进入录音失败(绿色像素=%d,可能没点中语音条按钮)" % gcount
        log("   ", res["error"])
        # 尝试取消,避免残留在录音态
        try:
            cx, cy = anchor_pos(hwnd, "cancel_btn")
            ws.click(cx, cy)
        except Exception:
            pass
        return res
    log("       ✅ 已进入录音状态(绿色像素=%d)" % gcount)

    # 3c. 再等一会儿,确保微信的录音管线真正开始取音
    #     实测:点完按钮立刻播放,开头一段会被漏掉。
    if rec_settle is None:
        rec_settle = T_REC_SETTLE_AFTER
    if rec_settle > 0:
        log("       等待录音管线就绪 %.2fs" % rec_settle)
        time.sleep(rec_settle)

    # 4. 播放(可选同步录音,便于诊断)
    log("[4/5] 播放到虚拟线缆 …")
    diag_path = None
    _diag = DIAG_RECORD if diag_record is None else bool(diag_record)
    if _diag and snapshot_prefix:
        diag_path = "%s_cable.wav" % snapshot_prefix
    elif not _diag:
        log("       (已关闭诊断录音 —— 避免与微信抢录音设备)")
    try:
        info = play_to_cable(stereo, record_to=diag_path)
        if info:
            res["cable_probe"] = info
            log("       实采信号: RMS=%.0f 峰值=%d 时长=%.2fs" % (
                info["rms"], info["peak"], info["duration"]))
            if info["rms"] < 50:
                log("       ⚠ 线缆上信号极弱,微信可能录不到声音")
    except Exception as e:
        res["error"] = "播放失败: %s" % e
        log("   ", res["error"])
        # 播放失败要取消录音,避免发出空白语音
        try:
            cx, cy = anchor_pos(hwnd, "cancel_btn")
            ws.click(cx, cy)
        except Exception:
            pass
        return res
    time.sleep(T_TAIL)

    # 4b. 发送前再确认仍在录音态
    still, g2 = is_recording(hwnd, verbose=verbose)
    if not still:
        res["error"] = "播放后录音已中断(绿色像素=%d),取消发送" % g2
        log("   ", res["error"])
        return res
    if snapshot_prefix:
        _snap(hwnd, "%s_recording.png" % snapshot_prefix)

    # 5. 发送
    #
    # ⚠️ 实测坑:SendInput 可能**静默失败**(前台锁定被拒/焦点被抢),
    #    表现就是"鼠标动了但没点下去"。这时如果不管,录音会一直持续到
    #    微信的 60 秒上限 —— 所以必须:
    #      ① 点击前确保微信在前台;
    #      ② 检查 click() 的返回值;
    #      ③ 点完确认真的退出了录音态,失败则重试(重试前先复位光标到发送键)。
    log("[5/5] 点击发送")
    sx, sy = anchor_pos(hwnd, "send_btn")
    log("       坐标 (%d,%d)" % (sx, sy))

    sent = False
    for attempt in range(3):
        # 每次都先把微信拉到前台,降低 SendInput 被丢弃的概率
        try:
            ws.bring_to_front(hwnd)
            time.sleep(0.25)
        except Exception:
            pass

        ok_click = ws.click(sx, sy)
        if not ok_click:
            log("       ⚠ 第%d次点击未确认送达" % (attempt + 1))

        time.sleep(T_AFTER_SEND)
        left, g = is_recording(hwnd, verbose=verbose)
        if not left:
            log("       ✅ 已退出录音态,发送生效")
            sent = True
            break
        log("       ⚠ 仍在录音(绿色像素=%d),第%d次重试" % (g, attempt + 2))

    if not sent:
        # 三次都没成功:尝试取消,避免留下一条 60 秒的超长录音
        res["error"] = "点击发送失败(3 次),已尝试取消录音"
        log("   ✗ " + res["error"])
        try:
            cx, cy = anchor_pos(hwnd, "cancel_btn")
            ws.click(cx, cy)
            time.sleep(0.6)
        except Exception:
            pass
        res["ok"] = False
        return res

    if snapshot_prefix:
        _snap(hwnd, "%s_sent.png" % snapshot_prefix)

    if not keep_wav:
        try:
            os.remove(wav)
        except Exception:
            pass
    res["ok"] = True
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
