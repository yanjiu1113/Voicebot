# -*- coding: utf-8 -*-
"""
发布前隐私审计 —— 回答"我现在能安全 push 吗?"

要点:本地删文件**不影响**推送内容。`git push` 真正会传出去的是:
  ① 当前跟踪的文件树
  ② **全部提交历史**(包括后来被删掉的文件 —— .gitignore 只挡新提交,不清理历史)
  ③ 提交信息 / 标签
  ④ .git/config 里的远程地址(是否内嵌凭据)

用法:
    python 02_工具面板/发布前隐私审计.py
    python 02_工具面板/发布前隐私审计.py --mask   # 命中内容掩码显示

退出码:0 = 可以推;1 = 发现阻断项,先处理。


⚠️ 修订记录(重要,别再犯)
    老版本第 [3] 项(扫历史内容)有**两个 bug,导致永远报"零命中"**:
      ① 写成 `git grep <模式> -- <一串提交>` —— 那个 `--` 会把提交当成
         **pathspec(路径)**,于是它其实只在工作区里找不存在的路径,
         **根本没扫历史**;
      ② 模式里用了 `(?=...)`/`(?<=...)` 环视,而 git grep -E 用的
         POSIX ERE **不支持环视** —— 会报错到 stderr,而老版本没检查
         返回码,于是又是空输出。
    结果:它给过"✅ 可以 push"的**假保证**,而真实微信号/昵称其实
    已经随历史推上去了。
    现在改为:**直接用 Python 读每一个 blob 来扫**(全功能正则 + 检查
    返回码 + 流式批量读取),不再经过 git grep 的语法限制。
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
         "请填", "abc123", "你的测试号", "文件传输助手", "<用户名>", "<KEY>",
         "你的微信号")

# 通用模式(与具体是谁无关)。现在是 Python 扫,支持环视。
# 每项:(名称, 正则, 是否阻断)
PATTERNS = [
    ("API Key (sk-)", re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), True),
    ("带前缀的 Key",
     re.compile(r"sk-(?:or|ant|proj)-[A-Za-z0-9_\-]{16,}"), True),
    ("Google AIza", re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), True),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), True),
    ("私有 Key 文件头", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), True),
    ("真实 wxid", re.compile(r"wxid_(?!x{6,})(?![0]{6,})[a-z0-9]{8,}"), True),
    ("疑似手机号/微信号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), True),
    # 名字必须真的像"密钥",否则会误伤 CABLE_PLAY_KEY 这类声卡设备名
    ("密钥赋非空值",
     re.compile(r"\b(?:[A-Z][A-Z0-9_]*_)?(?:API_KEY|ACCESS_KEY|SECRET_KEY"
                r"|AUTH_TOKEN|ACCESS_TOKEN)\s*=\s*(['\"])(?!\1)(?!YOUR)"
                r"[^'\"]{12,}\1"), True),
    ("登录密码赋非空值",
     re.compile(r"LOGIN_PASSWORD\s*=\s*(['\"])(?!\1)(?!YOUR)[^'\"]{3,}\1"), True),
    ("本机用户名路径",
     re.compile(r"[A-Za-z]:\\+Users\\+(?!<)[^\\\s\"']+"), False),
]

# 已知的真实联系人在此追加。本工具**不写死任何个人信息** ——
# 由本地文件提供(已加入 .gitignore,不会被提交)。
LOCAL_TOKENS_FILE = os.path.join(TOOLS, ".private_tokens.txt")


def git(*args):
    p = subprocess.run(["git", "-C", REPO] + list(args), capture_output=True)
    err = p.stderr.decode("utf-8", "replace")
    if p.returncode != 0 and err.strip():
        # 不再静默吞掉错误 —— 老版本正是栽在这里
        print("    (git %s 返回 %d: %s)"
              % (" ".join(args[:2]), p.returncode, err.strip().split("\n")[-1][:90]))
    return p.stdout.decode("utf-8", "replace")


def local_tokens():
    if not os.path.isfile(LOCAL_TOKENS_FILE):
        return []
    try:
        with open(LOCAL_TOKENS_FILE, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except Exception:
        return []


def all_objects():
    """(sha, path) —— 所有提交可达的对象(commit/tree/blob)。"""
    out = git("rev-list", "--objects", "--all")
    res = []
    for line in out.split("\n"):
        if not line.strip():
            continue
        p = line.split(" ", 1)
        res.append((p[0].strip(), p[1].strip() if len(p) == 2 else ""))
    return res


def batch_blobs(shas):
    """流式批量取内容。⚠️ 非 blob 对象的内容字节也必须跳过,否则流错位。"""
    if not shas:
        return {}
    p = subprocess.run(["git", "-C", REPO, "cat-file", "--batch"],
                       input=("\n".join(shas) + "\n").encode(),
                       capture_output=True)
    data, out, i = p.stdout, {}, 0
    while i < len(data):
        nl = data.find(b"\n", i)
        if nl < 0:
            break
        h = data[i:nl].decode("utf-8", "replace").split()
        i = nl + 1
        if len(h) >= 2 and h[1] == "missing":
            continue
        if len(h) < 3:
            continue
        try:
            size = int(h[2])
        except ValueError:
            break
        body = data[i:i + size]
        i += size + 1
        if h[1] == "blob":
            out[h[0]] = body
    return out


def scan(text, tokens):
    """返回 [(名称, 行号, 片段, 是否阻断)]"""
    hits = []
    for name, rx, blk in PATTERNS:
        for m in rx.finditer(text):
            v = m.group(0)
            if any(a in v for a in ALLOW):
                continue
            a = text.rfind("\n", 0, m.start()) + 1
            b = text.find("\n", m.start())
            line = text[a:b if b > 0 else len(text)]
            if any(al in line for al in ALLOW):
                continue
            hits.append((name, text[:m.start()].count("\n") + 1, v, blk))
    for t in tokens:
        for m in re.finditer(re.escape(t), text):
            hits.append(("本地词表命中", text[:m.start()].count("\n") + 1, t, True))
    return hits


def main():
    mask = "--mask" in sys.argv
    state = {"blocking": 0, "notes": 0}

    def report(label, items):
        if not items:
            print("  ✅ 无")
            return
        print("  %d 处:" % len(items))
        seen = set()
        for name, where, val, blk in items:
            key = (name, where)
            if key in seen:
                continue
            seen.add(key)
            state["blocking" if blk else "notes"] += 1
            v = val if not (mask and len(val) > 4) else val[:2] + "*" * 4 + val[-1]
            print("      %s [%s] %s" % ("⚠" if blk else "·", name, where))
            if blk and not mask:
                print("            %s" % v[:110])

    print("=" * 84)
    print("发布前隐私审计")
    print("=" * 84)
    tracked = [t for t in git("ls-files").split("\n") if t.strip()]
    revs = sorted(set(git("rev-list", "--all").split()))
    tags = [t for t in git("tag").split("\n") if t.strip()]
    tok = local_tokens()
    print("仓库          : %s" % REPO)
    print("分支          : %s" % git("rev-parse", "--abbrev-ref", "HEAD").strip())
    print("提交对象      : %d" % len(revs))
    print("跟踪文件      : %d" % len(tracked))
    print("标签          : %s" % (", ".join(tags) or "(无)"))
    print("本地词表      : %s"
          % ("已加载 %d 个词" % len(tok) if tok
             else "无(可在 02_工具面板/.private_tokens.txt 加自己已知的隐私词)"))
    print()

    print("-" * 84)
    print("[1] 跟踪文件的【文件名】")
    print("-" * 84)
    items = []
    for f in tracked:
        for name, rx, blk in PATTERNS:
            if rx.search(f):
                items.append((name, f, f, blk))
        for t in tok:
            if t in f:
                items.append(("本地词表命中", f, t, True))
    report("文件名", items)

    print()
    print("-" * 84)
    print("[2] 【历史】里出现过的文件名(含后来删掉的)")
    print("-" * 84)
    pairs = all_objects()
    ever = set(p for _, p in pairs if p)
    items = []
    for f in sorted(ever):
        for name, rx, blk in PATTERNS:
            if rx.search(os.path.basename(f)):
                items.append((name, f, f, blk))
        for t in tok:
            if t in f:
                items.append(("本地词表命中", f, t, True))
    for f in sorted(ever):
        if os.path.basename(f) in ("voice_bot.py", "config.py",
                                   "reference_config.json",
                                   "voice_bot_config.local.py"):
            items.append(("个人配置文件曾进历史(仅提示)", f, "", False))
    report("历史文件名", items)

    print()
    print("-" * 84)
    print("[3] 【当前树 + 全部历史】文件内容 —— 直接读 blob 扫")
    print("-" * 84)
    sha2paths = {}
    for sha, path in pairs:
        sha2paths.setdefault(sha, set()).add(path or "(未知)")
    blobs = batch_blobs(sorted(sha2paths))
    print("  可达对象 = %d,其中 blob 读到 = %d" % (len(sha2paths), len(blobs)))
    items = []
    for sha, raw in blobs.items():
        text = raw.decode("utf-8", "replace")
        for name, ln, v, blk in scan(text, tok):
            for p in sorted(sha2paths.get(sha, {"?"})):
                if not p:
                    continue
                items.append((name, "%s L%d (blob %s)" % (p, ln, sha[:8]), v, blk))
    report("内容", items)

    print()
    print("-" * 84)
    print("[4] 提交信息(标题 + 正文)")
    print("-" * 84)
    msgs = git("log", "--all", "--format=%h%x09%s%x09%b")
    items = []
    for line in msgs.split("\n"):
        if not line.strip():
            continue
        for name, ln, v, blk in scan(line, tok):
            items.append((name, line[:64], v, blk))
    report("提交信息", items)

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
            state["blocking"] += 1

    print()
    print("=" * 84)
    if state["blocking"] == 0:
        print("结论: ✅ 可以 push —— 当前树与全部历史均未发现敏感信息")
    else:
        print("结论: ⚠ 发现 %d 处阻断项,先处理再 push" % state["blocking"])
        print("      注意:**删掉文件不等于清掉历史** —— 已提交过的内容必须")
        print("      改写历史并强推,才能从远程移除。")
    if state["notes"]:
        print("      (另有 %d 条提示项,不阻断)" % state["notes"])
    print("=" * 84)
    print()
    print("提醒:本工具只检查**仓库内容**。仓库是否为公开(public)属于 GitHub 设置。")
    return 0 if state["blocking"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
