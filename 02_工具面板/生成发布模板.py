# -*- coding: utf-8 -*-
"""
生成发布模板 voice_bot.example.py

把 01_核心模块/voice_bot.py 里的真实配置(CHATS: 真实微信号 / 昵称 / persona)
换成占位,输出 voice_bot.example.py 供发布到 Git 仓库。

voice_bot.py 本身被 .gitignore 忽略(含个人配置),仓库里只放这份模板。

自检方式(重要):
    本脚本**不硬编码任何个人信息**(否则它自己就成了泄漏源)。
    它改为**从 voice_bot.py 里把 CHATS 的 key / name / persona 指纹提取出来**,
    再回头确认这些东西**没有出现在生成的模板里**;另外叠加通用模式
    (wxid_xxx / sk-xxx / 本机用户名路径)。任何一项命中就中止,不写文件。

用法:
    python 02_工具面板/生成发布模板.py
"""
import ast
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
    #    界面顺序 = 置顶 + 最后消息时间,和 --list 的数据库顺序**不一样**,
    #    所以 --list 的序号不能直接当 row 用!
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

# 通用模式:与具体是谁无关,任何真值都算可疑
# 注意:`wxid_xxxxxxxxxxxxxxxx` 这类**占位符**要放过 —— 排除全同一个字符的情况。
GENERIC_PATTERNS = [
    (r"wxid_(?!x{6,})(?![0]{6,})[a-z0-9]{8,}", "疑似真实 wxid"),
    (r"sk-[A-Za-z0-9]{20,}", "疑似 API Key"),
    (r"[A-Za-z]:\\\\Users\\\\[^\\\\\s\"']+", "疑似本机用户目录(泄漏用户名)"),
]


def collect_secrets(src_text):
    """从源文件里提取它**自己的**敏感词 —— 脚本本身不写死任何个人信息。

    取 CHATS 的 key(真实微信号/昵称)与 persona 指纹。
    """
    secrets = set()
    m = re.search(r"^CHATS\s*=\s*(\{.*?^\})", src_text, re.S | re.M)
    if not m:
        return secrets
    try:
        chats = ast.literal_eval(m.group(1))
    except Exception:
        return secrets
    if not isinstance(chats, dict):
        return secrets
    for k, v in chats.items():
        if isinstance(k, str) and len(k) >= 4:
            secrets.add(k)
        if isinstance(v, dict):
            nm = v.get("name")
            if isinstance(nm, str) and len(nm) >= 2:
                secrets.add(nm)
            p = v.get("persona")
            if isinstance(p, str) and len(p) > 120:
                secrets.add(p[:60])          # persona 开头指纹
                secrets.add(p[-60:])         # persona 结尾指纹
    return secrets


def main():
    if not os.path.isfile(SRC):
        print("找不到 %s" % SRC)
        return 1
    with io.open(SRC, encoding="utf-8") as f:
        text = f.read()

    secrets = collect_secrets(text)
    print("从 voice_bot.py 提取到 %d 个敏感词(不显示内容)" % len(secrets))

    pat = re.compile(r"^CHATS = \{.*?^\}\n", re.S | re.M)
    # 用函数做替换,避免 re.sub 把替换串里的 \n 当转义处理
    new, n = pat.subn(lambda m: TEMPLATE, text)
    if n != 1:
        print("替换 CHATS 块失败,匹配到 %d 处" % n)
        return 1

    bad = sorted(s for s in secrets if s and s in new)
    if bad:
        print("‼ 发布版里仍有真实配置内容(%d 项),已中止,未写文件" % len(bad))
        for s in bad[:5]:
            print("     …%s…" % s[:24])
        return 1

    for rx, why in GENERIC_PATTERNS:
        mm = re.search(rx, new)
        if mm:
            print("‼ 命中通用风险模式:%s -> %s" % (why, mm.group(0)[:40]))
            print("   已中止,未写文件")
            return 1

    with io.open(DST, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)

    import py_compile
    py_compile.compile(DST, doraise=True)

    print("已生成 %s(%d 字节)" % (DST, len(new)))
    print("敏感词自检:通过 ✓(零命中)")
    print("通用模式自检:通过 ✓")
    print("语法检查:通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
