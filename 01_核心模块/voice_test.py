# -*- coding: utf-8 -*-
"""
voice_test.py — 独立测试脚本：GPT-SoVITS -> 微信语音

不改动 bot.py，直接验证整条链路。

用法示例：
  # 1) 只测语音合成，不发微信（最安全，先跑这个）
  python voice_test.py --check
  python voice_test.py --tts "你好，我是小玲" --out test.wav

  # 2) 合成并发到微信（会真的发消息！）
  python voice_test.py --text "你好呀，在忙什么呢？" --who "文件传输助手"
  python voice_test.py --text "你好" --who "文件传输助手" --mode file

参数：
  --text/-t     要朗读的文本
  --who/-w      微信发送对象（昵称或群名），建议先用「文件传输助手」测试
  --mode        发送模式: auto(默认) / audio(仅语音条) / file(仅音频文件)
  --out         仅合成时保存到的路径
  --ref         参考音频路径
  --prompt      参考音频对应的文本
  --speed       语速倍率，默认 1.0
  --prob        指定语音概率(测试用)，force 时忽略
  --force       忽略概率/长度限制
  --check       只做环境检查
  --env         打印环境报告
"""

import os
import sys
import argparse

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import gptsovits_tts as tts


def print_env():
    """打印环境与能力报告（不导入微信，避免触发授权检查）。"""
    print("=" * 60)
    print("环境检查")
    print("=" * 60)
    print("GPT-SoVITS 目录 : %s" % tts.GPT_SOVITS_DIR)
    print("TTS 接口地址    : http://%s:%s/tts" % (tts.GPT_SOVITS_HOST, tts.GPT_SOVITS_PORT))

    alive = tts.is_server_alive(timeout=3)
    print("TTS 服务在线    : %s" % ("是" if alive else "否"))

    ref = tts._resolve_ref_audio(tts.DEFAULT_REF_AUDIO)
    print("参考音频        : %s" % ref)
    print("参考音频存在    : %s" % ("是" if os.path.isfile(ref) else "否"))
    if os.path.isfile(ref):
        d = tts.audio_duration(ref)
        print("参考音频时长    : %s" % ("%.2fs" % d if d else "未知"))
    print("参考音频文本    : %s" % (tts.DEFAULT_PROMPT_TEXT or "(未设置，建议填写以提高音色还原度)"))

    # ffmpeg
    try:
        import shutil
        print("ffmpeg          : %s" % (shutil.which("ffmpeg") or "未找到"))
    except Exception:
        print("ffmpeg          : 检测失败")

    # wxauto 语音条能力
    # 注意：未授权设备时 wxauto 会在 import 阶段抛 SystemExit，必须一并捕获
    try:
        from wxautox4_wechatbot import WeChat  # type: ignore
        has = hasattr(WeChat, "SendAudio")
        ver = "unknown"
        try:
            import importlib.metadata as md
            ver = md.version("wxautox4-wechatbot")
        except Exception:
            pass
        print("wxautox4 版本   : %s" % ver)
        print("SendAudio 支持  : %s" % ("是（可发语音条）" if has else "否（将降级为音频文件）"))
    except SystemExit:
        print("wxauto 状态     : 未授权设备（import 被拒绝）")
        print("                  解决：当前设备未获得 wxauto 授权，")
        print("                  请到 https://plus.wxauto.org 获取授权后再运行 bot。")
        print("                  GPT-SoVITS 侧的语音合成不受影响，可正常测试。")
    except Exception as e:
        print("wxauto 检测失败 : %s" % e)
        print("提示：需要微信客户端已登录，且 wxauto 授权有效。")

    print("=" * 60)
    return alive


def do_synthesize(args):
    """只做合成，不发微信。"""
    out = args.out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "voice_test_output.wav"
    )
    print("正在合成: %r" % args.text)
    path = tts.synthesize(
        args.text,
        out,
        ref_audio_path=args.ref,
        prompt_text=args.prompt,
        speed_factor=args.speed,
    )
    size = os.path.getsize(path)
    dur = tts.audio_duration(path)
    print("合成完成: %s" % path)
    print("文件大小: %.1f KB" % (size / 1024.0))
    print("音频时长: %s" % ("%.2fs" % dur if dur else "未知"))
    return path


def do_send(args):
    """合成并发送到微信。"""
    if not args.who:
        print("错误：发送到微信必须指定 --who（建议先用「文件传输助手」测试）")
        return 1

    # 延迟导入：只有在真正要发微信时才初始化 wxauto
    import voice_reply

    # 用命令行参数覆盖配置
    if args.mode:
        os.environ["VOICE_FORCE_MODE"] = args.mode
    if args.ref:
        voice_reply.VOICE_REF_AUDIO = args.ref
    if args.prompt is not None:
        voice_reply.VOICE_PROMPT_TEXT = args.prompt
    if args.speed:
        voice_reply.VOICE_SPEED = args.speed

    # 让 send_voice 用命令行指定的模式
    if args.mode:
        voice_reply.VOICE_SEND_MODE = args.mode

    print("[1/3] 初始化微信（请确保微信已登录且窗口未最小化）...")
    try:
        from wxautox4_wechatbot import WeChat
        wx = WeChat()
    except SystemExit:
        print("微信初始化失败：当前设备未获得 wxauto 授权。")
        print("请到 https://plus.wxauto.org 获取授权后重试。")
        return 1
    except Exception as e:
        print("微信初始化失败: %s" % e)
        print("提示：需要微信客户端已登录并保持运行。")
        return 1

    print("[2/3] 探测语音条能力...")
    can = voice_reply.supports_send_audio(wx)
    print("      SendAudio 可用: %s" % ("是" if can else "否（将发送音频文件）"))

    print("[3/3] 合成并发送给 %r ..." % args.who)
    ok = voice_reply.send_voice(wx, args.who, args.text, force=True)
    print("发送结果: %s" % ("成功" if ok else "失败"))
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(
        description="GPT-SoVITS -> 微信语音 测试脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--text", "-t", help="要朗读的文本")
    parser.add_argument("--who", "-w", help="微信发送对象（昵称/群名）")
    parser.add_argument("--mode", choices=["auto", "audio", "file"],
                        help="发送模式：auto=语音条优先自动降级 / audio=仅语音条 / file=仅音频文件")
    parser.add_argument("--out", help="仅合成时输出路径")
    parser.add_argument("--ref", help="参考音频路径")
    parser.add_argument("--prompt", help="参考音频对应文本")
    parser.add_argument("--speed", type=float, default=None, help="语速倍率，默认 1.0")
    parser.add_argument("--check", action="store_true", help="只做环境检查")
    parser.add_argument("--env", action="store_true", help="打印环境报告")

    args = parser.parse_args()

    if args.check or args.env:
        print_env()
        if args.check:
            return 0

    if not args.text:
        parser.print_help()
        print("\n提示：先运行  python voice_test.py --check   查看环境是否就绪。")
        return 0

    # 没指定 --who 就只合成
    if not args.who:
        do_synthesize(args)
        return 0

    return do_send(args)


if __name__ == "__main__":
    sys.exit(main())
