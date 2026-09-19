# -*- coding: utf-8 -*-
"""
VoiceBot 控制面板 —— 统一入口(GUI)

整合:
  1. 语音自动回复(读取消息 → AI → 合成 → 发语音条)
  2. 配置面板(AI 模型 / TTS 音色 / 音频留存)
  3. 环境自检
  4. 音频留存清理与统计
  5. 常用工具(启动 TTS 服务、打开目录)

双击本文件即可运行。
"""

import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                     # VoiceBot/
DESKTOP = os.path.dirname(ROOT)
PROJECT = os.path.join(DESKTOP, "WeChatBot_WXAUTO_SE-3.28")
TTS_DIR = os.path.join(DESKTOP, "GPT-SoVITS-v2pro-20250604-nvidia50")
CORE = os.path.join(ROOT, "01_核心模块")
TOOLS = os.path.join(ROOT, "02_工具面板")
RET_DIR = os.path.join(ROOT, "04_音频留存")
DOCS = os.path.join(ROOT, "05_文档")

VOICE_BOT = os.path.join(CORE, "voice_bot.py")
CONFIG_TOOL = os.path.join(TOOLS, "config_tool.py")
RETENTION = os.path.join(RET_DIR, "audio_retention.py")
TTS_API = "http://127.0.0.1:9880"

PY = sys.executable
PYW = os.path.join(os.path.dirname(PY), "pythonw.exe")
if not os.path.isfile(PYW):
    PYW = PY
CREATE_NO_WINDOW = 0x08000000


class Panel:
    def __init__(self, root):
        self.root = root
        root.title("VoiceBot 控制面板")
        root.geometry("820x640")
        root.configure(bg="#eef1f5")

        # 标题
        head = tk.Frame(root, bg="#2d3e50", height=64)
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Label(head, text="VoiceBot  微信语音自动回复", bg="#2d3e50", fg="white",
                 font=("Microsoft YaHei", 15, "bold")).pack(side="left", padx=18)
        self.status = tk.Label(head, text="", bg="#2d3e50", fg="#9fe6bd",
                               font=("Microsoft YaHei", 9))
        self.status.pack(side="right", padx=18)

        # 按钮区
        body = tk.Frame(root, bg="#eef1f5")
        body.pack(fill="x", padx=16, pady=14)

        def mk(parent, text, desc, cmd, color, r, c):
            fr = tk.Frame(parent, bg="#ffffff", highlightthickness=1,
                          highlightbackground="#d8dee6")
            fr.grid(row=r, column=c, padx=7, pady=7, sticky="nsew")
            b = tk.Button(fr, text=text, bg=color, fg="white", relief="flat",
                          font=("Microsoft YaHei", 11, "bold"), cursor="hand2",
                          height=2, command=cmd)
            b.pack(fill="x", padx=10, pady=(10, 4))
            tk.Label(fr, text=desc, bg="#ffffff", fg="#7a8899", justify="left",
                     wraplength=330, font=("Microsoft YaHei", 8)).pack(anchor="w", padx=12, pady=(0, 10))
            return fr

        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        mk(body, "① 环境自检", "检查微信窗口 / TTS 服务 / 虚拟线卡 / AI 接口。\n建议每次使用前先跑一次。",
           self.do_check, "#4a90d9", 0, 0)
        mk(body, "② 启动语音回复", "读取新消息 → AI 生成 → 合成语音 → 发语音条。\n会先确认,再实际发送。",
           self.do_run_live, "#2fa36b", 0, 1)
        mk(body, "③ 演练模式(不发送)", "完整跑一遍但**不真的发消息**,用于确认回复内容。",
           self.do_run_dry, "#7a9ec9", 1, 0)
        mk(body, "④ 配置面板", "修改 AI 模型、TTS 音色(GPT/SoVITS 权重)、\n音频留存限额。",
           self.do_config, "#8a6fd9", 1, 1)
        mk(body, "⑤ 音频留存管理", "查看占用、立即清理超出限额的音频/截图。",
           self.do_retention, "#d99a3d", 2, 0)
        mk(body, "⑥ 启动 TTS 服务", "启动 GPT-SoVITS 的 api_v2.py(9880 端口)。\n若已在线则不会重复启动。",
           self.do_start_tts, "#3d9aa8", 2, 1)
        mk(body, "⑦ 角色配置 Web UI", "在浏览器里管理每个会话的角色设定(prompt)、\n行号、启用开关,可一键从微信导入会话。",
           self.do_webui, "#7d5ba6", 3, 0)
        mk(body, "⑧ 打开项目目录", "打开 VoiceBot 文件夹(文档 / 备份 / 音频 / 模块)。",
           self.do_opendir, "#8a8f98", 3, 1)

        # 输出区
        outf = tk.Frame(root, bg="#eef1f5")
        outf.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        tk.Label(outf, text="运行输出", bg="#eef1f5", fg="#5a6a7a",
                 font=("Microsoft YaHei", 9, "bold")).pack(anchor="w")
        self.out = scrolledtext.ScrolledText(outf, font=("Consolas", 9),
                                             bg="#1e1e1e", fg="#d4d4d4",
                                             insertbackground="#d4d4d4", height=14)
        self.out.pack(fill="both", expand=True)

        self.busy = False
        self.log("VoiceBot 控制面板已启动")
        self.log("  项目目录: %s" % PROJECT)
        self.log("  核心模块: %s" % CORE)
        self.log("")
        threading.Thread(target=self._bg_status, daemon=True).start()

    # ---------------- 基础 ----------------
    def log(self, s):
        self.out.insert("end", str(s) + "\n")
        self.out.see("end")

    def _bg_status(self):
        """后台刷新状态栏。"""
        while True:
            try:
                import requests
                tts = "在线" if requests.get(TTS_API + "/openapi.json", timeout=3).status_code < 500 else "离线"
            except Exception:
                tts = "离线"
            wx = "运行中"
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
                h = ctypes.windll.user32.FindWindowW("Qt51514QWindowIcon", "微信")
                wx = "运行中" if h else "未启动"
            except Exception:
                wx = "?"

            def upd():
                self.status.config(text="TTS: %s   |   微信: %s" % (tts, wx),
                                   fg="#9fe6bd" if tts == "在线" else "#ffb3a7")
            self.root.after(0, upd)
            time.sleep(5)

    def _run(self, args, label, cwd=None, console=False, on_done=None):
        """在后台跑一个 python 脚本,输出到日志区。"""
        if self.busy and not console:
            messagebox.showinfo("请稍候", "已有任务在运行。")
            return
        self.busy = True
        self.log("")
        self.log("=" * 62)
        self.log("[%s] 启动 …" % label)
        self.log("=" * 62)

        def work():
            try:
                env = dict(os.environ)
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUTF8"] = "1"
                flags = 0 if console else CREATE_NO_WINDOW
                p = subprocess.Popen(
                    [PY, "-u"] + args, cwd=cwd or HERE, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=flags)
                for line in p.stdout:
                    self.root.after(0, self.log, line.rstrip("\n"))
                p.wait()
                self.root.after(0, self.log, "[结束] 退出码 %s" % p.returncode)
                if on_done:
                    self.root.after(0, on_done)
            except Exception as e:
                self.root.after(0, self.log, "[错误] %s" % e)
            finally:
                self.busy = False

        threading.Thread(target=work, daemon=True).start()

    def _run_forever(self, args, label, cwd=None):
        """跑一个常驻脚本(如语音回复循环),单独线程。"""
        self.log("")
        self.log("=" * 62)
        self.log("[%s] 启动(常驻,关闭本面板不会停止它)" % label)
        self.log("=" * 62)

        def work():
            try:
                env = dict(os.environ)
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUTF8"] = "1"
                p = subprocess.Popen(
                    [PY, "-u"] + args, cwd=cwd or HERE, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=CREATE_NO_WINDOW)
                for line in p.stdout:
                    self.root.after(0, self.log, line.rstrip("\n"))
                p.wait()
                self.root.after(0, self.log, "[结束] 退出码 %s" % p.returncode)
            except Exception as e:
                self.root.after(0, self.log, "[错误] %s" % e)

        threading.Thread(target=work, daemon=True).start()

    # ---------------- 功能 ----------------
    def do_check(self):
        if not os.path.isfile(VOICE_BOT):
            messagebox.showerror("找不到", VOICE_BOT)
            return
        self._run([VOICE_BOT, "--check"], "环境自检", cwd=CORE)

    def do_run_dry(self):
        self._run_forever([VOICE_BOT], "演练模式(不发送)", cwd=CORE)

    def do_run_live(self):
        if not messagebox.askyesno(
                "确认实际发送",
                "这会真的发出语音条。\n\n"
                "请确认:\n"
                "  · 微信已登录且窗口最大化\n"
                "  · 默认录音设备 = VoiceMeeter Output\n"
                "  · 默认播放设备 = 扬声器\n"
                "  · voice_bot 里的 CHATS 不含真实联系人\n\n"
                "继续?"):
            return
        self._run_forever([VOICE_BOT, "--live"], "实际发送", cwd=CORE)

    def do_config(self):
        if not os.path.isfile(CONFIG_TOOL):
            messagebox.showerror("找不到", CONFIG_TOOL)
            return

        def open_it():
            subprocess.Popen([PYW, CONFIG_TOOL], cwd=TOOLS,
                             creationflags=CREATE_NO_WINDOW)
            self.log("[配置面板] 已在新窗口打开")
        threading.Thread(target=open_it, daemon=True).start()

    def do_retention(self):
        self._run([RETENTION, "--report"], "音频留存统计", cwd=RET_DIR,
                  on_done=self._ask_clean)

    def _ask_clean(self):
        if messagebox.askyesno("清理", "是否立即清理超出限额的文件?"):
            self._run([RETENTION], "音频留存清理", cwd=RET_DIR)

    def do_webui(self):
        """启动角色配置 Web UI 并打开浏览器。"""
        webui = os.path.join(TOOLS, "character_webui.py")
        if not os.path.isfile(webui):
            messagebox.showerror("找不到", webui)
            return

        # 已在运行就直接开浏览器
        try:
            import requests
            if requests.get("http://127.0.0.1:5100/api/state", timeout=3).status_code == 200:
                import webbrowser
                webbrowser.open("http://127.0.0.1:5100")
                self.log("[Web UI] 已在运行,已打开浏览器")
                return
        except Exception:
            pass

        def work():
            try:
                self.root.after(0, self.log, "[Web UI] 启动中 …")
                env = dict(os.environ)
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUTF8"] = "1"
                p = subprocess.Popen(
                    [PY, "-u", webui], cwd=TOOLS, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=CREATE_NO_WINDOW)
                time.sleep(2.5)
                import webbrowser
                webbrowser.open("http://127.0.0.1:5100")
                self.root.after(0, self.log, "[Web UI] 地址 http://127.0.0.1:5100  (PID %s)" % p.pid)
                for line in p.stdout:
                    self.root.after(0, self.log, line.rstrip("\n"))
            except Exception as e:
                self.root.after(0, self.log, "[Web UI] 启动失败: %s" % e)
        threading.Thread(target=work, daemon=True).start()

    def do_opendir(self):
        try:
            os.startfile(ROOT)
            self.log("[目录] 已打开 %s" % ROOT)
        except Exception as e:
            messagebox.showerror("失败", str(e))

    def do_start_tts(self):
        try:
            import requests
            if requests.get(TTS_API + "/openapi.json", timeout=3).status_code < 500:
                messagebox.showinfo("已在线", "TTS 服务已在运行,无需重复启动。")
                return
        except Exception:
            pass

        runtime = os.path.join(TTS_DIR, "runtime", "python.exe")
        api = os.path.join(TTS_DIR, "api_v2.py")
        if not os.path.isfile(runtime) or not os.path.isfile(api):
            messagebox.showerror("找不到", "检查:\n%s\n%s" % (runtime, api))
            return

        def work():
            self.root.after(0, self.log, "[TTS] 启动 api_v2.py(首次加载模型约 20~60 秒)…")
            env = dict(os.environ)
            env["PYTHONPATH"] = TTS_DIR
            p = subprocess.Popen(
                [runtime, api, "-a", "127.0.0.1", "-p", "9880",
                 "-c", "GPT_SoVITS/configs/tts_infer.yaml"],
                cwd=TTS_DIR, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW)
            self.root.after(0, self.log, "[TTS] PID=%s" % p.pid)
            for line in p.stdout:
                self.root.after(0, self.log, line.rstrip("\n"))
        threading.Thread(target=work, daemon=True).start()


def main():
    root = tk.Tk()
    Panel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
