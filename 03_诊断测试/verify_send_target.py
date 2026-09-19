# -*- coding: utf-8 -*-
"""核对刚才的语音条发到哪了、发了什么。数据库是权威来源。"""
import os
import sys
import time

WD = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "WeChatBot_WXAUTO_SE-3.28")
sys.path.insert(0, WD)
import wxauto_bootstrap
wxauto_bootstrap.setup()

import psutil
import wechatauto.db as _wdb


def _pids(self):
    out = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if (p.info["name"] or "").lower() == "weixin.exe":
                out.append(p.info["pid"])
        except Exception:
            pass
    return out


_wdb.WeChatDB._find_weixin_pids = _pids
from wechatauto import WeChatDB

db = WeChatDB()
me = db.get_self_info()
print("账号:", me["nick_name"], me["username"])
print()

for chat, label in (("filehelper", "文件传输助手"),):
    print("=" * 74)
    print("%s 最近 12 条" % label)
    print("=" * 74)
    try:
        msgs = db.get_messages(chat, limit=12)
        for m in msgs:
            ct = m.get("create_time")
            typ = m.get("type")
            sid = m.get("sender_id")
            content = str(m.get("content") or "")
            when = time.strftime("%H:%M:%S", time.localtime(int(ct))) if ct else "?"
            mark = "  <<< 语音" if "语音" in str(typ) else ""
            print("  [%s] type=%-8s sender=%-4s%s" % (when, typ, sid, mark))
            if content and "语音" not in str(typ):
                print("        %s" % content[:60])
    except Exception as e:
        print("  读取失败:", type(e).__name__, e)

print()
print("=" * 74)
print("测试号B —— 找它的 username")
print("=" * 74)
try:
    for s in db.get_sessions(limit=20):
        try:
            nick = db.get_nickname(s["username"])
        except Exception:
            nick = s["username"]
        if "铃兰" in str(nick) or "絮" in str(nick):
            print("  匹配: nick=%r username=%r last=%s" % (nick, s["username"], s.get("last_time")))
except Exception as e:
    print("  失败:", type(e).__name__, e)
