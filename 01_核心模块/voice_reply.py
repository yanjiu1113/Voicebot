# -*- coding: utf-8 -*-
"""
voice_reply.py — 让 WeChatBot 通过 GPT-SoVITS 在微信发送语音

发送策略（自动降级）：
  1. 优先调用 wx.SendAudio(...) 发真正的「微信语音条」。
     需要 wxautox4 >= 40.1.13 + 微信 4.1.9+ + ffmpeg + VB-CABLE 虚拟声卡。
  2. 若当前 wxauto 版本没有 SendAudio（例如 40.1.10），
     自动降级为 wx.SendFiles(...) 发送可播放的音频文件。

对外主入口：
    send_voice(wx, user_id, text)  -> bool

配置项见文件末尾 CONFIG 说明，或直接在 config.py 增加同名变量覆盖。
"""

import os
import re
import time
import random
import logging
import tempfile
import threading
from datetime import datetime

import gptsovits_tts as tts

logger = logging.getLogger("voice_reply")

# ---------------------------------------------------------------------------
# 默认配置（可在 config.py 中用同名变量覆盖）
# ---------------------------------------------------------------------------

ENABLE_VOICE_REPLY = True          # 总开关
VOICE_REPLY_PROBABILITY = 100      # 每条回复发语音的概率(%)
VOICE_MAX_CHARS = 120              # 超过此长度不转语音（语音条不适合太长）
VOICE_SEND_MODE = "auto"           # auto=有 SendAudio 就发语音条，否则发文件; audio=只发语音条; file=只发文件
VOICE_MAX_DURATION = 60            # 语音条最长秒数（微信上限 60s）
VOICE_SPLIT_LONG_TEXT = True       # 长文本是否切分成多条语音
VOICE_TEMP_DIR = "Voice_Temp"      # 音频临时目录
VOICE_KEEP_FILES = False           # 是否保留生成的音频文件
VOICE_TEXT_MODE = "voice_only"     # voice_only=只发语音; text_and_voice=先文字后语音; voice_and_text=先语音后文字

# 参考音频配置（透传给 gptsovits_tts）
VOICE_REF_AUDIO = tts.DEFAULT_REF_AUDIO
VOICE_PROMPT_TEXT = tts.DEFAULT_PROMPT_TEXT
VOICE_PROMPT_LANG = tts.DEFAULT_PROMPT_LANG
VOICE_TEXT_LANG = tts.DEFAULT_TEXT_LANG
VOICE_SPEED = tts.DEFAULT_SPEED


def _cfg(name, default):
    """从 WeChatBot 的 config.py 读取配置（若存在），否则用默认值。

    这样用户既可以在 config.py 里改，也可以不改而使用本文件默认值。
    """
    try:
        import config as bot_config
        return getattr(bot_config, name, default)
    except Exception:
        return default


def _get_conf():
    """每次发送时重新读取配置，方便用 config.py 动态调整。"""
    return {
        "enabled": bool(_cfg("ENABLE_VOICE_REPLY", ENABLE_VOICE_REPLY)),
        "probability": float(_cfg("VOICE_REPLY_PROBABILITY", VOICE_REPLY_PROBABILITY)),
        "max_chars": int(_cfg("VOICE_MAX_CHARS", VOICE_MAX_CHARS)),
        "mode": str(_cfg("VOICE_SEND_MODE", VOICE_SEND_MODE)),
        "max_duration": float(_cfg("VOICE_MAX_DURATION", VOICE_MAX_DURATION)),
        "split_long": bool(_cfg("VOICE_SPLIT_LONG_TEXT", VOICE_SPLIT_LONG_TEXT)),
        "temp_dir": str(_cfg("VOICE_TEMP_DIR", VOICE_TEMP_DIR)),
        "keep": bool(_cfg("VOICE_KEEP_FILES", VOICE_KEEP_FILES)),
        "text_mode": str(_cfg("VOICE_TEXT_MODE", VOICE_TEXT_MODE)),
        "ref_audio": str(_cfg("VOICE_REF_AUDIO", VOICE_REF_AUDIO)),
        "prompt_text": str(_cfg("VOICE_PROMPT_TEXT", VOICE_PROMPT_TEXT)),
        "prompt_lang": str(_cfg("VOICE_PROMPT_LANG", VOICE_PROMPT_LANG)),
        "text_lang": str(_cfg("VOICE_TEXT_LANG", VOICE_TEXT_LANG)),
        "speed": float(_cfg("VOICE_SPEED", VOICE_SPEED)),
    }


# ---------------------------------------------------------------------------
# 能力探测：当前 wxauto 到底能不能发语音条
# ---------------------------------------------------------------------------

_AUDIO_CAPABILITY = None  # None=未探测, True/False=结果


def supports_send_audio(wx=None, refresh=False):
    """探测当前 wxauto 是否支持发送语音条（SendAudio）。

    结果会被缓存，避免每条消息都做一次反射。
    """
    global _AUDIO_CAPABILITY
    if _AUDIO_CAPABILITY is not None and not refresh:
        return _AUDIO_CAPABILITY

    ok = False

    # 1) 有实例时直接看实例，最可靠，且不需要 import
    if wx is not None:
        try:
            ok = callable(getattr(wx, "SendAudio", None))
        except Exception:
            ok = False
    else:
        # 2) 没有实例时才尝试 import 探测。
        #    注意：wxautox4 未授权设备时会在 import 阶段抛 SystemExit，
        #    所以这里必须同时捕获 BaseException 类的东西，否则会把 bot 直接带崩。
        for modname in ("wxautox4_wechatbot", "wxautox4"):
            try:
                mod = __import__(modname, fromlist=["WeChat"])
                WeChat = getattr(mod, "WeChat", None)
                if WeChat is not None:
                    ok = hasattr(WeChat, "SendAudio")
                    break
            except SystemExit:
                # 未授权设备：明确降级，不要把整个进程干掉
                logger.warning(
                    "wxauto(%s) 授权校验未通过，无法探测 SendAudio，按不支持处理。", modname
                )
                ok = False
                break
            except BaseException:
                continue

    _AUDIO_CAPABILITY = ok
    return ok


def describe_environment(wx=None):
    """返回一份人类可读的能力报告，便于排查配置问题。"""
    lines = []
    lines.append("GPT-SoVITS 服务在线: %s" % ("是" if tts.is_server_alive() else "否"))
    lines.append("SendAudio(语音条)可用: %s" % ("是" if supports_send_audio(wx) else "否"))
    ref = tts._resolve_ref_audio(_get_conf()["ref_audio"])
    lines.append("参考音频: %s (%s)" % (ref, "存在" if os.path.isfile(ref) else "缺失"))
    try:
        import shutil
        lines.append("ffmpeg: %s" % (shutil.which("ffmpeg") or "未找到"))
    except Exception:
        lines.append("ffmpeg: 检测失败")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 文件管理
# ---------------------------------------------------------------------------

_send_lock = threading.Lock()


def _temp_dir(conf):
    d = conf["temp_dir"]
    if not os.path.isabs(d):
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), d)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    return d


def _new_audio_path(conf, user_id):
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", str(user_id))[:40] or "user"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return os.path.join(_temp_dir(conf), "%s_%s.wav" % (safe, stamp))


def _cleanup(path, keep):
    if keep:
        return
    try:
        if os.path.isfile(path):
            os.remove(path)
    except Exception as e:
        logger.debug("删除临时音频失败 %s: %s", path, e)


def cleanup_temp_dir(conf=None):
    """清空语音临时目录（可挂在 bot 的定时清理里）。"""
    conf = conf or _get_conf()
    d = _temp_dir(conf)
    removed = 0
    try:
        for name in os.listdir(d):
            if name.lower().endswith(".wav"):
                try:
                    os.remove(os.path.join(d, name))
                    removed += 1
                except Exception:
                    pass
    except Exception as e:
        logger.debug("清理语音临时目录失败: %s", e)
    return removed


# ---------------------------------------------------------------------------
# 核心发送逻辑
# ---------------------------------------------------------------------------

def _send_audio_message(wx, filepath, user_id, max_retries=3):
    """尝试用 SendAudio 发送语音条。返回 True/False。"""
    duration = tts.audio_duration(filepath)
    kwargs = {"filepath": filepath, "who": user_id, "max_retries": max_retries}
    if duration:
        kwargs["duration"] = int(min(duration, VOICE_MAX_DURATION))

    try:
        result = wx.SendAudio(**kwargs)
        # WxResponse 可能为对象，bool() 判断
        if result is None:
            return True
        ok = bool(result)
        if not ok:
            # 有些版本返回带 message 的对象
            msg = getattr(result, "message", None)
            if msg:
                logger.warning("SendAudio 返回失败: %s", msg)
        return ok
    except TypeError:
        # 参数不兼容，退回最小参数集
        try:
            result = wx.SendAudio(filepath, who=user_id)
            return True if result is None else bool(result)
        except Exception as e:
            logger.warning("SendAudio 调用失败: %s", e)
            return False
    except Exception as e:
        logger.warning("SendAudio 异常: %s", e)
        return False


def _send_audio_file(wx, filepath, user_id, retries=3):
    """降级方案：把音频作为文件发送。返回 True/False。"""
    for attempt in range(retries):
        try:
            result = wx.SendFiles(filepath=filepath, who=user_id)
            if result is None or bool(result):
                return True
            logger.warning("发送音频文件失败，第 %d 次", attempt + 1)
        except Exception as e:
            logger.warning("发送音频文件异常，第 %d 次: %s", attempt + 1, e)
        if attempt < retries - 1:
            time.sleep(0.5)
    return False


def send_voice(wx, user_id, text, force=False):
    """把 text 合成语音并发送给 user_id。

    Args:
        wx: WeChatBot 的 WeChat 实例。
        user_id: 微信昵称 / 群名。
        text: 要朗读的文本。
        force: True 时忽略概率与长度限制（用于手动测试）。

    Returns:
        bool: 是否成功发送了语音。
    """
    conf = _get_conf()

    if not conf["enabled"] and not force:
        return False

    clean = tts.clean_text_for_tts(text)
    if not clean:
        logger.info("语音回复：清洗后无有效文本，跳过。")
        return False

    if not force:
        if len(clean) > conf["max_chars"]:
            logger.info(
                "语音回复：文本长度 %d 超过上限 %d，跳过语音。",
                len(clean), conf["max_chars"],
            )
            return False
        if conf["probability"] < 100 and random.uniform(0, 100) > conf["probability"]:
            logger.info("语音回复：未命中概率（%s%%），本次不发语音。", conf["probability"])
            return False

    mode = conf["mode"].lower()
    can_audio = supports_send_audio(wx)
    if mode == "audio" and not can_audio:
        logger.warning(
            "语音回复：VOICE_SEND_MODE=audio 但当前 wxauto 不支持 SendAudio，已跳过。"
        )
        return False

    # 合成文本：长文本可选择切分
    segments = [clean]
    if conf["split_long"] and len(clean) > conf["max_chars"]:
        segments = tts.split_text_for_tts(
            clean, max_len=int(conf["max_chars"])
        ) or [clean[:conf["max_chars"]]]

    sent_any = False
    with _send_lock:
        for seg in segments:
            path = _new_audio_path(conf, user_id)
            try:
                tts.synthesize(
                    seg,
                    path,
                    ref_audio_path=conf["ref_audio"],
                    prompt_text=conf["prompt_text"],
                    prompt_lang=conf["prompt_lang"],
                    text_lang=conf["text_lang"],
                    speed_factor=conf["speed"],
                )
            except tts.TTSError as e:
                logger.error("语音合成失败，跳过本条语音: %s", e)
                _cleanup(path, conf["keep"])
                continue

            # 发送：语音条优先，失败则降级为文件
            ok = False
            if can_audio and mode in ("auto", "audio"):
                ok = _send_audio_message(wx, path, user_id)
                if not ok:
                    logger.warning("语音条发送失败，降级为音频文件。")

            if not ok and mode in ("auto", "file"):
                ok = _send_audio_file(wx, path, user_id)

            if ok:
                sent_any = True
                logger.info("已向 %s 发送语音（%d 字）", user_id, len(seg))
            else:
                logger.error("语音发送失败: %s", user_id)

            _cleanup(path, conf["keep"])

            if len(segments) > 1:
                time.sleep(random.uniform(0.6, 1.4))

    return sent_any


__all__ = [
    "send_voice",
    "supports_send_audio",
    "describe_environment",
    "cleanup_temp_dir",
]
