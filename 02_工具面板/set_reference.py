# -*- coding: utf-8 -*-
"""
配置参考音频与它对应的文字(prompt_text)。

GPT-SoVITS 用「参考音频 + 该音频的文字内容」来克隆音色。
prompt_text 为空时音色会明显失真 —— 这是实测踩过的坑。

用法:
    python set_reference.py                          # 显示当前配置
    python set_reference.py <音频文件> [文字]         # 指定音频与文字
    python set_reference.py <音频文件> --from-name    # 用文件名当文字
    python set_reference.py --list                    # 列出可用参考音频

也可以直接把音频丢进 参考音频/ 目录,文字取自文件名。
"""

import argparse
import io
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DESKTOP = os.path.dirname(ROOT)
TTS_DIR = None

# 定位 GPT-SoVITS 目录
for name in os.listdir(DESKTOP):
    p = os.path.join(DESKTOP, name)
    if os.path.isdir(p) and ("GPT-SoVITS" in name or "GPT_SoVITS" in name) \
            and os.path.isdir(os.path.join(p, "GPT_SoVITS")):
        TTS_DIR = p
        break

REF_DIR = os.path.join(ROOT, "参考音频")
CONF = os.path.join(ROOT, "reference_config.json")
AUDIO_EXT = (".wav", ".mp3", ".flac", ".m4a", ".ogg")

DEFAULTS = {
    "ref_audio": "",       # 绝对路径
    "prompt_text": "",     # 该音频的文字内容
    "prompt_lang": "zh",
}


def load():
    cfg = dict(DEFAULTS)
    if os.path.isfile(CONF):
        try:
            cfg.update(json.load(io.open(CONF, encoding="utf-8")))
        except Exception as e:
            print("配置读取失败:", e)
    return cfg


def save(cfg):
    io.open(CONF, "w", encoding="utf-8").write(
        json.dumps(cfg, ensure_ascii=False, indent=2))


def ensure_ref_dir():
    os.makedirs(REF_DIR, exist_ok=True)
    # 把散落在 VoiceBot 根目录的音频收进来
    moved = 0
    for fn in os.listdir(ROOT):
        p = os.path.join(ROOT, fn)
        if os.path.isfile(p) and fn.lower().endswith(AUDIO_EXT):
            dst = os.path.join(REF_DIR, fn)
            if not os.path.exists(dst):
                shutil.move(p, dst)
                moved += 1
                print("  已收入参考音频/: %s" % fn)
    return moved


def list_refs():
    ensure_ref_dir()
    out = []
    if os.path.isdir(REF_DIR):
        for fn in sorted(os.listdir(REF_DIR)):
            if fn.lower().endswith(AUDIO_EXT):
                out.append(os.path.join(REF_DIR, fn))
    return out


def show():
    cfg = load()
    print("=" * 70)
    print("当前参考音频配置")
    print("=" * 70)
    print("  配置文件 :", CONF)
    print("  参考音频 :", cfg.get("ref_audio") or "(未设置)")
    pt = cfg.get("prompt_text") or ""
    print("  对应文字 :", (pt[:60] + "…") if len(pt) > 60 else (pt or "(空!)"))
    if not pt:
        print()
        print("  ⚠️  对应文字为空 —— 这会导致音色明显失真!")
        print("     用 python set_reference.py <音频> --from-name 可自动填入")
    if cfg.get("ref_audio") and not os.path.isfile(cfg["ref_audio"]):
        print()
        print("  ⚠️  参考音频文件不存在!")
    print()
    refs = list_refs()
    if refs:
        print("  可用的参考音频(%d 个):" % len(refs))
        for r in refs:
            sz = os.path.getsize(r) / 1024.0
            print("    %-58s %7.1f KB" % (os.path.basename(r)[:58], sz))


def set_ref(audio, text, lang="zh"):
    ensure_ref_dir()
    # 允许只给文件名(从 参考音频/ 里找)
    if not os.path.isabs(audio):
        cand = os.path.join(REF_DIR, audio)
        if os.path.isfile(cand):
            audio = cand
        elif os.path.isfile(os.path.join(ROOT, audio)):
            audio = os.path.join(ROOT, audio)
    if not os.path.isfile(audio):
        print("❌ 找不到音频文件:", audio)
        return 1

    # 存相对路径(相对 VoiceBot 根目录),这样配置可移植、不泄露用户名
    abs_audio = os.path.abspath(audio)
    try:
        rel = os.path.relpath(abs_audio, ROOT)
        if rel.startswith(".."):
            stored = abs_audio          # 在项目外,只能存绝对路径
        else:
            stored = rel.replace("\\", "/")
    except Exception:
        stored = abs_audio

    cfg = {
        "ref_audio": stored,
        "prompt_text": text or "",
        "prompt_lang": lang,
    }
    save(cfg)
    print("✅ 已保存")
    print("   参考音频 :", cfg["ref_audio"])
    print("   对应文字 :", cfg["prompt_text"][:70] or "(空)")
    return 0


def main():
    ap = argparse.ArgumentParser(description="配置参考音频与对应文字")
    ap.add_argument("audio", nargs="?", help="参考音频文件(可用文件名)")
    ap.add_argument("text", nargs="?", help="该音频的文字内容")
    ap.add_argument("--from-name", action="store_true",
                    help="用音频文件名(去掉扩展名)作为文字")
    ap.add_argument("--lang", default="zh", help="参考音频语种 zh/en/ja/ko/yue")
    ap.add_argument("--list", action="store_true", help="列出可用参考音频")
    args = ap.parse_args()

    if args.list:
        refs = list_refs()
        print("参考音频目录:", REF_DIR)
        for r in refs:
            print("  %-58s %7.1f KB" % (os.path.basename(r)[:58],
                                        os.path.getsize(r) / 1024.0))
        if not refs:
            print("  (空)把音频文件丢进这个目录即可")
        return

    if not args.audio:
        show()
        return

    text = args.text
    if args.from_name or not text:
        text = os.path.splitext(os.path.basename(args.audio))[0]
        print("使用文件名作为文字:", text)
    sys.exit(set_ref(args.audio, text, args.lang))


if __name__ == "__main__":
    main()
