# -*- coding: utf-8 -*-
"""
底噪基线测试 —— 判断 VoiceMeeter 输入总线是否"干净"。

做法:静音状态下各录 3 秒,比较 RMS/峰值。
  A. VoiceMeeter Output  -> 目标:接近 0(无环境音混入)
  B. 物理麦克风阵列       -> 作为对照,应能录到环境音

说明:录音期间请保持安静(不要说话),但**电脑风扇/键盘等轻微环境音属正常**。
"""
import os
import sys
import time

WD = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "WeChatBot_WXAUTO_SE-3.28")
sys.path.insert(0, os.path.join(WD, "vendor_py"))

import numpy as np
import sounddevice as sd

DUR = 3.0


def find_dev(keyword, want_input=True):
    for i, d in enumerate(sd.query_devices()):
        if keyword.lower() in d["name"].lower():
            if want_input and d["max_input_channels"] > 0:
                return i, d
            if (not want_input) and d["max_output_channels"] > 0:
                return i, d
    return None, None


def record(dev_idx, name, dur=DUR):
    """录 dur 秒,返回 (rms, peak, 实测时长)。"""
    try:
        rate = int(sd.query_devices(dev_idx)["default_samplerate"])
        ch = min(2, sd.query_devices(dev_idx)["max_input_channels"])
        rec = sd.rec(int(dur * rate), samplerate=rate, channels=ch,
                     device=dev_idx, dtype="int16", blocking=True)
        sd.wait()
        flat = rec.flatten().astype(np.float64)
        rms = float(np.sqrt(np.mean(flat ** 2)))
        peak = int(np.max(np.abs(flat)))
        return rms, peak, rate, rec
    except Exception as e:
        print("    录音失败: %s: %s" % (type(e).__name__, e))
        return None, None, None, None


def save(rec, rate, path):
    try:
        import wave
        with wave.open(path, "wb") as w:
            w.setnchannels(rec.shape[1] if rec.ndim > 1 else 1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(rec.astype(np.int16).tobytes())
        return True
    except Exception as e:
        print("    保存失败:", e)
        return False


print("=" * 72)
print("底噪基线测试 —— 请保持安静 %.0f 秒" % (DUR * 2 + 2))
print("=" * 72)

tests = []
i, d = find_dev("VoiceMeeter Output", True)
if i is not None:
    tests.append((i, "A. VoiceMeeter Output (目标链路)", d["name"]))
else:
    print("未找到 VoiceMeeter Output")

i2, d2 = find_dev("麦克风阵列", True)
if i2 is None:
    i2, d2 = find_dev("Realtek", True)
if i2 is not None:
    tests.append((i2, "B. 物理麦克风 (对照)", d2["name"]))
else:
    print("未找到物理麦克风")

results = {}
for idx, label, realname in tests:
    print()
    print("%s" % label)
    print("    设备[%d] %s" % (idx, realname[:52]))
    print("    录音中 … 保持安静", end="", flush=True)
    rms, peak, rate, rec = record(idx, label)
    print("  完成")
    if rms is None:
        continue
    results[label] = (rms, peak)
    print("    RMS = %-10.2f 峰值 = %-8d 采样率 = %d" % (rms, peak, rate))
    p = os.path.join(WD, "_noise_%s.wav" % ("vm" if "VoiceMeeter" in label else "mic"))
    if save(rec, rate, p):
        print("    已存:", os.path.basename(p))

print()
print("=" * 72)
print("判读")
print("=" * 72)
vm = results.get("A. VoiceMeeter Output (目标链路)")
mic = results.get("B. 物理麦克风 (对照)")
if vm:
    r, pk = vm
    print("  VoiceMeeter Output : RMS=%.2f  峰值=%d" % (r, pk))
    if pk < 50:
        print("     >>> 干净!几乎没有信号 -> 物理麦克风未混入,")
        print("         把 MuMu 麦克风改成 VoiceMeeter Output 后环境音会消失。")
    elif pk < 500:
        print("     >>> 基本干净(有极低底噪/残余)。大概率可用,")
        print("         建议在 VoiceMeeter 面板确认没有多余的 Hardware Input。")
    else:
        print("     >>> 有明显的信号 -> 物理麦克风可能已混入 VoiceMeeter,")
        print("         需要先把它从输入总线摘掉,否则环境音仍会被录到。")
if mic:
    r2, pk2 = mic
    print("  物理麦克风(对照)  : RMS=%.2f  峰值=%d" % (r2, pk2))
    if vm and pk2 > pk * 3:
        print("     >>> 对照有效:物理麦克风明显比 VoiceMeeter 吵,")
        print("         两者差异印证了「换源」能消除环境音。")
