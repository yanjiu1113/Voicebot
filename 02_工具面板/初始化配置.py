# -*- coding: utf-8 -*-
"""
首次运行引导 —— 确保 01_核心模块/voice_bot.py 存在

【为什么需要这个】
voice_bot.py 里含**个人配置**(CHATS:要自动回复哪些会话、角色 prompt),
所以它被 .gitignore 忽略,仓库里只发布 voice_bot.example.py 模板。
新用户 clone 下来**没有 voice_bot.py**,而所有入口都会直接调用它:

    启动.cmd 的 [5] 自检 / [6] 演练 / [7] 实际发送
    VoiceBot控制面板.pyw
    character_webui.py(角色配置 WebUI)

缺文件时它们会直接报错。本脚本负责在第一次运行时从模板生成。

【用法】
    python 02_工具面板/初始化配置.py            # 缺了就创建
    python 02_工具面板/初始化配置.py --check     # 只检查,不创建
    python 02_工具面板/初始化配置.py --quiet     # 什么都不缺时不输出

返回码:0 = 可用(已存在或已创建);1 = 不可用(缺模板)
"""
from __future__ import print_function

import io
import os
import shutil
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
CORE = os.path.join(ROOT, "01_核心模块")
TARGET = os.path.join(CORE, "voice_bot.py")
TEMPLATE = os.path.join(CORE, "voice_bot.example.py")

# 生成后的补充提示:写进文件头部,让新用户一眼知道要改哪里
HEADER = """# -*- coding: utf-8 -*-
# ============================================================================
#  ★ 这是你自己的配置文件(从 voice_bot.example.py 复制而来)
#
#  本文件已被 .gitignore 忽略,**不会**被提交到 Git 仓库 ——
#  所以你可以放心在这里写真实微信号、真实昵称和角色设定。
#
#  下一步:
#    1. 找到下面的 CHATS,把 "请填对方的微信号或昵称" 换成真实对象
#       (key 可以是 wxid / 微信号 / 昵称,程序会自动解析)
#    2. 把 row 改成该会话在**界面**列表里的行号(0 起);
#       建议把目标会话在微信里【置顶】,行号才稳定。
#       行号不对也不会发错人 —— 发送前会 OCR 核对 name。
#    3. 也可以直接双击 02_工具面板/VoiceBot控制面板.pyw 用界面配置。
#
#  查当前真实行号:  python 01_核心模块/voice_bot.py --list
# ============================================================================
"""


def ensure(create=True, quiet=False):
    """确保 voice_bot.py 存在。

    Returns: (ok: bool, action: str)  action ∈ {exists, created, no_template}
    """
    def say(*a):
        if not quiet:
            print(*a)

    if os.path.isfile(TARGET):
        say("  [配置] voice_bot.py 已存在 ✓")
        return True, "exists"

    if not os.path.isfile(TEMPLATE):
        print("  [配置] ✗ 既没有 voice_bot.py,也找不到模板 voice_bot.example.py")
        print("          预期模板位置:%s" % TEMPLATE)
        print("          请确认下载完整(或从 Git 仓库重新 clone)。")
        return False, "no_template"

    if not create:
        say("  [配置] voice_bot.py 不存在(加 --check 时不会自动创建)")
        return False, "no_template"

    shutil.copyfile(TEMPLATE, TARGET)
    # 在文件头插入提示,不改动模板本身的正文
    with io.open(TARGET, encoding="utf-8") as f:
        body = f.read()
    if not body.startswith("# -*- coding: utf-8 -*-\n# ===="):
        with io.open(TARGET, "w", encoding="utf-8", newline="\n") as f:
            f.write(HEADER + body)

    print("  [配置] 已从模板生成 voice_bot.py ✓")
    print("          %s" % TARGET)
    print("          该文件不受 Git 管理,可以放心填真实微信号/昵称。")
    return True, "created"


def main():
    argv = sys.argv[1:]
    quiet = "--quiet" in argv
    check_only = "--check" in argv
    ok, action = ensure(create=not check_only, quiet=quiet)
    if ok and action == "created" and not quiet:
        print()
        print("  ⚠️ 这是模板配置,还没填真实对象。")
        print("     请编辑 CHATS(或双击 02_工具面板/VoiceBot控制面板.pyw 用界面配置),")
        print("     否则程序不会回复任何人。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
