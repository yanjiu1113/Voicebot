# -*- coding: utf-8 -*-
"""
验证 VoiceMeeter Input -> VoiceMeeter Output 是否真直通。

做法:
  1. 用 GPT-SoVITS 生成一段可辨识的语音 WAV
  2. 用 sounddevice 播放到 "VoiceMeeter Input"
  3. 用 sounddevice 从 "VoiceMeeter Output" 录音
  4. 对比录音长度/能量,判断音频是否真的走通了

注:sounddevice 已装在项目 vendor_py(含 PortAudio)。
    这一步不改任何系统设置,只播放一段我们自己的合成音频。
"""
import os
import sys
import time
import wave
import struct
import math

WD = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "WeChatBot_WXAUTO_SE-3.28")
sys.path.insert(0, WD)
sys.path.insert(0, os.path.join(WD, "vendor_py"))
import wxauto_bootstrap
wxauto_bootstrap.setup()

import numpy as np
import sounddevice as sd


def list_devices():
    print("=" * 72)
    print("sounddevice/PortAudio 设备列表")
    print("=" * 72)
    devs = sd.query_devices()
    for i, d in enumerate(devs):
        name = d["name"]
        if any(h in name.lower() for h in ("voicemeeter", "cable", "vb-audio", "virtual")):
            print("  [%d] %-46s in=%d out=%d" % (
                i, name[:46], d["max_input_channels"], d["max_output_channels"]))
    print()
    print("默认设备:", sd.default.device)
    return devs


def find(devs, keyword, want_input):
    for i, d in enumerate(devs):
        if keyword.lower() in d["name"].lower():
            if want_input and d["max_input_channels"] > 0:
                return i
            if (not want_input) and d["max_output_channels"] > 0:
                return i
    return None


def synth(path):
    print("生成测试语音 …")
    import gptsovits_tts as tts
    tts.synthesize("这是一段虚拟声卡直通测试,一二三四五。", path)
    with wave.open(path, "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
        ch = w.getnchannels()
        sw = w.getsampwidth()
    print("  已生成: %s  %.2fs  %dHz  %dch" % (path, frames / rate, rate, ch))
    return rate, ch


def main():
    devs = list_devices()

    out_idx = find(devs, "VoiceMeeter Input", want_input=False)
    in_idx = find(devs, "VoiceMeeter Output", want_input=True)
    print("VoiceMeeter Input  (播放) 设备号:", out_idx)
    print("VoiceMeeter Output (录音) 设备号:", in_idx)
    if out_idx is None or in_idx is None:
        print("未找到 VoiceMeeter 设备,无法测试")
        return

    wav = os.path.join(WD, "_vadtest.wav")
    rate, ch = synth(wav)

    # 读取 wav
    with wave.open(wav, "rb") as w:
        n = w.getnframes()
        data = np.frombuffer(w.readframes(n), dtype=np.int16)
        src_rate = w.getframerate()
        src_ch = w.getnchannels()
    if src_ch > 1:
        data = data.reshape(-1, src_ch).mean(axis=1).astype(np.int16)
    dur = len(data) / src_rate
    print("  播放时长: %.2fs" % dur)

    print()
    print("=" * 72)
    print("实测:播到 VoiceMeeter Input,同时从 VoiceMeeter Output 录")
    print("=" * 72)
    rec_frames = []

    def cb(indata, frames, t, status):
        rec_frames.append(indata.copy())

    try:
        with sd.OutputStream(device=out_idx, samplerate=src_rate,
                             channels=1, dtype="int16") as os_:
            with sd.InputStream(device=in_idx, samplerate=src_rate,
                                channels=1, dtype="int16", callback=cb):
                time.sleep(0.3)
                os_.write(data)
                time.sleep(0.6)     # 多录一点尾巴
    except Exception as e:
        print("失败:", type(e).__name__, e)
        print()
        print("可能原因:VoiceMeeter 的输入/输出通道未在混音器里路由,或采样率不匹配")
        return

    if not rec_frames:
        print(">>> 没有录到任何数据")
        return

    rec = np.concatenate(rec_frames).flatten()
    rec_dur = len(rec) / src_rate
    rms = float(np.sqrt(np.mean(rec.astype(np.float64) ** 2)))
    peak = int(np.max(np.abs(rec))) if len(rec) else 0

    print("  录到 %.2fs, RMS=%.1f, 峰值=%d" % (rec_dur, rms, peak))
    print()
    if peak > 300 and rms > 30:
        print(">>> 直通成功!音频从 VoiceMeeter Input 流到了 Output")
        print(">>> 也就是说:模拟器只要把麦克风选成 VoiceMeeter Output,")
        print(">>>            就能「听到」我们在电脑上播放的音频。")
    else:
        print(">>> 录到了数据但能量极低 —— 可能 VoiceMeeter 未启用内部路由,")
        print(">>> 或需要打开 VoiceMeeter 面板把 Input 拉到 B 总线。")

    # 保存录音供人工听
    outp = os.path.join(WD, "_vadtest_rec.wav")
    try:
        with wave.open(outp, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(src_rate)
            w.writeframes(rec.astype(np.int16).tobytes())
        print("  录音已保存:", outp)
    except Exception as e:
        print("  保存录音失败:", e)


if __name__ == "__main__":
    main()
