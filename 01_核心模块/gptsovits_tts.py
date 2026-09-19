# -*- coding: utf-8 -*-
"""
gptsovits_tts.py — GPT-SoVITS 语音合成客户端（供 WeChatBot 调用）

职责：
  1. 调用 GPT-SoVITS 的 api_v2.py HTTP 接口，把文字合成为音频文件。
  2. 提供音频时长探测、文本清洗、可用性检查等辅助能力。

本模块**只负责生成音频**，不涉及微信发送逻辑；
微信发送在 voice_reply.py 中实现（语音条 / 音频文件自动降级）。

依赖：requests（WeChatBot 已自带）
"""

import os
import re
import time
import wave
import contextlib
import threading

import requests

# ---------------------------------------------------------------------------
# 默认配置（可被 voice_reply.py 或环境变量覆盖）
# ---------------------------------------------------------------------------

GPT_SOVITS_HOST = os.environ.get("GPT_SOVITS_HOST", "127.0.0.1")
GPT_SOVITS_PORT = int(os.environ.get("GPT_SOVITS_PORT", "9880"))

# GPT-SoVITS 所在目录（用于定位参考音频、权重等相对路径）
#
# 优先顺序: 环境变量 > 自动探测
# 自动探测: 本文件位于 Desktop/VoiceBot/01_核心模块/ ,
#           故 GPT-SoVITS 目录在 ../../../ 下按名称查找。
def _detect_tts_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    # 1) 同级 / 上级目录里找名字含 GPT-SoVITS 的
    for up in (here, os.path.dirname(here), os.path.dirname(os.path.dirname(here)),
               os.path.dirname(os.path.dirname(os.path.dirname(here)))):
        try:
            for name in os.listdir(up):
                if "GPT-SoVITS" in name or "GPT_SoVITS" in name:
                    p = os.path.join(up, name)
                    if os.path.isdir(p) and os.path.isdir(os.path.join(p, "GPT_SoVITS")):
                        return p
        except Exception:
            continue
    # 2) 兜底:常见位置
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(here))),
                     "GPT-SoVITS-v2pro-20250604-nvidia50")
    return d


GPT_SOVITS_DIR = os.environ.get("GPT_SOVITS_DIR") or _detect_tts_dir()

# 默认参考音频（相对 GPT_SOVITS_DIR 或绝对路径）
DEFAULT_REF_AUDIO = os.environ.get(
    "GPT_SOVITS_REF_AUDIO", os.path.join("参考音频", "my_voice_origin.wav")
)

# 参考音频对应的文本（prompt_text）。
# 说明：GPT-SoVITS 用「参考音频 + 它的文字内容」来克隆音色。
# 留空也能出声，但音色相似度和稳定性会明显下降（实测音色会"不对劲"），
# 强烈建议填写 —— 内容就是那段参考音频里实际说的话。
#
# 优先顺序: 环境变量 > reference_config.json > 空
def _load_reference_config():
    """读取 VoiceBot/reference_config.json（由 02_工具面板/set_reference.py 维护）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    p = os.path.join(root, "reference_config.json")
    if not os.path.isfile(p):
        return {}
    try:
        import json
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


_REF_CFG = _load_reference_config()


def ensure_weights(gpt_path=None, sovits_path=None, timeout=180, verbose=True,
                   force=False):
    """确保 TTS 服务加载的是**指定的权重**,不依赖 tts_infer.yaml。

    为什么需要:
        实测 GPT-SoVITS 的 api_v2.py 会把 tts_infer.yaml 里的 `version`
        **改写成 v1**(即使配置写的是 v2Pro)。若服务按那份被改写的配置
        重启,就会加载到不匹配的版本 -> 音色异常。
        所以这里在服务在线时主动调用接口把权重设成我们想要的,
        绕开那个不可靠的字段。

    ⚠️ 每次调用都会让服务**重新加载权重**(实测日志里会出现
       "Loading Text2Semantic weights from ..."),既慢又占显存,
       所以结果会缓存;同一对权重只设一次。要强制重设用 force=True。

    返回 (ok, 信息文本)
    """
    import re
    import requests

    gpt_path = gpt_path or _REF_CFG.get("gpt_weights")
    sovits_path = sovits_path or _REF_CFG.get("sovits_weights")

    if not gpt_path or not sovits_path:
        try:
            yml = os.path.join(GPT_SOVITS_DIR, "GPT_SoVITS", "configs", "tts_infer.yaml")
            with open(yml, "r", encoding="utf-8") as f:
                txt = f.read()
            m = re.search(r"t2s_weights_path:\s*(\S+)", txt)
            if m and not gpt_path:
                gpt_path = m.group(1)
            m = re.search(r"vits_weights_path:\s*(\S+)", txt)
            if m and not sovits_path:
                sovits_path = m.group(1)
        except Exception:
            pass

    if not gpt_path and not sovits_path:
        return False, "没有可用的权重路径配置"

    # 缓存:同一对权重不重复设置(避免反复重新加载)
    cache_key = (gpt_path, sovits_path)
    prev = getattr(ensure_weights, "_done", None)
    if not force and prev == cache_key:
        return True, "已设置过,跳过(%s)" % os.path.basename(gpt_path or "")

    ok_all = True
    msgs = []
    for ep, p in (("/set_gpt_weights", gpt_path),
                  ("/set_sovits_weights", sovits_path)):
        if not p:
            continue
        p = p.replace("/", os.sep) if os.sep == "\\" else p
        if not os.path.isfile(p):
            ok_all = False
            msgs.append("%s 文件不存在: %s" % (ep, os.path.basename(p)))
            continue
        try:
            url = "http://%s:%s%s" % (GPT_SOVITS_HOST, GPT_SOVITS_PORT, ep)
            r = requests.get(url, params={"weights_path": p}, timeout=timeout)
            if r.status_code == 200:
                msgs.append("%s -> %s" % (ep, os.path.basename(p)))
            else:
                ok_all = False
                msgs.append("%s 失败: %s %s" % (ep, r.status_code, r.text[:60]))
        except Exception as e:
            ok_all = False
            msgs.append("%s 异常: %s" % (ep, str(e)[:60]))

    if ok_all:
        ensure_weights._done = cache_key

    if verbose:
        for line in msgs:
            print("  [TTS] %s" % line)
    return ok_all, " | ".join(msgs)

DEFAULT_PROMPT_TEXT = os.environ.get(
    "GPT_SOVITS_PROMPT_TEXT", _REF_CFG.get("prompt_text", ""))

# 参考音频语种：zh / en / ja / ko / yue
DEFAULT_PROMPT_LANG = os.environ.get(
    "GPT_SOVITS_PROMPT_LANG", _REF_CFG.get("prompt_lang", "zh"))
# 合成文本语种
DEFAULT_TEXT_LANG = os.environ.get("GPT_SOVITS_TEXT_LANG", "zh")

# 合成参数
DEFAULT_TIMEOUT = float(os.environ.get("GPT_SOVITS_TIMEOUT", "120"))
DEFAULT_SPEED = float(os.environ.get("GPT_SOVITS_SPEED", "1.0"))


class TTSError(Exception):
    """语音合成失败。"""


# ---------------------------------------------------------------------------
# 文本清洗
# ---------------------------------------------------------------------------

# 微信文件 / 语音条不适合朗读的内容
_RE_URL = re.compile(r"https?://\S+")
_RE_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_RE_CQ_AT = re.compile(r"@\S+")
# 微信不支持的 emoji 与各类符号（保留中英文、数字、常用标点）
_RE_EMOJI = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002600-\U000027BF"
    "\U0001F900-\U0001F9FF"
    "\U00002B00-\U00002BFF"
    "\U0000FE0F"
    "\U0000200D"
    "]+",
    flags=re.UNICODE,
)


def clean_text_for_tts(text):
    """把 AI 回复整理成适合朗读的纯文本。

    去掉 markdown、URL、emoji 等朗读出来会很怪的内容。
    """
    if not text:
        return ""

    t = str(text)
    t = _RE_MARKDOWN_LINK.sub(r"\1", t)   # [文字](链接) -> 文字
    t = _RE_URL.sub("", t)                # 裸链接去掉
    t = _RE_EMOJI.sub("", t)              # emoji 去掉
    t = t.replace("*", "").replace("#", "").replace("`", "")

    # GPT-SoVITS 对换行支持一般，统一成逗号停顿
    t = re.sub(r"[\r\n]+", "，", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"[，。！？、；：]{2,}", lambda m: m.group(0)[0], t)
    t = t.strip(" ，。；：")

    return t


def _resolve_ref_audio(ref_audio_path=None):
    """把参考音频路径解析为绝对路径。

    优先顺序: 显式传入 > reference_config.json > DEFAULT_REF_AUDIO
    """
    p = ref_audio_path or _REF_CFG.get("ref_audio") or DEFAULT_REF_AUDIO
    if not p:
        return ""
    p = p.replace("/", os.sep)
    if os.path.isabs(p):
        return p
    # 相对路径:先试 VoiceBot 根目录,再试 GPT-SoVITS 目录
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cand = os.path.join(root, p)
    if os.path.isfile(cand):
        return cand
    return os.path.join(GPT_SOVITS_DIR, p)


# ---------------------------------------------------------------------------
# 音频时长
# ---------------------------------------------------------------------------

def wav_duration(path):
    """读取 wav 时长（秒）。失败返回 None。"""
    try:
        with contextlib.closing(wave.open(path, "rb")) as f:
            frames = f.getnframes()
            rate = f.getframerate()
            if rate <= 0:
                return None
            return frames / float(rate)
    except Exception:
        return None


def audio_duration(path):
    """尽可能获取音频时长。wav 直接读头，其它格式交给 ffprobe。"""
    d = wav_duration(path)
    if d is not None:
        return d

    # 非 wav：尝试 ffprobe
    try:
        import subprocess
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0 and out.stdout.strip():
            return float(out.stdout.strip())
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# TTS 调用
# ---------------------------------------------------------------------------

def synthesize(
    text,
    out_path,
    ref_audio_path=None,
    prompt_text=None,
    prompt_lang=None,
    text_lang=None,
    speed_factor=None,
    timeout=None,
    host=None,
    port=None,
    text_split_method="cut5",
    retry=2,
):
    """把 text 合成为音频并写入 out_path。成功返回 out_path，失败抛 TTSError。

    Args:
        text: 要合成的文字。
        out_path: 输出音频文件绝对路径（.wav）。
        ref_audio_path: 参考音频路径，默认用 DEFAULT_REF_AUDIO。
        prompt_text: 参考音频对应文本。
        prompt_lang: 参考音频语种，默认 zh。
        text_lang: 合成文本语种，默认 zh。
        speed_factor: 语速倍率。
        timeout: 单次请求超时（秒）。
        retry: 失败重试次数。
    """
    clean = clean_text_for_tts(text)
    if not clean:
        raise TTSError("文本清洗后为空，无需合成")

    ref_audio = _resolve_ref_audio(ref_audio_path)
    if not os.path.isfile(ref_audio):
        raise TTSError("参考音频不存在: %s" % ref_audio)

    payload = {
        "text": clean,
        "text_lang": text_lang or DEFAULT_TEXT_LANG,
        "ref_audio_path": ref_audio,
        "prompt_text": prompt_text if prompt_text is not None else DEFAULT_PROMPT_TEXT,
        "prompt_lang": prompt_lang or DEFAULT_PROMPT_LANG,
        "text_split_method": text_split_method,
        "batch_size": 1,
        "media_type": "wav",
        "streaming_mode": False,
        "speed_factor": speed_factor or DEFAULT_SPEED,
        "parallel_infer": True,
        "repetition_penalty": 1.35,
    }

    url = "http://%s:%s/tts" % (host or GPT_SOVITS_HOST, port or GPT_SOVITS_PORT)
    _timeout = timeout or DEFAULT_TIMEOUT
    last_err = None

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    for attempt in range(max(1, retry + 1)):
        try:
            resp = requests.post(url, json=payload, timeout=_timeout)
            if resp.status_code != 200:
                # GPT-SoVITS 失败时返回带 message 的 JSON
                detail = resp.text[:300]
                try:
                    detail = resp.json().get("message", detail)
                except Exception:
                    pass
                raise TTSError("TTS 接口返回 %s: %s" % (resp.status_code, detail))

            if not resp.content:
                raise TTSError("TTS 接口返回空音频")

            with open(out_path, "wb") as f:
                f.write(resp.content)

            if os.path.getsize(out_path) < 128:
                raise TTSError("生成的音频文件过小，可能合成失败")

            return out_path

        except TTSError as e:
            last_err = e
        except requests.exceptions.ConnectionError:
            last_err = TTSError(
                "无法连接 GPT-SoVITS (%s)。请先运行 api_v2.py 启动语音服务。" % url
            )
        except requests.exceptions.Timeout:
            last_err = TTSError("TTS 请求超时（%ss）" % _timeout)
        except Exception as e:
            last_err = TTSError("TTS 合成异常: %s" % e)

        if attempt < retry:
            time.sleep(1.0 + attempt)

    raise last_err if last_err else TTSError("TTS 合成失败")


def split_text_for_tts(text, max_len=120):
    """把长文本切成适合语音条的多段。

    GPT-SoVITS 单次合成过长文本容易不稳，语音条本身也不适合太长。
    按标点优先切分，返回段落列表。
    """
    clean = clean_text_for_tts(text)
    if not clean:
        return []
    if len(clean) <= max_len:
        return [clean]

    parts = []
    buf = ""
    # 在标点后断开
    for chunk in re.split(r"(?<=[。！？；，、])", clean):
        if not chunk:
            continue
        if len(buf) + len(chunk) <= max_len:
            buf += chunk
        else:
            if buf:
                parts.append(buf)
            # 单块就超长则硬切
            while len(chunk) > max_len:
                parts.append(chunk[:max_len])
                chunk = chunk[max_len:]
            buf = chunk
    if buf:
        parts.append(buf)
    return parts


# ---------------------------------------------------------------------------
# 可用性检查
# ---------------------------------------------------------------------------

def is_server_alive(host=None, port=None, timeout=3):
    """检查 GPT-SoVITS 服务是否在运行。"""
    url = "http://%s:%s/docs" % (host or GPT_SOVITS_HOST, port or GPT_SOVITS_PORT)
    try:
        r = requests.get(url, timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


def wait_for_server(host=None, port=None, timeout=180, interval=2, on_wait=None):
    """等待 GPT-SoVITS 服务就绪。返回 True/False。

    注意:这只保证**端口可响应**,不保证模型已加载完。
    要确认真正可用请用 wait_until_ready()。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_server_alive(host=host, port=port):
            return True
        if on_wait:
            try:
                on_wait()
            except Exception:
                pass
        time.sleep(interval)
    return False


def wait_until_ready(timeout=180, verbose=True, interval=3.0):
    """等待 TTS **真正可用**(端口通了 + 模型加载完 + 能合成)。

    为什么需要:
        api_v2.py 启动后端口很快就能响应,但**模型还在加载**。
        此时调 /tts 会失败。实测踩过这个坑:
        没等启动完就发送 -> "合成失败" -> 没有播放音频、没有点发送。

    返回 (ok, 信息文本)
    """
    t0 = time.time()
    last = "未开始"
    while time.time() - t0 < timeout:
        if is_server_alive():
            # 端口通了:再确认模型真能合成(用极短文本探一次)
            ok, info = _probe_synthesis()
            if ok:
                # 顺带把权重设成配置的那对
                try:
                    ensure_weights(verbose=False)
                except Exception:
                    pass
                if verbose:
                    print("  [TTS] 已就绪(等待 %.0f 秒)" % (time.time() - t0))
                return True, "ready in %.0fs" % (time.time() - t0)
            last = info
        else:
            last = "端口未响应"
        time.sleep(interval)
    return False, "等待超时(%.0f 秒),最后状态: %s" % (timeout, last)


def _probe_synthesis(timeout=60):
    """试合成一小段,确认模型确实加载完。返回 (ok, 信息)。"""
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "_tts_probe.wav")
    try:
        synthesize("测试", tmp, timeout=timeout)
        ok = os.path.isfile(tmp) and os.path.getsize(tmp) > 1000
        return ok, ("ok" if ok else "输出文件异常")
    except Exception as e:
        return False, str(e)[:70]
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


def set_weights(gpt_path=None, sovits_path=None, host=None, port=None, timeout=60):
    """切换 GPT / SoVITS 权重（可选）。路径相对 GPT_SOVITS_DIR。"""
    base = "http://%s:%s" % (host or GPT_SOVITS_HOST, port or GPT_SOVITS_PORT)
    result = {}
    for endpoint, path in (
        ("/set_gpt_weights", gpt_path),
        ("/set_sovits_weights", sovits_path),
    ):
        if not path:
            continue
        if not os.path.isabs(path):
            path = os.path.join(GPT_SOVITS_DIR, path)
        try:
            r = requests.get(base + endpoint, params={"weights_path": path}, timeout=timeout)
            result[endpoint] = r.text[:200]
        except Exception as e:
            result[endpoint] = "error: %s" % e
    return result


__all__ = [
    "TTSError",
    "synthesize",
    "clean_text_for_tts",
    "split_text_for_tts",
    "audio_duration",
    "wav_duration",
    "is_server_alive",
    "wait_for_server",
    "set_weights",
]
