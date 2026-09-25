# -*- coding: utf-8 -*-
"""
VoiceBot 控制面板 —— 统一入口(GUI)

整合:
  1. 语音自动回复(读取消息 → AI → 合成 → 发语音条)
  2. 配置面板(AI 模型 / TTS 音色 / 音频留存)
  3. 环境自检
  4. 音频留存清理与统计
  5. 常用工具(启动 TTS 服务、打开目录)
  6. 部署路径(手动指定 WeChatBot / GPT-SoVITS 的位置)

关于两个外部程序
---------------------------------------------------------------------------
VoiceBot 自己只是插件,真正干活的是它外面两个程序:

    WeChatBot      读写微信(解密数据库、发消息、发语音条)
    GPT-SoVITS     文字 → 语音(音色克隆)

所以点按钮前先查一遍,不行就当面说清楚,别等跑到一半才莫名失败:

    点『启动 TTS 服务』   → 目录不可用 → 弹「未部署 GPT-SoVITS」/「TTS 路径不正确」
    点『启动语音回复』     → 目录不可用 → 弹「未部署 WeChatBot」/「WeChatBot 路径不正确」

两种失败的话术是分开的:机器上压根没装 → 告诉去哪下载;
装了只是路径指错 → 直接把检测到的可用目录给你,一键改用。

两个目录的位置写在 VoiceBot/部署路径.json(本机路径,不入库),
voice_bot.py / gptsovits_tts.py 和本面板用的是同一份 —— 面板里改了就是真改了。

双击本文件即可运行。
"""

import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                     # VoiceBot/
DESKTOP = os.path.dirname(ROOT)
CORE = os.path.join(ROOT, "01_核心模块")
TOOLS = os.path.join(ROOT, "02_工具面板")
RET_DIR = os.path.join(ROOT, "04_音频留存")
DOCS = os.path.join(ROOT, "05_文档")

# 部署路径.py 是"两个外部目录在哪"的唯一真值来源,面板与核心模块共用它
if CORE not in sys.path:
    sys.path.insert(0, CORE)
import 部署路径 as dp

VOICE_BOT = os.path.join(CORE, "voice_bot.py")
CONFIG_TOOL = os.path.join(TOOLS, "config_tool.py")
RETENTION = os.path.join(RET_DIR, "audio_retention.py")
TTS_API = "http://127.0.0.1:9880"

PY = sys.executable
PYW = os.path.join(os.path.dirname(PY), "pythonw.exe")
if not os.path.isfile(PYW):
    PYW = PY
CREATE_NO_WINDOW = 0x08000000

# 「未部署」弹窗里给的自救说明 —— 两个程序下载方式不同,分开写
DEPLOY_HINT = {
    "wechatbot": (
        "VoiceBot 只是个插件,微信的读取和发送全靠它,\n"
        "必须自行下载部署:\n"
        "   1. 下载 WeChatBot_WXAUTO_SE\n"
        "   2. 解压到任意目录(例如桌面)\n"
        "   3. 回来点『选择…』指定该目录"),
    "tts": (
        "VoiceBot 只是个插件,文字转语音全靠它,\n"
        "必须自行下载部署:\n"
        "   1. 下载 GPT-SoVITS(v2Pro 整合包)\n"
        "   2. 解压到任意目录(例如桌面)\n"
        "   3. 回来点『选择…』指定该目录"),
}


class Panel:
    def __init__(self, root):
        self.root = root
        root.title("VoiceBot 控制面板")
        # 按屏幕尺寸自适应并居中。
        # 注意 Tk 拿到的宽高是**缩放后**的逻辑尺寸:实测 2560x1600 物理屏、
        # 175% 缩放下 Tk 只报 1463x914 —— 可用高度远小于物理高度。
        # 所以这里不写死尺寸,并且下面的布局要按"可用高度可能只有 750 左右"来设计。
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w = max(820, min(1040, sw - 40))
        h = max(600, min(820, sh - 70))
        root.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 3)))
        root.minsize(820, 580)
        root.configure(bg="#eef1f5")

        # 当前生效的两个外部目录(可能在『部署路径』区被改)
        self.wechatbot_dir = dp.wechatbot_dir()
        self.tts_dir = dp.tts_dir()

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
        #
        # 尺寸是算过的:五排按钮每排多占十几像素,底部的东西就会被挤出窗口
        # (实测过:部署路径区整块看不见)。所以用 height=1 + 更紧的间距 +
        # 更宽的两列,把每排压到 70 上下,给底部留出位置。
        body = tk.Frame(root, bg="#eef1f5")
        body.pack(fill="x", padx=14, pady=(12, 8))
        wrap = max(260, int((w - 60) / 2) - 46)

        def mk(parent, text, desc, cmd, color, r, c):
            fr = tk.Frame(parent, bg="#ffffff", highlightthickness=1,
                          highlightbackground="#d8dee6")
            fr.grid(row=r, column=c, padx=6, pady=5, sticky="nsew")
            b = tk.Button(fr, text=text, bg=color, fg="white", relief="flat",
                          font=("Microsoft YaHei", 10, "bold"), cursor="hand2",
                          height=1, command=cmd)
            b.pack(fill="x", padx=8, pady=(2, 3))
            tk.Label(fr, text=desc, bg="#ffffff", fg="#7a8899", justify="left",
                     wraplength=wrap, font=("Microsoft YaHei", 8)).pack(
                         anchor="w", padx=10, pady=(0, 6))
            return fr

        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        mk(body, "① 环境自检", "检查微信窗口 / TTS 服务 / 虚拟线卡 / AI 接口。",
           self.do_check, "#4a90d9", 0, 0)
        mk(body, "② 启动语音回复", "读消息 → AI → 合成 → 实际发送语音条。",
           self.do_run_live, "#2fa36b", 0, 1)
        mk(body, "③ 演练模式(不发送)", "完整跑一遍,但不真的发消息。",
           self.do_run_dry, "#7a9ec9", 1, 0)
        mk(body, "④ 配置面板", "改 AI 模型、TTS 音色、音频留存限额。",
           self.do_config, "#8a6fd9", 1, 1)
        mk(body, "⑤ 音频留存管理", "查看占用,清理超出限额的音频/截图。",
           self.do_retention, "#d99a3d", 2, 0)
        mk(body, "⑥ 启动 TTS 服务", "启动 GPT-SoVITS 的 api_v2.py(9880 端口)。",
           self.do_start_tts, "#3d9aa8", 2, 1)
        mk(body, "⑦ 角色配置 Web UI", "浏览器里管理角色 prompt、行号、监视开关。",
           self.do_webui, "#7d5ba6", 3, 0)
        mk(body, "⑧ 打开项目目录", "打开 VoiceBot 文件夹(文档 / 备份 / 音频)。",
           self.do_opendir, "#8a8f98", 3, 1)
        mk(body, "⑨ 停止语音回复", "终止正在运行的语音回复进程。",
           self.do_stop_bot, "#d9534f", 4, 0)
        mk(body, "⑩ 停止全部进程", "终止语音回复 + 角色 Web UI + TTS 服务。",
           self.do_stop_all, "#b93b36", 4, 1)

        # 运行输出区:可伸缩,先钉在窗口底部
        outf = tk.Frame(root, bg="#eef1f5")
        outf.pack(side="bottom", fill="both", expand=True, padx=14, pady=(0, 10))
        tk.Label(outf, text="运行输出", bg="#eef1f5", fg="#5a6a7a",
                 font=("Microsoft YaHei", 9, "bold")).pack(anchor="w")
        self.out = scrolledtext.ScrolledText(outf, font=("Consolas", 9),
                                             bg="#1e1e1e", fg="#d4d4d4",
                                             insertbackground="#d4d4d4", height=5)
        self.out.pack(fill="both", expand=True)

        # 部署路径区:必须钉在输出区**上方**并优先占位 ——
        # 后 pack 的 side="bottom" 会落在先前那个的上面,所以顺序是
        # 输出区 → 部署路径区,而伸缩的只有输出区,部署路径不会被挤没。
        self._paths_section(root)

        self.busy = False
        self._procs = []          # 本面板启动的子进程
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.log("VoiceBot 控制面板已启动")
        self.log("  核心模块: %s" % CORE)
        self._log_deploy_status()
        self.log("")
        threading.Thread(target=self._bg_status, daemon=True).start()

    # ---------------- 基础 ----------------
    def log(self, s):
        self.out.insert("end", str(s) + "\n")
        self.out.see("end")

    def _tts_online(self):
        """GPT-SoVITS 的 HTTP 服务是否已经在响应。"""
        try:
            import requests
            return requests.get(TTS_API + "/openapi.json", timeout=3).status_code < 500
        except Exception:
            return False

    def _child_env(self):
        """给子进程注入两个外部目录的位置 + UTF-8。

        这样"面板里选的路径"对 voice_bot.py / gptsovits_tts.py 一样生效,
        不必去改它们的源码。环境变量优先级最高,是刻意的。
        """
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env[dp.ENV_WECHATBOT] = self.wechatbot_dir
        env[dp.ENV_TTS] = self.tts_dir
        return env

    def _bg_status(self):
        """后台刷新状态栏。"""
        while True:
            tts = "在线" if self._tts_online() else "离线"
            wx = "运行中"
            try:
                import ctypes
                # 注意:这里**不要**调 SetProcessDPIAware()。
                # 它是进程级设置,必须在创建任何窗口之前调用;在窗口建好之后
                # 再从后台线程调,会让 Tk 的尺寸与系统实际渲染对不上 ——
                # 实测在缩放显示器上表现为整块界面被放大、错位、右侧被截断。
                # 保持默认的"DPI 不感知"反而正确:由系统统一按缩放比例拉伸,
                # 字号和布局都正常。
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
                env = self._child_env()
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
                env = self._child_env()
                p = subprocess.Popen(
                    [PY, "-u"] + args, cwd=cwd or HERE, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=CREATE_NO_WINDOW)
                self._procs.append(p)
                self.root.after(0, self.log, "[PID] %s" % p.pid)
                for line in p.stdout:
                    self.root.after(0, self.log, line.rstrip("\n"))
                p.wait()
                self.root.after(0, self.log, "[结束] 退出码 %s" % p.returncode)
            except Exception as e:
                self.root.after(0, self.log, "[错误] %s" % e)

        threading.Thread(target=work, daemon=True).start()

    # ---------------- 部署路径 ----------------
    def _log_deploy_status(self):
        """启动时把两个目录的体检结果写进日志 —— 出问题一眼能看到。"""
        for kind, name in (("wechatbot", "WeChatBot"), ("tts", "GPT-SoVITS")):
            d = dp.diagnose_wechatbot() if kind == "wechatbot" else dp.diagnose_tts()
            mark = {"ok": "✅ 正常",
                    "wrong_path": "❌ 路径不正确",
                    "not_deployed": "❌ 未部署"}[d["state"]]
            self.log("  %-12s %s" % (name, mark))
            self.log("               %s" % d["path"])
            if d["state"] != "ok" and d["issue"]:
                self.log("               问题: %s" % d["issue"])

    def _paths_section(self, parent):
        box = tk.LabelFrame(parent, text=" 部署路径(两个外部程序的位置,可手动指定) ",
                            bg="#eef1f5", fg="#2d3e50",
                            font=("Microsoft YaHei", 9, "bold"),
                            bd=1, relief="groove")
        box.pack(side="bottom", fill="x", padx=14, pady=(0, 8))
        box.columnconfigure(1, weight=1)

        self.var_wb = tk.StringVar(value=self.wechatbot_dir)
        self.var_tts = tk.StringVar(value=self.tts_dir)

        self.lbl_wb = self._path_row(box, 0, "WeChatBot", self.var_wb, "wechatbot")
        self.lbl_tts = self._path_row(box, 2, "TTS / GPT-SoVITS", self.var_tts, "tts")

        tk.Button(box, text="重新自动检测", font=("Microsoft YaHei", 8),
                  bg="#8a8f98", fg="white", relief="flat", cursor="hand2",
                  command=self.do_auto_detect).grid(
                      row=4, column=2, columnspan=2, sticky="e", padx=8, pady=(0, 6))

        # 边打字边给出反馈,不用每次点『检测』
        self.var_wb.trace_add("write", lambda *a: self._refresh_path_ui())
        self.var_tts.trace_add("write", lambda *a: self._refresh_path_ui())
        self._refresh_path_ui()

    def _path_row(self, box, row, label, var, kind):
        tk.Label(box, text=label, bg="#eef1f5", fg="#3c4b5a", width=16, anchor="w",
                 font=("Microsoft YaHei", 9, "bold")).grid(
                     row=row, column=0, sticky="w", padx=(10, 4), pady=(5, 0))
        tk.Entry(box, textvariable=var, font=("Consolas", 9), bg="white",
                 relief="solid", bd=1).grid(
                     row=row, column=1, sticky="ew", padx=4, pady=(5, 0), ipady=2)
        tk.Button(box, text="选择…", font=("Microsoft YaHei", 8), bg="#4a90d9",
                  fg="white", relief="flat", cursor="hand2",
                  command=lambda k=kind: self.do_pick_dir(k)).grid(
                      row=row, column=2, padx=4, pady=(5, 0))
        tk.Button(box, text="检测", font=("Microsoft YaHei", 8), bg="#5aa469",
                  fg="white", relief="flat", cursor="hand2",
                  command=lambda k=kind: self.do_check_path(k)).grid(
                      row=row, column=3, padx=(0, 8), pady=(5, 0))
        lbl = tk.Label(box, text="", bg="#eef1f5", fg="#7a8899", anchor="w",
                       justify="left", font=("Microsoft YaHei", 8))
        lbl.grid(row=row + 1, column=1, columnspan=3, sticky="w", padx=6, pady=(0, 2))
        return lbl

    def _checker_of(self, kind):
        return dp.check_wechatbot if kind == "wechatbot" else dp.check_tts

    def _name_of(self, kind):
        """程序名 —— 用在说明正文里。"""
        return "WeChatBot" if kind == "wechatbot" else "GPT-SoVITS"

    def _title_of(self, kind, state):
        """弹窗标题。

        『未部署』用程序全名(要用户去下载的是 GPT-SoVITS 这个程序);
        『路径不正确』用 TTS —— 用户是给"服务"配路径的,找的就是 TTS 这个词。
        """
        if kind == "tts":
            return "未部署 GPT-SoVITS" if state == "not_deployed" else "TTS 路径不正确"
        return "未部署 WeChatBot" if state == "not_deployed" else "WeChatBot 路径不正确"

    def _refresh_path_ui(self):
        """状态标签跟着输入框里的内容走(而不是跟着已保存的值)。"""
        for kind, var, lbl in (("wechatbot", self.var_wb, self.lbl_wb),
                               ("tts", self.var_tts, self.lbl_tts)):
            p = var.get().strip().strip('"')
            ok, why = self._checker_of(kind)(p)
            if ok:
                saved = self.wechatbot_dir if kind == "wechatbot" else self.tts_dir
                extra = "" if dp.same_path(p, saved) else "    (未保存,点『检测』生效)"
                lbl.config(text="✅ 正常" + (("　" + why) if why else "") + extra,
                           fg="#1f7a45")
            else:
                lbl.config(text="❌ " + why, fg="#c0392b")

    def _apply_path(self, kind, path):
        path = os.path.normpath(path)
        if kind == "wechatbot":
            self.wechatbot_dir = path
            dp.save(wechatbot_dir=path)
            self.var_wb.set(path)
        else:
            self.tts_dir = path
            dp.save(tts_dir=path)
            self.var_tts.set(path)
        self.log("[路径] %s = %s" % (self._name_of(kind), path))
        self._refresh_path_ui()

    def do_pick_dir(self, kind):
        """手动选择目录。选完立刻校验,不合格说清原因并允许重选。"""
        name = self._name_of(kind)
        checker = self._checker_of(kind)
        cur = self.wechatbot_dir if kind == "wechatbot" else self.tts_dir
        while True:
            p = filedialog.askdirectory(
                title="选择 %s 目录" % name, mustexist=True,
                initialdir=cur if os.path.isdir(cur) else DESKTOP)
            if not p:
                return False
            p = os.path.normpath(p)
            ok, why = checker(p)
            if ok:
                self._apply_path(kind, p)
                if why:
                    messagebox.showinfo("已保存(有提醒)", "%s\n\n%s" % (p, why))
                return True
            if not messagebox.askretrycancel(
                    self._title_of(kind, "wrong_path"),
                    "这个目录不能用:\n%s\n\n问题:%s\n\n"
                    "『重试』= 重新选一个目录\n『取消』= 放弃(保留原来的设置)"
                    % (p, why)):
                return False

    def do_check_path(self, kind):
        """校验并保存输入框里手填的路径。"""
        name = self._name_of(kind)
        var = self.var_wb if kind == "wechatbot" else self.var_tts
        p = var.get().strip().strip('"')
        if not p:
            messagebox.showwarning("没有填路径", "请先『选择…』或填写 %s 的目录。" % name)
            return
        ok, why = self._checker_of(kind)(p)
        if ok:
            self._apply_path(kind, p)
            messagebox.showinfo("检测通过", "%s 目录可用:\n%s%s"
                                % (name, p, ("\n\n" + why) if why else ""))
        else:
            messagebox.showerror(self._title_of(kind, "wrong_path"),
                                 "这个目录不能用:\n%s\n\n问题:%s" % (p, why))
            self._refresh_path_ui()

    def do_auto_detect(self):
        """忽略配置,重新在桌面/用户目录里扫一遍可用目录。"""
        lines = []
        for kind, name in (("wechatbot", "WeChatBot"), ("tts", "GPT-SoVITS")):
            old = self.wechatbot_dir if kind == "wechatbot" else self.tts_dir
            found = dp.detect_wechatbot() if kind == "wechatbot" else dp.detect_tts()
            if found:
                self._apply_path(kind, found)
                if not dp.same_path(found, old):
                    lines.append("  %s: %s\n      (原: %s)" % (name, found, old))
                else:
                    lines.append("  %s: %s(不变)" % (name, found))
            else:
                lines.append("  %s: 没扫到可用目录,保持原设置\n      %s" % (name, old))
        self.log("[路径] 重新自动检测完成")
        for s in lines:
            self.log(s)
        messagebox.showinfo("自动检测结果", "\n".join(lines))

    def _ensure_deploy(self, kind):
        """点按钮时的第一道保险:目录不可用就弹窗给出解决办法。

        两种失败分开说 —— 用户该做的事完全不同:
            未部署      机器上压根没这个程序   → 告诉他去哪下载、怎么指定
            路径不正确  装了,只是指错了地方  → 把检测到的可用目录直接给他
        """
        name = self._name_of(kind)
        d = dp.diagnose_wechatbot() if kind == "wechatbot" else dp.diagnose_tts()
        if d["state"] == "ok":
            if d["issue"]:
                self.log("[%s] %s" % (name, d["issue"]))
            return True

        title = self._title_of(kind, d["state"])

        if d["state"] == "not_deployed":
            if messagebox.askyesno(
                    title,
                    "没有找到 %s 程序。\n\n%s\n\n现在要手动指定 %s 目录吗?"
                    % (name, DEPLOY_HINT[kind], name)):
                return self.do_pick_dir(kind)
            return False

        # ---- 路径不正确 ----
        found = d.get("found")
        usable = bool(found) and not d.get("found_issue")
        lines = ["当前设置的位置不能用:", "  %s" % d["path"], "",
                 "问题: %s" % d["issue"]]
        if usable:
            lines += ["", "检测到可用的目录:", "  %s" % found]
        elif found:
            lines += ["", "在下面这个位置找到了同名目录,但它也有问题:",
                      "  %s" % found, "  问题: %s" % d["found_issue"]]

        if usable:
            ans = messagebox.askyesnocancel(
                title,
                "\n".join(lines) +
                "\n\n是(Y)   = 改用检测到的目录\n否(N)   = 手动选择其它目录\n取消    = 先不改")
            if ans is None:
                return False
            if ans:
                self._apply_path(kind, found)
                return True
            return self.do_pick_dir(kind)

        if messagebox.askyesno(title, "\n".join(lines) + "\n\n要手动选择目录吗?"):
            return self.do_pick_dir(kind)
        return False

    # ---------------- 进程管理 ----------------
    def _find_bot_procs(self):
        """找出所有与 VoiceBot 相关的 python 进程。

        返回 [(pid, 简短描述, 创建时间), ...]
        """
        import psutil
        out = []
        me = os.getpid()
        for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                if p.info["pid"] == me:
                    continue
                nm = (p.info["name"] or "").lower()
                if "python" not in nm:
                    continue
                cl = p.info["cmdline"] or []
                joined = " ".join(cl)
                # 只认我们自己的脚本,避免误杀别的 python
                if "voice_bot" in joined or "character_webui" in joined:
                    tag = []
                    if "character_webui" in joined:
                        tag.append("角色WebUI")
                    if "voice_bot" in joined:
                        tag.append("语音回复")
                    if "--live" in joined:
                        tag.append("实际发送")
                    elif "voice_bot" in joined:
                        tag.append("演练")
                    out.append((p.info["pid"], "+".join(tag) or "?",
                                p.info.get("create_time") or 0))
            except Exception:
                continue
        return out

    def _check_tts_ready(self, offer_start=True):
        """检查 TTS 是否真正可用(目录 + 端口 + 模型)。

        实测坑:api_v2.py 端口先通、模型后加载完。
        没等就绪就发语音 -> 合成失败 -> 表现成"没播放音频、没点发送"。
        所以启动语音回复前先在这里把关。

        返回 True 表示可以继续。
        """
        if not self._ensure_deploy("tts"):
            return False

        if self._tts_online():
            self.log("[TTS] 服务在线")
            return True

        if not offer_start:
            return False
        if messagebox.askyesno(
                "TTS 服务未启动",
                "GPT-SoVITS 程序在,但语音合成服务(9880 端口)没有运行。\n\n"
                "不启动的话,发送语音时会在『合成语音』这一步失败,\n"
                "表现为:没有播放音频、鼠标也没有点击发送。\n\n"
                "要现在启动它吗?\n(启动后需等 20~60 秒加载模型)"):
            self.do_start_tts()
            self.log("")
            self.log("⚠ TTS 正在启动。请等日志出现 " +
                     "'Uvicorn running on http://127.0.0.1:9880'、")
            self.log("  并且状态栏显示『TTS: 在线』后再启动语音回复。")
            self.log("  (程序本身也会在合成前自动等待就绪,不必抢时间)")
        return False

    def _kill_pids(self, pids, label):
        import psutil
        ok, fail = 0, 0
        for pid in pids:
            try:
                pr = psutil.Process(pid)
                pr.terminate()
                try:
                    pr.wait(timeout=5)
                except psutil.TimeoutExpired:
                    pr.kill()
                ok += 1
                self.log("  已终止 PID %s" % pid)
            except Exception as e:
                fail += 1
                self.log("  终止 PID %s 失败: %s" % (pid, str(e)[:60]))
        self.log("[%s] 成功 %d 个,失败 %d 个" % (label, ok, fail))
        return ok

    def do_stop_bot(self):
        procs = self._find_bot_procs()
        # 只停语音回复,不停角色 Web UI
        target = [(pid, tag, t) for pid, tag, t in procs if "WebUI" not in tag]
        if not target:
            messagebox.showinfo("没有运行中", "当前没有正在运行的语音回复进程。")
            self.log("[停止] 没有找到语音回复进程")
            return
        names = "\n".join("  PID %s  (%s)" % (pid, tag) for pid, tag, _ in target)
        if not messagebox.askyesno("确认停止", "要终止以下进程吗?\n\n%s" % names):
            return
        self._kill_pids([pid for pid, _, _ in target], "停止语音回复")

    def do_stop_all(self):
        procs = self._find_bot_procs()
        lines = []
        for pid, tag, _ in procs:
            lines.append("  PID %s  (%s)" % (pid, tag))
        tts_on = self._tts_online()
        if tts_on:
            lines.append("  GPT-SoVITS TTS 服务(9880 端口)")
        if not lines:
            messagebox.showinfo("没有运行中", "没有找到相关进程,TTS 也未运行。")
            return
        if not messagebox.askyesno(
                "确认停止全部",
                "要终止以下进程吗?\n\n%s\n\n"
                "(本控制面板自身不会被关闭)" % "\n".join(lines)):
            return
        self._kill_pids([pid for pid, _, _ in procs], "停止全部")
        if tts_on:
            self._stop_tts()

    def _stop_tts(self):
        """终止 api_v2.py 进程。"""
        import psutil
        killed = 0
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cl = " ".join(p.info["cmdline"] or [])
                if "api_v2.py" in cl:
                    p.terminate()
                    try:
                        p.wait(timeout=8)
                    except psutil.TimeoutExpired:
                        p.kill()
                    killed += 1
                    self.log("  已终止 TTS 服务 PID %s" % p.info["pid"])
            except Exception:
                continue
        if killed:
            self.log("[TTS] 已终止 %d 个进程" % killed)
        else:
            self.log("[TTS] 未找到 api_v2.py 进程(可能是外部终端启动的)")

    def _on_close(self):
        """关闭面板时提示是否一并停止后台进程。"""
        procs = self._find_bot_procs()
        tts_on = self._tts_online()
        if procs or tts_on:
            n = len(procs) + (1 if tts_on else 0)
            ans = messagebox.askyesnocancel(
                "退出前确认",
                "还有 %d 个后台进程在运行:\n\n%s%s\n\n"
                "是(Y)   = 全部停止后退出\n"
                "否(N)   = 保持运行,只关面板\n"
                "取消    = 不退出" % (
                    n,
                    "".join("  PID %s (%s)\n" % (p, t) for p, t, _ in procs),
                    "  TTS 服务(9880)\n" if tts_on else ""))
            if ans is None:
                return
            if ans:
                self._kill_pids([p for p, _, _ in procs], "退出时停止")
                if tts_on:
                    self._stop_tts()
        try:
            self.root.destroy()
        except Exception:
            pass

    # ---------------- 功能 ----------------
    def do_check(self):
        if not self._ensure_deploy("wechatbot"):
            return
        if not os.path.isfile(VOICE_BOT):
            messagebox.showerror("找不到", VOICE_BOT)
            return
        self._run([VOICE_BOT, "--check"], "环境自检", cwd=CORE)

    def do_run_dry(self):
        # 演练模式也要合成语音,所以同样检查 wechatbot 和 TTS
        if not self._ensure_deploy("wechatbot"):
            return
        if not self._check_tts_ready(offer_start=True):
            return
        self._run_forever([VOICE_BOT], "演练模式(不发送)", cwd=CORE)

    def do_run_live(self):
        if not self._ensure_deploy("wechatbot"):
            return
        if not self._check_tts_ready(offer_start=True):
            return
        if not messagebox.askyesno(
                "确认实际发送",
                "这会真的发出语音条。\n\n"
                "请确认:\n"
                "  · 微信已登录且窗口最大化\n"
                "  · 默认录音设备 = VoiceMeeter Output\n"
                "  · 默认播放设备 = 扬声器\n"
                "  · 角色配置里的会话不含真实联系人\n\n"
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
                env = self._child_env()
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
        # ① 先确认 GPT-SoVITS 装好了、路径也对 —— 这是本次加的保险措施
        if not self._ensure_deploy("tts"):
            return

        # ② 已经在线就不用重复启动
        if self._tts_online():
            messagebox.showinfo("已在线", "TTS 服务已在运行,无需重复启动。")
            return

        # ③ 启动
        runtime = os.path.join(self.tts_dir, "runtime", "python.exe")
        api = os.path.join(self.tts_dir, "api_v2.py")
        cfg_abs = os.path.join(self.tts_dir, "GPT_SoVITS", "configs", "tts_infer.yaml")
        if not os.path.isfile(cfg_abs):
            messagebox.showerror(
                "整合包不完整",
                "找不到配置文件:\n%s\n\n这个 GPT-SoVITS 目录看起来不完整,\n"
                "请重新解压整合包,或用『选择…』指定正确的目录。" % cfg_abs)
            return

        def work():
            self.root.after(0, self.log, "[TTS] 启动 api_v2.py(首次加载模型约 20~60 秒)…")
            self.root.after(0, self.log, "[TTS] 目录 %s" % self.tts_dir)
            env = self._child_env()
            env["PYTHONPATH"] = self.tts_dir
            p = subprocess.Popen(
                [runtime, api, "-a", "127.0.0.1", "-p", "9880",
                 "-c", "GPT_SoVITS/configs/tts_infer.yaml"],
                cwd=self.tts_dir, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW)
            self.root.after(0, self.log, "[TTS] PID=%s" % p.pid)
            for line in p.stdout:
                self.root.after(0, self.log, line.rstrip("\n"))
        threading.Thread(target=work, daemon=True).start()


def _bootstrap_voice_bot():
    """首次运行时确保 voice_bot.py 存在(从 voice_bot.example.py 生成)。

    voice_bot.py 含个人配置,被 .gitignore 忽略;新用户 clone 下来没有它。
    没有这一步,面板里所有"发送/自检"按钮都会报"找不到 voice_bot.py"。
    失败不阻断面板启动 —— 只提示,让用户还能用配置面板。
    """
    try:
        sys.path.insert(0, TOOLS)
        import 初始化配置 as _init
        ok, action = _init.ensure(create=True, quiet=True)
        if action == "created":
            print("[配置] 已从模板生成 voice_bot.py(请先在「角色配置」里填对象)")
        return ok
    except Exception as e:
        print("[配置] 引导失败(不影响面板启动): %s" % str(e)[:80])
        return False


def main():
    _bootstrap_voice_bot()
    root = tk.Tk()
    Panel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
