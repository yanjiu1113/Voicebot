# -*- coding: utf-8 -*-
"""
发布前隐私审计 —— 回答"我现在能安全 push 吗?"

要点:本地删文件**不影响**推送内容。`git push` 真正会传出去的是:
  ① 当前跟踪的文件树
  ② **全部提交历史**(包括后来被删掉的文件 —— .gitignore 只挡新提交,不清理历史)
  ③ 提交信息 / 标签
  ④ .git/config 里的远程地址(是否内嵌凭据)

所以本工具逐项扫这四样。

用法:
    python 02_工具面板/发布前隐私审计.py
    python 02_工具面板/发布前隐私审计.py --mask   # 命中内容也掩码显示

退出码:0 = 可以推;1 = 发现问题,先处理。
"""
from __future__ import print_function

import os
import re
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)

# 视为"允许出现"的占位符 / 通用名
ALLOW = ("YOUR_API_KEY", "YOUR_PASSWORD", "你的Key", "你的密码", "xxxxxxxx",
         "请填", "abc123", "你的测试号", "文件传输助手", "<用户名>")

# (名称, 正则, 是否算"阻断")
PATTERNS = [
    ("API Key (sk-)", re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), True),
    ("真实 wxid", re.compile(r"wxid_(?!x{6,})(?![0]{6,})[a-z0-9]{8,}"), True),
    ("API Key 赋非空值",
     re.compile(r"(?:API_)?KEY\s*=\s*(['\"])(?!\1)(?!YOUR)[^'\"]{8,}\1"), True),
    ("登录密码赋非空值",
     re.compile(r"LOGIN_PASSWORD\s*=\s*(['\"])(?!\1)(?!YOUR)[^'\"]{3,}\1"), True),
    ("本机用户名路径",
     re.compile(r"[A-Za-z]:\\+Users\\+(?!<)[^\\\s\"']+"), True),
    ("疑似真实微信号(11位手机号)",
     re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), False),
]

# 已知的真实联系人在此追加(本工具不写死任何个人信息,由本地文件提供)
LOCAL_TOKENS_FILE = os.path.join(TOOLS, ".private_tokens.txt")


def git(*args):
    p = subprocess.run(["git", "-C", REPO] + list(args), capture_output=True)
    return p.stdout.decode("utf-8", "replace")


def local_tokens():
    """可选:本地放一个 .private_tokens.txt(每行一个词),用于扫自己已知的隐私。"""
    if not os.path.isfile(LOCAL_TOKENS_FILE):
        return []
    try:
        with open(LOCAL_TOKENS_FILE, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except Exception:
        return []


def hits_in(text):
    out = []
    for name, rx, blocking in PATTERNS:
        for m in rx.finditer(text):
            v = m.group(0)
            if any(a in v for a in ALLOW):
                continue
            out.append((name, v, blocking))
    for t in local_tokens():
        if t in text:
            out.append(("本地词表命中", t, True))
    return out


def main():
    mask = "--mask" in sys.argv
    blocking_total = 0
    note_total = 0

    print("=" * 84)
    print("发布前隐私审计")
    print("=" * 84)
    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    ahead = git("rev-list", "--count", "origin/main..HEAD").strip() if \
        git("rev-parse", "--verify", "origin/main").strip() else "?"
    tracked = [t for t in git("ls-files").split("\n") if t.strip()]
    revs = sorted(set(git("rev-list", "--all").split()))
    print("仓库          : %s" % REPO)
    print("分支          : %s" % branch)
    print("待推送提交    : %s" % ahead)
    print("全部提交对象  : %d" % len(revs))
    print("跟踪文件      : %d" % len(tracked))
    print("标签          : %s" % ", ".join(t for t in git("tag").split("\n") if t.strip()))
    if local_tokens():
        print("本地词表      : 已加载 %d 个词" % len(local_tokens()))
    else:
        print("本地词表      : 无(可在 02_工具面板/.private_tokens.txt 里加自己已知的隐私词)")
    print()

    def show(items):
        nonlocal blocking_total, note_total
        for name, where, val, blocking in items:
            tag = "⚠" if blocking else "·"
            if blocking:
                blocking_total += 1
            else:
                note_total += 1
            v = val
            if mask and len(v) > 4:
                v = v[:2] + "*" * 4 + v[-1:]
            print("  %s [%s] %s" % (tag, name, where))
            if blocking:
                print("        %s" % v[:110])

    # 1. 跟踪文件名
    print("-" * 84)
    print("[1] 跟踪文件的【文件名】")
    print("-" * 84)
    items = []
    for f in tracked:
        for name, rx, blocking in PATTERNS:
            if rx.search(f):
                items.append((name, f, f, blocking))
    for t in local_tokens():
        for f in tracked:
            if t in f:
                items.append(("本地词表命中", f, t, True))
    if items:
        show(items)
    else:
        print("  ✅ 无")

    # 2. 历史文件名(含已删除的)
    print()
    print("-" * 84)
    print("[2] 【历史】里出现过的文件名(含后来删掉的)")
    print("-" * 84)
    ever = set()
    for line in git("rev-list", "--all", "--objects").split("\n"):
        p = line.split(" ", 1)
        if len(p) == 2:
            ever.add(p[1])
    items = []
    for f in sorted(ever):
        for name, rx, blocking in PATTERNS:
            if rx.search(os.path.basename(f)):
                items.append((name, f, f, blocking))
    # 个人配置文件是否曾进历史 —— 只提示(内容另行扫描)
    for f in sorted(ever):
        if os.path.basename(f) in ("voice_bot.py", "config.py",
                                   "reference_config.json",
                                   "voice_bot_config.local.py"):
            items.append(("个人配置文件曾进历史(仅提示,请看[3]内容扫描)",
                          f, "", False))
    if items:
        show(items)
    else:
        print("  ✅ 无")

    # 3. 历史内容
    print()
    print("-" * 84)
    print("[3] 【当前树 + 全部历史】文件内容")
    print("-" * 84)
    items = []
    if revs:
        for name, rx, blocking in PATTERNS:
            out = git("grep", "-I", "-n", "-E", rx.pattern, "--", *revs)
            for line in out.split("\n"):
                if not line.strip():
                    continue
                if any(a in line for a in ALLOW):
                    continue
                items.append((name, line[:120], line, blocking))
        for t in local_tokens():
            out = git("grep", "-I", "-n", "-F", "--", *revs, "-e", t)
            for line in out.split("\n"):
                if line.strip():
                    items.append(("本地词表命中", line[:120], line, True))
    if items:
        show(items)
    else:
        print("  ✅ 无")

    # 4. 提交信息
    print()
    print("-" * 84)
    print("[4] 提交信息(标题 + 正文)")
    print("-" * 84)
    msgs = git("log", "--all", "--format=%H%x09%an <%ae>%x09%s%x09%b")
    items = []
    for line in msgs.split("\n"):
        for name, val, blocking in hits_in(line):
            items.append((name, line[:70], val, blocking))
    if items:
        show(items)
    else:
        print("  ✅ 无")

    # 5. 远程地址
    print()
    print("-" * 84)
    print("[5] 远程地址是否内嵌凭据")
    print("-" * 84)
    cfg = os.path.join(REPO, ".git", "config")
    urls = []
    if os.path.isfile(cfg):
        with open(cfg, encoding="utf-8", errors="ignore") as f:
            urls = re.findall(r"url\s*=\s*(\S+)", f.read())
    if not urls:
        print("  (未配置远程 url)")
    for u in urls:
        cred = re.search(r"://[^/@]+@", u)
        print("  %s %s" % ("⚠ 内嵌凭据!" if cred else "✅", u))
        if cred:
            blocking_total += 1

    print()
    print("=" * 84)
    if blocking_total == 0:
        print("结论: ✅ 可以 push —— 当前树与全部历史均未发现 API Key / 真实微信个人信息")
        if note_total:
            print("      (另有 %d 条提示项,不影响推送)" % note_total)
    else:
        print("结论: ⚠ 发现 %d 处阻断问题,先处理再 push" % blocking_total)
    print("=" * 84)
    print()
    print("提醒:本工具只检查**仓库内容**。仓库是否为公开(public)属于 GitHub 设置,")
    print("      与此无关 —— 不想公开就去仓库 Settings 改成 Private。")
    return 0 if blocking_total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
