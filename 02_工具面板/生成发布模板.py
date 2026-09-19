# -*- coding: utf-8 -*-
"""
生成发布模板 voice_bot.example.py

把 01_核心模块/voice_bot.py 里的真实配置(CHATS: 真实微信号/昵称/persona)
换成占位,输出 voice_bot.example.py 供发布到 Git 仓库。

voice_bot.py 本身被 .gitignore 忽略(含个人配置),仓库里只放这份模板。

用法:
    python 02_工具面板/生成发布模板.py
"""
import io
import os
import re
import sys

CORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "01_核心模块")
SRC = os.path.join(CORE, "voice_bot.py")
DST = os.path.join(CORE, "voice_bot.example.py")

TEMPLATE = '''CHATS = {
    # key 可以填三种之一,程序会自动解析:
    #     wxid(如 wxid_abc123)  /  微信号  /  昵称
    # 用  python voice_bot.py --list  查看所有会话的真实 username。
    #
    # ⚠️ row 是"会话在**界面**列表里的行号"(0 起,置顶会话也算)。
    #    界面顺序 = 置顶 + 最后消息时间,和 --list 的数据库顺序**不一样**。
    #    行号会随时间漂移 —— 强烈建议把要自动回复的会话【置顶】,
    #    这样行号才稳定。即使填错也不会发错人:发送前会 OCR 核对标题。
    "请填对方的微信号或昵称": {
        "name": "会话列表里显示的昵称",   # 用于 OCR 核对,防止发错人
        "row": 0,                          # 界面列表行号(0 起)
        "enabled": True,                   # False = 临时停用这个角色
        "persona": (
            "# 任务\\n"
            "你需要扮演指定角色,根据角色的经历,模仿她的语气进行线上日常对话。\\n\\n"
            "# 角色\\n"
            "在这里写角色设定……\\n\\n"
            "# 输出要求\\n"
            "回答尽量简短,控制在 30 字以内。使用中文。\\n"
            "只输出语言,不要旁白/括号动作。\\n"
        ),
    },
    # 想加第二个角色就复制上面这段,改 key / name / row / persona。
}
'''

# 不允许出现在发布版里的真实标识
SENSITIVE = ("你的微信号", "会话B", "爱丽希雅", "逐火之蛾",
             "DEEPSEEK_API_KEY = 'sk", "sk-")


def main():
    if not os.path.isfile(SRC):
        print("找不到 %s" % SRC)
        return 1
    with io.open(SRC, encoding="utf-8") as f:
        text = f.read()

    pat = re.compile(r"^CHATS = \{.*?^\}\n", re.S | re.M)
    # 用函数做替换,避免 re.sub 把替换串里的 \n 当转义处理
    new, n = pat.subn(lambda m: TEMPLATE, text)
    if n != 1:
        print("替换 CHATS 块失败,匹配到 %d 处" % n)
        return 1

    bad = [s for s in SENSITIVE if s in new]
    if bad:
        print("‼ 发布版里仍有敏感内容,已中止:%s" % bad)
        return 1

    with io.open(DST, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)

    import py_compile
    py_compile.compile(DST, doraise=True)

    print("已生成 %s(%d 字节)" % (DST, len(new)))
    print("敏感串自检:通过 ✓")
    print("语法检查:通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
