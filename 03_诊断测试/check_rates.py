# -*- coding: utf-8 -*-
"""查 Line 1 播放/录音端支持的采样率,并测试哪个能用。"""
import os
import sys

WD = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "WeChatBot_WXAUTO_SE-3.28")
sys.path.insert(0, os.path.join(WD, "vendor_py"))
import sounddevice as sd

out_idx, in_idx = 26, 36
print("播放端 [%d] %s" % (out_idx, sd.query_devices(out_idx)["name"]))
try:
    print("  默认采样率:", sd.query_devices(out_idx)["default_samplerate"])
except Exception as e:
    print("  读取失败:", e)
print("录音端 [%d] %s" % (in_idx, sd.query_devices(in_idx)["name"]))
try:
    print("  默认采样率:", sd.query_devices(in_idx)["default_samplerate"])
except Exception as e:
    print("  读取失败:", e)

print()
print("=" * 66)
print("逐个测试采样率能否打开")
print("=" * 66)
CANDIDATES = [48000, 44100, 32000, 22050, 16000, 8000]
for rate in CANDIDATES:
    r_out = r_in = "?"
    try:
        s = sd.OutputStream(device=out_idx, samplerate=rate, channels=1, dtype="int16")
        s.close()
        r_out = "OK"
    except Exception as e:
        r_out = "FAIL(%s)" % str(e).split("[")[-1][:10]
    try:
        s = sd.InputStream(device=in_idx, samplerate=rate, channels=1, dtype="int16")
        s.close()
        r_in = "OK"
    except Exception as e:
        r_in = "FAIL(%s)" % str(e).split("[")[-1][:10]
    print("  %6d Hz   播放=%-12s 录音=%-12s" % (rate, r_out, r_in))

print()
print("=" * 66)
print("查 VAC 控制面板设置")
print("=" * 66)
import glob
for p in glob.glob(r"C:\Program Files*\VB\VBCABLE\*") + glob.glob(r"C:\Program Files*\Virtual Audio Cable\*"):
    print("  ", p)
