# -*- coding: utf-8 -*-
"""
(1) 列出所有 Line 1 设备的通道配置,找出配对
(2) 用 Core Audio 读出默认录音设备的**名字**(而不只是 GUID)
"""
import os
import sys
import comtypes
comtypes.CoInitialize()

WD = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "WeChatBot_WXAUTO_SE-3.28")
sys.path.insert(0, os.path.join(WD, "vendor_py"))

import sounddevice as sd

print("=" * 74)
print("所有 Line 1 / VAC / VoiceMeeter 设备的方向与通道")
print("=" * 74)
for i, d in enumerate(sd.query_devices()):
    n = d["name"]
    low = n.lower()
    if any(h in low for h in ("line 1", "mic 1", "virtual cable", "voicemeeter", "vb-audio")):
        direction = []
        if d["max_output_channels"] > 0:
            direction.append("播放(out=%d)" % d["max_output_channels"])
        if d["max_input_channels"] > 0:
            direction.append("录音(in=%d)" % d["max_input_channels"])
        hostapi = sd.query_hostapis(d["hostapi"])["name"]
        print("  [%2d] %-42s %-22s %s" % (
            i, n[:42], " ".join(direction), hostapi[:20]))

# 默认设备
print()
print("=" * 74)
print("sounddevice 默认设备")
print("=" * 74)
din, dout = sd.default.device
try:
    print("  默认输入 [%s] = %s" % (din, sd.query_devices(din)["name"]))
except Exception as e:
    print("  默认输入读取失败:", e)
try:
    print("  默认输出 [%s] = %s" % (dout, sd.query_devices(dout)["name"]))
except Exception as e:
    print("  默认输出读取失败:", e)

# Core Audio 读默认录音设备名字
print()
print("=" * 74)
print("Core Audio: 默认录音设备的名称")
print("=" * 74)
try:
    from pycaw.pycaw import AudioUtilities
    # pycaw 有 GetMicrophone 吗?
    mic = None
    if hasattr(AudioUtilities, "GetMicrophone"):
        mic = AudioUtilities.GetMicrophone()
    if mic:
        print("  GetMicrophone() ->", mic.FriendlyName)
    else:
        print("  pycaw 无 GetMicrophone,改用枚举匹配 GUID")
        from pycaw.pycaw import AudioUtilities as AU
        for d in AU.GetAllDevices():
            if "fbb85295" in str(getattr(d, "id", "")).lower():
                print("  GUID 匹配 ->", getattr(d, "FriendlyName", "?"), "| state=", getattr(d, "state", "?"))
except Exception as e:
    print("  失败:", type(e).__name__, e)
