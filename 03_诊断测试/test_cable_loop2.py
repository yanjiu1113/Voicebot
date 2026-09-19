# -*- coding: utf-8 -*-
"""
端到端直通测试 v2:加入重采样(32k -> 48k)与立体声输出。
Line 1 固定 48000Hz,必须重采样。
"""
import os
import sys
import time
import wave

WD = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "WeChatBot_WXAUTO_SE-3.28")
sys.path.insert(0, WD)
sys.path.insert(0, os.path.join(WD, "vendor_py"))

import numpy as np
import sounddevice as sd

OUT_IDX = 26      # Line 1 播放端
IN_IDX = 36       # Line 1 录音端
RATE = 48000


def resample_linear(x, src_rate, dst_rate):
    """线性插值重采样(避免引入 scipy 依赖)。"""
    if src_rate == dst_rate:
        return x
    n_out = int(round(len(x) * dst_rate / float(src_rate)))
    idx = np.linspace(0, len(x) - 1, n_out)
    lo = np.floor(idx).astype(np.int64)
    hi = np.minimum(lo + 1, len(x) - 1)
    frac = idx - lo
    out = x[lo] * (1 - frac) + x[hi] * frac
    return out.astype(np.int16)


# 1) 合成
wav = os.path.join(WD, "_cabletest.wav")
print("合成测试语音 …")
import gptsovits_tts as tts
if not tts.is_server_alive():
    print("  TTS 未就绪"); raise SystemExit(2)
tts.synthesize("这是一段虚拟线缆直通测试,一二三四五六七。", wav)

with wave.open(wav, "rb") as w:
    src_rate = w.getframerate()
    ch = w.getnchannels()
    raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
if ch > 1:
    raw = raw.reshape(-1, ch).mean(axis=1).astype(np.int16)

print("  原始: %.2fs @ %dHz 单声道" % (len(raw) / src_rate, src_rate))
res = resample_linear(raw.astype(np.float64), src_rate, RATE)
print("  重采样后: %.2fs @ %dHz" % (len(res) / RATE, RATE))

# 立体声(左右都填同一信号),提高录音端通道匹配成功率
stereo = np.repeat(res.reshape(-1, 1), 2, axis=1)

rec = []
def cb(indata, frames, t, status):
    rec.append(indata.copy())

print()
print("=" * 74)
print("播到 Line 1[26] (立体声 48k),同时从 Line 1[36] 采集")
print("=" * 74)
try:
    with sd.OutputStream(device=OUT_IDX, samplerate=RATE, channels=2, dtype="int16") as o:
        with sd.InputStream(device=IN_IDX, samplerate=RATE, channels=2,
                            dtype="int16", callback=cb):
            time.sleep(0.3)
            o.write(stereo)
            time.sleep(0.8)
except Exception as e:
    print("失败:", type(e).__name__, e)
    raise SystemExit(3)

if not rec:
    print("没采到数据"); raise SystemExit(4)

arr = np.concatenate(rec)
flat = arr.flatten().astype(np.float64)
rms = float(np.sqrt(np.mean(flat ** 2)))
peak = int(np.max(np.abs(flat)))
print("  采到 %.2fs  RMS=%.1f  峰值=%d" % (len(arr) / RATE, rms, peak))

# 分左右看
if arr.ndim > 1 and arr.shape[1] >= 2:
    l = arr[:, 0].astype(np.float64); r = arr[:, 1].astype(np.float64)
    print("  左声道 RMS=%.1f 峰值=%d | 右声道 RMS=%.1f 峰值=%d" % (
        np.sqrt(np.mean(l**2)), int(np.max(np.abs(l))),
        np.sqrt(np.mean(r**2)), int(np.max(np.abs(r)))))

print()
if peak > 500 and rms > 50:
    print(">>> 直通成功!默认麦克风设为 Line 1 后,微信点击录音即可录到我们播的音频")
else:
    print(">>> 信号弱。可能 VAC 的 Line 1 录音端未启用,或需要 audiorepeater 建立路由")

outp = os.path.join(WD, "_cabletest_rec.wav")
with wave.open(outp, "wb") as w:
    w.setnchannels(2 if arr.ndim > 1 else 1)
    w.setsampwidth(2); w.setframerate(RATE)
    w.writeframes(arr.astype(np.int16).tobytes())
print("  录音已保存:", os.path.basename(outp))
