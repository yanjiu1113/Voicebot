# -*- coding: utf-8 -*-
"""
config_tool.py —— 统一配置面板(GUI)

可修改:
  1. WeChatBot 的 AI 模型(写回 WeChatBot 的 config.py)
  2. TTS 的 GPT / SoVITS 权重(写回 tts_infer.yaml,并可在服务运行时热切换)
  3. TTS 参考音频
  4. 音频留存限额
  5. 语音发送参数(会话行号、角色人设等)

所有修改都会**先备份**再写入。
"""

import json
import os
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # VoiceBot/
DESKTOP = os.path.dirname(ROOT)                    # Desktop/
PROJECT = os.path.join(DESKTOP, "WeChatBot_WXAUTO_SE-3.28")
TTS_DIR = os.path.join(DESKTOP, "GPT-SoVITS-v2pro-20250604-nvidia50")
TTS_YAML = os.path.join(TTS_DIR, "GPT_SoVITS", "configs", "tts_infer.yaml")
WCB_CONFIG = os.path.join(PROJECT, "config.py")
VB_CONFIG = os.path.join(ROOT, "voice_bot_config.json")
RET_CONFIG = os.path.join(ROOT, "04_音频留存", "retention_config.json")
BACKUP_DIR = os.path.join(ROOT, "05_文档", "配置备份")

TTS_API = "http://127.0.0.1:9880"

# 默认候选模型(选了"自定义/未知服务商"时用)
AI_MODELS = [
    "deepseek-v4-pro",
    "deepseek-flash",
    "deepseek-chat",
    "deepseek-reasoner",
]

# 常见服务商(选了会自动填 BASE_URL 与该服务商的**正确模型名格式**)
#
# ⚠️ 各家的模型名规则不同,混用会报 "The supported API model names are ..."
#     OpenRouter   ->  deepseek/deepseek-v4-pro     (带 vendor 前缀)
#     DeepSeek官方  ->  deepseek-v4-pro              (不带前缀)
#     硅基流动      ->  deepseek-ai/DeepSeek-V3
PROVIDERS = [
    ("OpenRouter", "https://openrouter.ai/api/v1",
     ["deepseek/deepseek-v4-flash", "deepseek/deepseek-v3.2",
      "deepseek/deepseek-v4-pro", "deepseek/deepseek-chat-v3.1",
      "deepseek/deepseek-r1-0528"]),
    ("DeepSeek 官方", "https://api.deepseek.com",
     ["deepseek-v4-pro", "deepseek-flash", "deepseek-chat",
      "deepseek-reasoner"]),
    ("硅基流动", "https://api.siliconflow.cn/v1",
     ["deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1"]),
    ("月之暗面 Moonshot", "https://api.moonshot.cn/v1",
     ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"]),
    ("阿里百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1",
     ["qwen-plus", "qwen-max", "qwen-turbo"]),
    ("智谱 GLM", "https://open.bigmodel.cn/api/paas/v4",
     ["glm-4-plus", "glm-4-flash", "glm-4-air"]),
    ("自定义 / 第三方中转", "", []),
]


def validate_model_for_provider(base_url, model):
    """检查模型名与 BASE_URL 是否匹配。返回 (是否可疑, 提示文本)。

    这是踩过的坑:把 OpenRouter 的 'deepseek/xxx' 用在实际是
    api.deepseek.com 的端点上,导致所有模型都失败。
    """
    b = (base_url or "").lower()
    m = (model or "").strip()
    if not b or not m:
        return False, ""

    has_slash = "/" in m

    # OpenRouter 需要带 vendor 前缀
    if "openrouter.ai" in b:
        if not has_slash:
            return True, ("OpenRouter 的模型名通常需要带厂商前缀,例如 "
                          "'deepseek/deepseek-v4-pro'。你填的 %r 可能不被接受。" % m)
        return False, ""

    # DeepSeek 官方不需要前缀
    if "api.deepseek.com" in b:
        if has_slash:
            return True, ("DeepSeek 官方 API 的模型名**不带**厂商前缀,例如 "
                          "'deepseek-v4-pro'。你填的 %r 会报 "
                          "\"The supported API model names are...\"。" % m)
        return False, ""

    # 硅基流动需要 'vendor/Model' 形式
    if "siliconflow" in b:
        if not has_slash:
            return True, ("硅基流动的模型名形如 'deepseek-ai/DeepSeek-V3',"
                          "你填的 %r 可能不被接受。" % m)
        return False, ""

    return False, ""


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def backup(path, tag=""):
    """备份文件到 05_文档/配置备份/。返回备份路径。"""
    if not os.path.isfile(path):
        return None
    _ensure_backup_dir()
    base = os.path.basename(path)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, "%s%s.%s.bak" % (base, ("_" + tag) if tag else "", stamp))
    try:
        shutil.copy2(path, dst)
        return dst
    except Exception as e:
        print("备份失败:", e)
        return None


def scan_weights():
    """扫描可用的 GPT / SoVITS 权重。返回 (gpt_list, sovits_list)。"""
    gpt, sov = [], []
    if os.path.isdir(TTS_DIR):
        for d in sorted(os.listdir(TTS_DIR)):
            full = os.path.join(TTS_DIR, d)
            if not os.path.isdir(full):
                continue
            if d.startswith("GPT_weights"):
                for f in sorted(os.listdir(full)):
                    if f.endswith((".ckpt", ".pth")):
                        gpt.append((d, f))
            elif d.startswith("SoVITS_weights"):
                for f in sorted(os.listdir(full)):
                    if f.endswith((".ckpt", ".pth")):
                        sov.append((d, f))
    return gpt, sov


AUDIO_EXT = (".wav", ".mp3", ".flac", ".m4a", ".ogg")
REF_DIR = os.path.join(ROOT, "参考音频")
REF_CONFIG = os.path.join(ROOT, "reference_config.json")


def scan_ref_audio():
    """扫描可用参考音频:优先 VoiceBot/参考音频/,其次 GPT-SoVITS/参考音频/。
    返回 [(文件名, 绝对路径, 时长秒或 None), ...]"""
    out = []
    seen = set()
    for d in (REF_DIR, os.path.join(TTS_DIR, "参考音频")):
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.lower().endswith(AUDIO_EXT) or f in seen:
                continue
            seen.add(f)
            p = os.path.join(d, f)
            out.append((f, p, _wav_duration(p)))
    return out


def _wav_duration(path):
    """读 wav 时长(秒);非 wav 或失败返回 None。"""
    try:
        import wave
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return None


def read_ref_config():
    """读 reference_config.json。"""
    if not os.path.isfile(REF_CONFIG):
        return {}
    try:
        with open(REF_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def write_ref_config(audio_path, prompt_text, lang="zh"):
    """写 reference_config.json(存相对 VoiceBot 根目录的路径,便于移植)。"""
    stored = audio_path
    if audio_path and os.path.isabs(audio_path):
        try:
            rel = os.path.relpath(audio_path, ROOT)
            if not rel.startswith(".."):
                stored = rel.replace("\\", "/")
        except Exception:
            pass
    cfg = {"ref_audio": stored, "prompt_text": prompt_text or "",
           "prompt_lang": lang or "zh"}
    with open(REF_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


def read_yaml_custom():
    """读 tts_infer.yaml 的 custom 段(简易解析,不引入 yaml 依赖)。"""
    data = {}
    if not os.path.isfile(TTS_YAML):
        return data
    with open(TTS_YAML, "r", encoding="utf-8") as f:
        lines = f.readlines()
    in_custom = False
    for ln in lines:
        if ln.startswith("custom:"):
            in_custom = True
            continue
        if in_custom:
            if ln and not ln[0].isspace():
                break
            if ":" in ln:
                k, v = ln.split(":", 1)
                data[k.strip()] = v.strip()
    return data


def write_yaml_custom(gpt_path=None, sovits_path=None, version=None):
    """更新 tts_infer.yaml 的 custom 段(保留其他内容)。"""
    if not os.path.isfile(TTS_YAML):
        raise FileNotFoundError(TTS_YAML)
    with open(TTS_YAML, "r", encoding="utf-8") as f:
        lines = f.readlines()

    out = []
    in_custom = False
    seen = {"t2s": False, "vits": False, "version": False}
    for ln in lines:
        if ln.startswith("custom:"):
            in_custom = True
            out.append(ln)
            continue
        if in_custom:
            if ln and not ln[0].isspace():
                # custom 段结束:补上缺失的键
                for key, val, flag in (("t2s_weights_path", gpt_path, "t2s"),
                                       ("vits_weights_path", sovits_path, "vits"),
                                       ("version", version, "version")):
                    if val and not seen[flag]:
                        out.append("  %s: %s\n" % (key, val))
                in_custom = False
                out.append(ln)
                continue
            s = ln.strip()
            if s.startswith("t2s_weights_path") and gpt_path:
                out.append("  t2s_weights_path: %s\n" % gpt_path)
                seen["t2s"] = True
                continue
            if s.startswith("vits_weights_path") and sovits_path:
                out.append("  vits_weights_path: %s\n" % sovits_path)
                seen["vits"] = True
                continue
            if s.startswith("version") and version:
                out.append("  version: %s\n" % version)
                seen["version"] = True
                continue
            out.append(ln)
            continue
        out.append(ln)

    with open(TTS_YAML, "w", encoding="utf-8") as f:
        f.writelines(out)


def tts_online():
    try:
        import requests
        r = requests.get(TTS_API + "/openapi.json", timeout=3)
        return r.status_code < 500
    except Exception:
        return False


def hot_swap(gpt_abs=None, sovits_abs=None):
    """服务在线时热切换权重,免重启。返回结果文本。"""
    import requests
    msgs = []
    for ep, p in (("/set_gpt_weights", gpt_abs), ("/set_sovits_weights", sovits_abs)):
        if not p:
            continue
        try:
            r = requests.get(TTS_API + ep, params={"weights_path": p}, timeout=120)
            msgs.append("%s -> %s %s" % (ep, r.status_code, r.text[:60]))
        except Exception as e:
            msgs.append("%s 失败: %s" % (ep, str(e)[:60]))
    return "\n".join(msgs)


def read_wcb_config(key):
    """从 WeChatBot/config.py 读取某个配置项的值。"""
    if not os.path.isfile(WCB_CONFIG):
        return None
    try:
        with open(WCB_CONFIG, "r", encoding="utf-8") as f:
            for ln in f:
                s = ln.strip()
                if s.startswith(key) and "=" in s and not s.startswith("#"):
                    return s.split("=", 1)[1].strip().strip("'\"")
    except Exception:
        pass
    return None


def write_wcb_config(pairs):
    """把 {KEY: value} 写回 WeChatBot/config.py(只改匹配的行,保留注释)。

    返回实际修改的行数。不存在的键不会新增(避免破坏文件结构)。
    """
    if not os.path.isfile(WCB_CONFIG):
        return 0
    with open(WCB_CONFIG, "r", encoding="utf-8") as f:
        lines = f.readlines()
    n = 0
    for i, ln in enumerate(lines):
        st = ln.strip()
        if st.startswith("#") or "=" not in st:
            continue
        name = st.split("=", 1)[0].strip()
        if name in pairs:
            comment = ""
            # 保留行尾注释(但 key 本身可能含 #,只在值之后找)
            val_part = ln.split("=", 1)[1]
            if "#" in val_part:
                comment = "  #" + val_part.split("#", 1)[1].rstrip("\n")
            lines[i] = "%s = '%s'%s\n" % (name, pairs[name], comment)
            n += 1
    with open(WCB_CONFIG, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return n


def read_wcb_model():
    return read_wcb_config("MODEL")


def write_wcb_model(model):
    """把 MODEL = 'xxx' 写回 WeChatBot/config.py(只改 MODEL 行)。"""
    if not os.path.isfile(WCB_CONFIG):
        return 0
    with open(WCB_CONFIG, "r", encoding="utf-8") as f:
        lines = f.readlines()
    n = 0
    for i, ln in enumerate(lines):
        if ln.strip().startswith("MODEL") and "=" in ln:
            # 保留行尾注释
            comment = ""
            if "#" in ln:
                comment = "  #" + ln.split("#", 1)[1].rstrip("\n")
            lines[i] = "MODEL = '%s'%s\n" % (model, comment)
            n += 1
    with open(WCB_CONFIG, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return n


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class ConfigTool:
    def __init__(self, root):
        self.root = root
        root.title("VoiceBot 配置面板")
        root.geometry("880x700")
        root.configure(bg="#f4f6f8")

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=12, pady=10)

        self._build_ai_tab(nb)
        self._build_tts_tab(nb)
        self._build_retention_tab(nb)
        self._build_log_tab(nb)
        self._build_about_tab(nb)

        self.load_all()

    # ---------------- 日志区 ----------------
    def _log(self, s):
        self.logbox.insert("end", s + "\n")
        self.logbox.see("end")
        self.root.update_idletasks()

    def _build_log_tab(self, nb):
        f = tk.Frame(nb, bg="#f4f6f8")
        nb.add(f, text="  运行日志  ")
        tk.Label(f, text="操作日志", bg="#f4f6f8", font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
        self.logbox = tk.Text(f, font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
                              insertbackground="#d4d4d4", wrap="word")
        self.logbox.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ---------------- AI 模型 ----------------
    def _build_ai_tab(self, nb):
        f = tk.Frame(nb, bg="#f4f6f8")
        nb.add(f, text="  AI 模型  ")

        tk.Label(f, text="WeChatBot 的 AI 接口", bg="#f4f6f8",
                 font=("Microsoft YaHei", 12, "bold")).pack(anchor="w", padx=14, pady=(14, 2))
        tk.Label(f, text="写入 %s" % WCB_CONFIG, bg="#f4f6f8", fg="#7a8899",
                 font=("Microsoft YaHei", 8)).pack(anchor="w", padx=14)

        body = tk.Frame(f, bg="#f4f6f8")
        body.pack(fill="x", padx=14, pady=12)

        # 服务商
        tk.Label(body, text="服务商 :", bg="#f4f6f8", width=10, anchor="w",
                 font=("Microsoft YaHei", 10)).grid(row=0, column=0, pady=5, sticky="w")
        self.prov_var = tk.StringVar()
        self.prov_combo = ttk.Combobox(body, textvariable=self.prov_var, width=24,
                                       values=[p[0] for p in PROVIDERS], state="readonly")
        self.prov_combo.grid(row=0, column=1, padx=8, sticky="w")
        self.prov_combo.bind("<<ComboboxSelected>>", self._on_provider)
        tk.Label(body, text="选择后会自动填下面的接口地址", bg="#f4f6f8", fg="#8a98a8",
                 font=("Microsoft YaHei", 8)).grid(row=0, column=2, sticky="w")

        # 接口地址
        tk.Label(body, text="接口地址 :", bg="#f4f6f8", width=10, anchor="w",
                 font=("Microsoft YaHei", 10)).grid(row=1, column=0, pady=5, sticky="w")
        self.base_var = tk.StringVar()
        tk.Entry(body, textvariable=self.base_var, width=52,
                 font=("Consolas", 9)).grid(row=1, column=1, columnspan=2, padx=8, sticky="w")
        tk.Label(body, text="→ DEEPSEEK_BASE_URL", bg="#f4f6f8", fg="#8a98a8",
                 font=("Microsoft YaHei", 8)).grid(row=1, column=3, padx=6, sticky="w")

        # API Key
        tk.Label(body, text="API Key :", bg="#f4f6f8", width=10, anchor="w",
                 font=("Microsoft YaHei", 10)).grid(row=2, column=0, pady=5, sticky="w")
        self.key_var = tk.StringVar()
        self.key_entry = tk.Entry(body, textvariable=self.key_var, width=52,
                                  font=("Consolas", 9), show="*")
        self.key_entry.grid(row=2, column=1, columnspan=2, padx=8, sticky="w")
        self.show_key = tk.BooleanVar(value=False)
        tk.Checkbutton(body, text="显示", variable=self.show_key,
                       command=lambda: self.key_entry.config(show="" if self.show_key.get() else "*"),
                       bg="#f4f6f8", font=("Microsoft YaHei", 8)).grid(row=2, column=3, padx=6, sticky="w")
        tk.Label(body, text="→ DEEPSEEK_API_KEY", bg="#f4f6f8", fg="#8a98a8",
                 font=("Microsoft YaHei", 8)).grid(row=3, column=3, padx=6, sticky="w")

        # 模型
        tk.Label(body, text="模型 :", bg="#f4f6f8", width=10, anchor="w",
                 font=("Microsoft YaHei", 10)).grid(row=4, column=0, pady=(10, 5), sticky="w")
        self.ai_var = tk.StringVar()
        self.ai_combo = ttk.Combobox(body, textvariable=self.ai_var, width=49, values=AI_MODELS)
        self.ai_combo.grid(row=4, column=1, columnspan=2, padx=8, sticky="w")
        tk.Label(body, text="→ MODEL(会同步改两处)", bg="#f4f6f8", fg="#8a98a8",
                 font=("Microsoft YaHei", 8)).grid(row=4, column=3, padx=6, sticky="w")

        self.ai_current = tk.Label(f, text="", bg="#f4f6f8", fg="#2fa36b",
                                   font=("Microsoft YaHei", 9))
        self.ai_current.pack(anchor="w", padx=14, pady=(4, 2))

        br = tk.Frame(f, bg="#f4f6f8"); br.pack(pady=12)
        tk.Button(br, text="保存(自动备份)", command=self.save_ai,
                  bg="#2fa36b", fg="white", relief="flat", cursor="hand2",
                  font=("Microsoft YaHei", 10), height=2, width=20).pack(side="left", padx=6)
        tk.Button(br, text="刷新", command=self.load_ai,
                  bg="#4a90d9", fg="white", relief="flat", cursor="hand2",
                  font=("Microsoft YaHei", 10), height=2, width=10).pack(side="left", padx=6)
        tk.Button(br, text="测试连通", command=self.test_ai,
                  bg="#8a6fd9", fg="white", relief="flat", cursor="hand2",
                  font=("Microsoft YaHei", 10), height=2, width=12).pack(side="left", padx=6)

        tk.Label(f, text="提示:模型失效时 voice_bot 会自动回退到备用模型;\n"
                         "     v4-flash 是推理模型,MAX_TOKEN 需 ≥300(当前 2000,足够)。\n"
                         "     Key 保存在 config.py 明文,注意不要外传该文件。",
                 bg="#f4f6f8", fg="#8a98a8", font=("Microsoft YaHei", 9),
                 justify="left").pack(anchor="w", padx=14)

    def _on_provider(self, evt=None):
        name = self.prov_var.get()
        for pname, base, models in PROVIDERS:
            if pname == name:
                if base:
                    self.base_var.set(base)
                if models:
                    self.ai_combo["values"] = models
                break

    # ---------------- TTS ----------------
    def _build_tts_tab(self, nb):
        f = tk.Frame(nb, bg="#f4f6f8")
        nb.add(f, text="  TTS 音色  ")

        tk.Label(f, text="GPT-SoVITS 权重", bg="#f4f6f8",
                 font=("Microsoft YaHei", 12, "bold")).pack(anchor="w", padx=14, pady=(14, 2))
        tk.Label(f, text="写入 %s" % TTS_YAML, bg="#f4f6f8", fg="#7a8899",
                 font=("Microsoft YaHei", 8)).pack(anchor="w", padx=14)

        # GPT
        r1 = tk.Frame(f, bg="#f4f6f8"); r1.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(r1, text="GPT 权重 :", bg="#f4f6f8", width=10, anchor="w",
                 font=("Microsoft YaHei", 10)).pack(side="left")
        self.gpt_var = tk.StringVar()
        self.gpt_combo = ttk.Combobox(r1, textvariable=self.gpt_var, width=52)
        self.gpt_combo.pack(side="left", padx=6)

        # SoVITS
        r2 = tk.Frame(f, bg="#f4f6f8"); r2.pack(fill="x", padx=14, pady=4)
        tk.Label(r2, text="SoVITS  :", bg="#f4f6f8", width=10, anchor="w",
                 font=("Microsoft YaHei", 10)).pack(side="left")
        self.sov_var = tk.StringVar()
        self.sov_combo = ttk.Combobox(r2, textvariable=self.sov_var, width=52)
        self.sov_combo.pack(side="left", padx=6)

        # version
        r3 = tk.Frame(f, bg="#f4f6f8"); r3.pack(fill="x", padx=14, pady=4)
        tk.Label(r3, text="version  :", bg="#f4f6f8", width=10, anchor="w",
                 font=("Microsoft YaHei", 10)).pack(side="left")
        self.ver_var = tk.StringVar(value="v2Pro")
        ttk.Combobox(r3, textvariable=self.ver_var, width=14,
                     values=["v1", "v2", "v2Pro", "v2ProPlus", "v3", "v4"]).pack(side="left", padx=6)
        tk.Label(r3, text="(与权重的训练版本一致,通常 v2Pro)", bg="#f4f6f8",
                 fg="#7a8899", font=("Microsoft YaHei", 8)).pack(side="left")

        # ---- 参考音频(决定音色,极重要) ----
        sep = tk.Frame(f, bg="#dfe5ec", height=1)
        sep.pack(fill="x", padx=14, pady=(14, 0))
        tk.Label(f, text="参考音频(决定音色)", bg="#f4f6f8",
                 font=("Microsoft YaHei", 12, "bold")).pack(anchor="w", padx=14, pady=(10, 2))
        tk.Label(f, text="GPT-SoVITS 靠「参考音频 + 该音频的文字」克隆音色;\n"
                         "文字留空或与音频不对应,音色都会明显失真。",
                 bg="#f4f6f8", fg="#7a8899", justify="left",
                 font=("Microsoft YaHei", 8)).pack(anchor="w", padx=14)

        r4 = tk.Frame(f, bg="#f4f6f8"); r4.pack(fill="x", padx=14, pady=(10, 2))
        tk.Label(r4, text="参考音频 :", bg="#f4f6f8", width=10, anchor="w",
                 font=("Microsoft YaHei", 10)).pack(side="left")
        self.ref_var = tk.StringVar()
        self.ref_combo = ttk.Combobox(r4, textvariable=self.ref_var, width=48,
                                      state="readonly")
        self.ref_combo.pack(side="left", padx=6)
        self.ref_combo.bind("<<ComboboxSelected>>", self._on_ref_selected)
        tk.Button(r4, text="打开目录", command=self._open_ref_dir,
                  bg="#8a8f98", fg="white", relief="flat", cursor="hand2",
                  font=("Microsoft YaHei", 8)).pack(side="left", padx=4)

        self.ref_info = tk.Label(f, text="", bg="#f4f6f8", fg="#4a90d9",
                                 font=("Microsoft YaHei", 9))
        self.ref_info.pack(anchor="w", padx=14, pady=(4, 2))

        r5 = tk.Frame(f, bg="#f4f6f8"); r5.pack(fill="x", padx=14, pady=(4, 4))
        tk.Label(r5, text="对应文字 :", bg="#f4f6f8", width=10, anchor="w",
                 font=("Microsoft YaHei", 10)).pack(side="left", anchor="n")
        self.ref_text = tk.Text(r5, height=3, width=58, font=("Microsoft YaHei", 9),
                                wrap="word", relief="solid", borderwidth=1)
        self.ref_text.pack(side="left", padx=6)
        tk.Button(r5, text="用文件名\n自动填入", command=self._ref_text_from_name,
                  bg="#4a90d9", fg="white", relief="flat", cursor="hand2",
                  font=("Microsoft YaHei", 8)).pack(side="left", padx=4)

        self.tts_status = tk.Label(f, text="", bg="#f4f6f8", font=("Microsoft YaHei", 9))
        self.tts_status.pack(anchor="w", padx=14, pady=(8, 2))

        br = tk.Frame(f, bg="#f4f6f8"); br.pack(pady=12)
        tk.Button(br, text="保存并热切换(免重启)", command=self.save_tts,
                  bg="#2fa36b", fg="white", relief="flat", cursor="hand2",
                  font=("Microsoft YaHei", 10), height=2, width=24).pack(side="left", padx=6)
        tk.Button(br, text="刷新列表", command=self.load_tts,
                  bg="#4a90d9", fg="white", relief="flat", cursor="hand2",
                  font=("Microsoft YaHei", 10), height=2, width=12).pack(side="left", padx=6)
        tk.Button(br, text="试听一句", command=self.test_tts,
                  bg="#8a6fd9", fg="white", relief="flat", cursor="hand2",
                  font=("Microsoft YaHei", 10), height=2, width=12).pack(side="left", padx=6)

    # ---- 参考音频相关回调 ----
    def _ref_list(self):
        """返回 [(显示名, 绝对路径)]"""
        out = []
        for name, path, dur in scan_ref_audio():
            tag = ""
            if dur is None:
                tag = "  [时长?]"
            elif dur < 3.0 or dur > 10.0:
                tag = "  [⚠ %.1fs 超出3~10s]" % dur
            else:
                tag = "  [%.1fs ✓]" % dur
            out.append((name + tag, path, name, dur))
        return out

    def _open_ref_dir(self):
        try:
            os.makedirs(REF_DIR, exist_ok=True)
            os.startfile(REF_DIR)
            self._log("[参考音频] 已打开目录: %s" % REF_DIR)
            self._log("           把音频丢进去,文件名写成它说的话,然后点「刷新列表」")
        except Exception as e:
            self._log("[参考音频] 打开目录失败: %s" % e)

    def _on_ref_selected(self, evt=None):
        """选中参考音频后:显示时长,并尝试从文件名填入文字。"""
        sel = self.ref_var.get()
        path = self._ref_path_by_label(sel)
        if not path:
            return
        dur = _wav_duration(path)
        name = os.path.basename(path)
        if dur is None:
            self.ref_info.config(text="时长: 非 wav 或无头信息(需自行确认在 3~10 秒内)",
                                 fg="#d99a3d")
        elif dur < 3.0 or dur > 10.0:
            self.ref_info.config(
                text="⚠ 时长 %.2f 秒 —— 超出 3~10 秒范围,合成会直接失败!" % dur,
                fg="#d9534f")
        else:
            self.ref_info.config(text="✓ 时长 %.2f 秒(符合 3~10 秒)" % dur, fg="#2fa36b")

        # 若文字框为空,自动用文件名填
        cur = self.ref_text.get("1.0", "end").strip()
        if not cur:
            self.ref_text.delete("1.0", "end")
            self.ref_text.insert("1.0", os.path.splitext(name)[0])

    def _ref_path_by_label(self, label):
        for disp, path, name, dur in self._ref_list():
            if disp == label:
                return path
        # 兼容:label 可能是纯文件名
        for disp, path, name, dur in self._ref_list():
            if name == label:
                return path
        return None

    def _ref_text_from_name(self):
        label = self.ref_var.get()
        path = self._ref_path_by_label(label)
        if not path:
            self._log("[参考音频] 请先选择一个参考音频")
            return
        name = os.path.splitext(os.path.basename(path))[0]
        self.ref_text.delete("1.0", "end")
        self.ref_text.insert("1.0", name)
        self._log("[参考音频] 已用文件名填入文字(%d 字)" % len(name))

    # ---------------- 留存 ----------------
    def _build_retention_tab(self, nb):
        f = tk.Frame(nb, bg="#f4f6f8")
        nb.add(f, text="  音频留存  ")

        tk.Label(f, text="音频/截图自动清理限额", bg="#f4f6f8",
                 font=("Microsoft YaHei", 12, "bold")).pack(anchor="w", padx=14, pady=(14, 2))
        tk.Label(f, text="超出任一限额时,自动删除最旧的文件", bg="#f4f6f8",
                 fg="#7a8899", font=("Microsoft YaHei", 9)).pack(anchor="w", padx=14)

        body = tk.Frame(f, bg="#f4f6f8"); body.pack(fill="x", padx=14, pady=12)
        self.ret_vars = {}
        fields = [("max_files", "最大文件数", "超过则从最旧的删"),
                  ("max_total_mb", "最大总占用(MB)", "超过则从最旧的删"),
                  ("max_age_days", "最长保留天数", "超龄直接删"),
                  ("min_age_hours", "保护期(小时)", "此时间内绝不删")]
        for i, (k, label, hint) in enumerate(fields):
            tk.Label(body, text=label, bg="#f4f6f8", width=16, anchor="w",
                     font=("Microsoft YaHei", 10)).grid(row=i, column=0, pady=4, sticky="w")
            v = tk.StringVar()
            self.ret_vars[k] = v
            tk.Entry(body, textvariable=v, width=12,
                     font=("Consolas", 10)).grid(row=i, column=1, padx=8, sticky="w")
            tk.Label(body, text=hint, bg="#f4f6f8", fg="#8a98a8",
                     font=("Microsoft YaHei", 8)).grid(row=i, column=2, sticky="w")

        self.ret_status = tk.Label(f, text="", bg="#f4f6f8", font=("Microsoft YaHei", 9))
        self.ret_status.pack(anchor="w", padx=14, pady=(6, 2))

        br = tk.Frame(f, bg="#f4f6f8"); br.pack(pady=12)
        tk.Button(br, text="保存限额", command=self.save_retention,
                  bg="#2fa36b", fg="white", relief="flat", cursor="hand2",
                  font=("Microsoft YaHei", 10), height=2, width=14).pack(side="left", padx=5)
        tk.Button(br, text="查看现状", command=self.report_retention,
                  bg="#4a90d9", fg="white", relief="flat", cursor="hand2",
                  font=("Microsoft YaHei", 10), height=2, width=14).pack(side="left", padx=5)
        tk.Button(br, text="立即清理", command=self.clean_retention,
                  bg="#d9534f", fg="white", relief="flat", cursor="hand2",
                  font=("Microsoft YaHei", 10), height=2, width=14).pack(side="left", padx=5)

    # ---------------- 关于 ----------------
    def _build_about_tab(self, nb):
        f = tk.Frame(nb, bg="#f4f6f8")
        nb.add(f, text="  说明  ")
        txt = tk.Text(f, font=("Microsoft YaHei", 9), bg="#ffffff", fg="#333",
                      wrap="word", relief="flat")
        txt.pack(fill="both", expand=True, padx=12, pady=12)
        txt.insert("end", """VoiceBot 配置面板

【AI 模型】
  修改 WeChatBot 使用的对话模型,写入它的 config.py。
  保存前会自动备份到 05_文档\\配置备份\\。
  注意:voice_bot.py 自身也支持模型自动回退,即使这里配的模型失效也能工作。

【TTS 音色】
  修改 GPT-SoVITS 的两个权重(GPT + SoVITS)以及 version。
  写入 tts_infer.yaml,并在服务运行时**热切换**(免重启)。
  version 必须与权重的训练版本一致 —— 当前权重都是 v2Pro。
  ⚠️ 注意:与 GPT 权重不是一一对应也能工作,但音色会不一致,
     建议按同一角色的配对选择(如 Elysia_v2 + Elysia_v2_e24)。

【音频留存】
  自动清理合成的 wav 与发送截图,防止占满磁盘。
  三重限额:文件数 / 总大小 / 保留天数,任一超出即从最旧的删。
  24 小时内生成的文件受保护,不会误删正在用的。

【目录结构】
  01_核心模块   voice_bot / wx_voice_sender / session_guard / gptsovits_tts / wxauto_bootstrap
  02_工具面板   本配置面板、启动器
  03_诊断测试   各种自检脚本(线缆直通、录音状态、会话检测等)
  04_音频留存   audio_retention.py + 音频文件
  05_文档       使用说明、配置备份
""")
        txt.config(state="disabled")

    # ---------------- 加载 ----------------
    def load_all(self):
        self.load_ai()
        self.load_tts()
        self.load_retention()

    def load_ai(self):
        cur = read_wcb_config("MODEL")
        base = read_wcb_config("DEEPSEEK_BASE_URL") or ""
        key = read_wcb_config("DEEPSEEK_API_KEY") or ""
        if cur:
            if cur not in AI_MODELS:
                self.ai_combo["values"] = [cur] + AI_MODELS
            self.ai_var.set(cur)
        self.base_var.set(base)
        self.key_var.set(key)

        # 反推服务商
        pname = "自定义"
        for p, b, _ in PROVIDERS:
            if b and base.rstrip("/") == b.rstrip("/"):
                pname = p
                break
        self.prov_var.set(pname)

        masked = (key[:10] + "…" + key[-4:]) if len(key) > 16 else ("(空)" if not key else key)
        self.ai_current.config(
            text="当前: %s  |  %s  |  Key %s" % (cur or "?", base or "?", masked))

    def save_ai(self):
        model = self.ai_var.get().strip()
        base = self.base_var.get().strip()
        key = self.key_var.get().strip()
        if not model:
            messagebox.showwarning("提示", "请先选择或输入模型 ID")
            return
        if not base:
            messagebox.showwarning("提示", "请填写接口地址")
            return
        if not key:
            if not messagebox.askyesno("Key 为空", "API Key 为空,确定继续?(大多数服务商会拒绝)"):
                return

        # 模型名与服务商是否匹配(踩过的坑:OpenRouter 风格用到 DeepSeek 官方)
        sus, msg = validate_model_for_provider(base, model)
        if sus:
            if not messagebox.askyesno(
                    "模型名可能不匹配",
                    "%s\n\n仍然保存吗?\n\n(如果保存后报「所有模型均不可用」,\n"
                    " 就是这个原因)" % msg):
                return

        b = backup(WCB_CONFIG, "before_ai")
        try:
            n = write_wcb_config({
                "DEEPSEEK_API_KEY": key,
                "DEEPSEEK_BASE_URL": base,
                "MODEL": model,
            })
        except Exception as e:
            messagebox.showerror("失败", str(e))
            return

        self._log("[AI] 已写入 %d 行:" % n)
        self._log("     DEEPSEEK_BASE_URL = %s" % base)
        self._log("     DEEPSEEK_API_KEY  = %s…%s" % (key[:10], key[-4:] if len(key) > 14 else ""))
        self._log("     MODEL             = %s" % model)
        if b:
            self._log("     备份: %s" % os.path.basename(b))
        self.load_ai()
        messagebox.showinfo("完成", "已保存:\n\n模型: %s\n服务商: %s\n\n写入 %d 行。\n\n"
                                    "WeChatBot 需重启后生效;\n"
                                    "VoiceBot(voice_bot.py)也会读取同样配置。" % (model, base, n))

    def test_ai(self):
        """用当前填写的配置试调一次 AI。"""
        base = self.base_var.get().strip()
        key = self.key_var.get().strip()
        model = self.ai_var.get().strip()

        def work():
            try:
                from openai import OpenAI
                self.root.after(0, self._log, "[测试] 连接 %s  模型 %s …" % (base, model))
                c = OpenAI(api_key=key or "dummy", base_url=base)

                # 1) 先列服务商真实支持的模型(避免猜名字)
                try:
                    ids = [x.id for x in c.models.list().data]
                    if ids:
                        self.root.after(0, self._log,
                                        "[测试] 该服务商支持 %d 个模型:" % len(ids))
                        for i in range(0, min(len(ids), 30), 3):
                            self.root.after(0, self._log,
                                            "        " + "  ".join(ids[i:i + 3]))
                        if model not in ids:
                            self.root.after(0, self._log,
                                            "[测试] ⚠ 你填的 %r 不在上面列表里!" % model)
                except Exception as e:
                    self.root.after(0, self._log,
                                    "[测试] (无法列出模型: %s)" % str(e)[:70])

                # 2) 试调
                t0 = time.time()
                r = c.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "只回复:正常"}],
                    max_tokens=64, temperature=0.5)
                txt = (r.choices[0].message.content or "").strip()
                self.root.after(0, self._log,
                                "[测试] ✓ 成功 (%.1fs): %r" % (time.time() - t0, txt[:40] or "(空回复)"))
                if not txt:
                    self.root.after(0, self._log,
                                    "[测试] ⚠ 返回空内容 —— 若为推理模型,增大 MAX_TOKEN 试试")
            except Exception as e:
                self.root.after(0, self._log, "[测试] ✗ 失败: %s" % str(e)[:260])
                sus, msg = validate_model_for_provider(base, model)
                if sus:
                    self.root.after(0, self._log, "[测试] 原因可能是: %s" % msg)
        threading.Thread(target=work, daemon=True).start()

    def load_tts(self):
        gpt, sov = scan_weights()
        self.gpt_combo["values"] = ["%s/%s" % (d, f) for d, f in gpt]
        self.sov_combo["values"] = ["%s/%s" % (d, f) for d, f in sov]

        # 参考音频下拉:带时长与合规提示
        refs = self._ref_list()
        self.ref_combo["values"] = [r[0] for r in refs]

        cur = read_yaml_custom()
        t2s = cur.get("t2s_weights_path", "")
        vits = cur.get("vits_weights_path", "")
        # 转成"目录/文件"形式
        def short(p):
            if not p:
                return ""
            p = p.replace("\\", "/")
            parts = p.split("/")
            return "/".join(parts[-2:]) if len(parts) >= 2 else p
        self.gpt_var.set(short(t2s))
        self.sov_var.set(short(vits))
        self.ver_var.set(cur.get("version", "v2Pro"))

        # 从 reference_config.json 载入当前参考音频与文字
        rc = read_ref_config()
        cur_ref = (rc.get("ref_audio") or "").replace("/", os.sep)
        cur_name = os.path.basename(cur_ref) if cur_ref else ""
        matched = False
        for disp, path, name, dur in refs:
            if name == cur_name:
                self.ref_var.set(disp)
                matched = True
                break
        if not matched and refs:
            # 配置里的文件不存在了,退回第一个并提示
            self.ref_var.set(refs[0][0])
            if cur_name:
                self._log("[参考音频] ⚠ 配置里的 %s 已不存在,请重新选择" % cur_name)
        # 文字
        self.ref_text.delete("1.0", "end")
        self.ref_text.insert("1.0", rc.get("prompt_text", ""))
        if self.ref_var.get():
            self._on_ref_selected()

        online = tts_online()
        self.tts_status.config(
            text="TTS 服务: %s" % ("在线(可热切换)" if online else "离线(仅写配置文件,重启后生效)"),
            fg="#2fa36b" if online else "#d9534f")
        self._log("[TTS] 扫描到 GPT 权重 %d 个, SoVITS 权重 %d 个, 参考音频 %d 个, 服务%s" % (
            len(gpt), len(sov), len(refs), "在线" if online else "离线"))

    def load_retention(self):
        try:
            with open(RET_CONFIG, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {"max_files": 200, "max_total_mb": 300, "max_age_days": 7, "min_age_hours": 24}
        for k, v in self.ret_vars.items():
            v.set(str(cfg.get(k, "")))
        try:
            sys.path.insert(0, os.path.dirname(os.path.dirname(RET_CONFIG)))
            import importlib
            spec = importlib.util.spec_from_file_location(
                "ar", os.path.join(os.path.dirname(RET_CONFIG), "audio_retention.py"))
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            files = m.collect(cfg)
            total = sum(s for _, s, _ in files) / 1048576.0
            self.ret_status.config(
                text="当前: %d 个文件, %.1f MB" % (len(files), total), fg="#4a90d9")
        except Exception as e:
            self.ret_status.config(text="统计失败: %s" % str(e)[:50], fg="#d9534f")

    # ---------------- 保存 ----------------
    def save_tts(self):
        gpt = self.gpt_var.get().strip()
        sov = self.sov_var.get().strip()
        ver = self.ver_var.get().strip()
        if not gpt and not sov:
            messagebox.showwarning("提示", "至少选择一个权重")
            return
        gpt_abs = os.path.join(TTS_DIR, gpt.replace("/", os.sep)) if gpt else None
        sov_abs = os.path.join(TTS_DIR, sov.replace("/", os.sep)) if sov else None
        for p in (gpt_abs, sov_abs):
            if p and not os.path.isfile(p):
                messagebox.showerror("找不到文件", p)
                return

        # ---- 参考音频校验(踩过的坑:时长超范围会直接失败) ----
        ref_path = self._ref_path_by_label(self.ref_var.get())
        ref_text = self.ref_text.get("1.0", "end").strip()
        if ref_path:
            dur = _wav_duration(ref_path)
            if dur is not None and (dur < 3.0 or dur > 10.0):
                messagebox.showerror(
                    "参考音频时长不合规",
                    "当前参考音频时长 %.2f 秒,超出 GPT-SoVITS 要求的 3~10 秒。\n\n"
                    "继续保存会导致每次合成都报:\n"
                    "  「参考音频在3~10秒范围外，请更换！」\n\n"
                    "请换一个 3~10 秒的片段。" % dur)
                return
            if not ref_text:
                if not messagebox.askyesno(
                        "缺少对应文字",
                        "参考音频的「对应文字」为空。\n\n"
                        "GPT-SoVITS 靠「音频 + 文字」克隆音色,文字留空会让音色\n"
                        "明显失真(这是实测踩过的坑)。\n\n"
                        "仍要保存吗?"):
                    return

        b = backup(TTS_YAML, "before_tts")
        try:
            write_yaml_custom(
                gpt_path=(gpt_abs.replace("\\", "/") if gpt_abs else None),
                sovits_path=(sov_abs.replace("\\", "/") if sov_abs else None),
                version=ver or None)
        except Exception as e:
            messagebox.showerror("写入失败", str(e))
            return
        self._log("[TTS] 已写入 tts_infer.yaml")
        if b:
            self._log("     备份: %s" % os.path.basename(b))

        # ---- 保存参考音频配置 ----
        if ref_path:
            try:
                cfg = write_ref_config(ref_path, ref_text)
                self._log("[TTS] 参考音频已保存: %s" % cfg["ref_audio"])
                self._log("     对应文字: %s" % (ref_text[:50] or "(空)"))
            except Exception as e:
                self._log("[TTS] ⚠ 参考音频配置写入失败: %s" % e)

        if tts_online():
            self._log("[TTS] 服务在线,尝试热切换 …")

            def work():
                r = hot_swap(gpt_abs, sov_abs)
                self.root.after(0, lambda: self._log(r))
                self.root.after(0, lambda: self.load_tts())
            threading.Thread(target=work, daemon=True).start()
            messagebox.showinfo("完成", "配置已写入并提交热切换。\n详见运行日志。")
        else:
            messagebox.showinfo("完成", "配置已写入。\n\nTTS 服务当前离线,重启 api_v2.py 后生效。")

    def test_tts(self):
        def work():
            try:
                sys.path.insert(0, os.path.join(ROOT, "01_核心模块"))
                sys.path.insert(0, os.path.join(PROJECT, "vendor_py"))
                import importlib
                sys.path.insert(0, PROJECT)
                import wxauto_bootstrap  # noqa
                wxauto_bootstrap.setup()
                import gptsovits_tts as tts
                if not tts.is_server_alive():
                    self.root.after(0, lambda: self._log("[试听] TTS 服务离线"))
                    return
                out = os.path.join(os.path.dirname(RET_CONFIG), "_试听.wav")
                self.root.after(0, lambda: self._log("[试听] 合成中 …"))
                tts.synthesize("你好呀，这是当前音色的试听。", out)
                os.startfile(out)
                self.root.after(0, lambda: self._log("[试听] 已播放: %s" % out))
            except Exception as e:
                self.root.after(0, lambda: self._log("[试听] 失败: %s" % str(e)[:150]))
        threading.Thread(target=work, daemon=True).start()

    def save_retention(self):
        try:
            cfg = json.load(open(RET_CONFIG, "r", encoding="utf-8"))
        except Exception:
            cfg = {}
        for k, v in self.ret_vars.items():
            try:
                cfg[k] = int(float(v.get()))
            except Exception:
                pass
        b = backup(RET_CONFIG, "before_ret")
        with open(RET_CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        self._log("[留存] 已保存: %s" % {k: cfg.get(k) for k in self.ret_vars})
        self.load_retention()
        messagebox.showinfo("完成", "留存限额已保存")

    def report_retention(self):
        def work():
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "ar", os.path.join(os.path.dirname(RET_CONFIG), "audio_retention.py"))
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                files = m.collect(m.load_config())
                self.root.after(0, lambda: self._log("[留存] 共 %d 个文件, %.1f MB" % (
                    len(files), sum(s for _, s, _ in files) / 1048576.0)))
                for p, s, t in files[:8]:
                    self.root.after(0, lambda p=p, s=s, t=t: self._log(
                        "        %s  %.1fKB  %s" % (os.path.basename(p), s / 1024.0,
                                                   time.strftime("%m-%d %H:%M", time.localtime(t)))))
                self.root.after(0, self.load_retention)
            except Exception as e:
                self.root.after(0, lambda: self._log("[留存] 失败: %s" % str(e)[:120]))
        threading.Thread(target=work, daemon=True).start()

    def clean_retention(self):
        if not messagebox.askyesno("确认", "立即清理超出限额的音频/截图?\n\n"
                                           "24 小时内的文件受保护,不会删除。"):
            return

        def work():
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "ar", os.path.join(os.path.dirname(RET_CONFIG), "audio_retention.py"))
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                r = m.cleanup(dry_run=False, verbose=False)
                self.root.after(0, lambda: self._log(
                    "[留存] 已删除 %d 个,释放 %.1f MB" % (r.get("deleted", 0), r.get("freed_mb", 0))))
                self.root.after(0, self.load_retention)
            except Exception as e:
                self.root.after(0, lambda: self._log("[留存] 清理失败: %s" % str(e)[:120]))
        threading.Thread(target=work, daemon=True).start()


def main():
    root = tk.Tk()
    app = ConfigTool(root)
    app._log("配置面板已启动")
    app._log("  项目目录: %s" % PROJECT)
    app._log("  TTS 目录: %s" % TTS_DIR)
    app._log("  TTS 服务: %s" % ("在线" if tts_online() else "离线"))
    root.mainloop()


if __name__ == "__main__":
    main()
