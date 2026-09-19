# -*- coding: utf-8 -*-
"""用数据库核对:刚才是否真的发出了消息。(数据库是权威来源)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxauto_bootstrap

wxauto_bootstrap.setup()

import psutil
import wechatauto.db as _wdb


def _find_pids_via_psutil(self):
    out = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if (p.info["name"] or "").lower() == "weixin.exe":
                out.append(p.info["pid"])
        except Exception:
            pass
    return out


_wdb.WeChatDB._find_weixin_pids = _find_pids_via_psutil

from wechatauto import WeChatDB

db = WeChatDB()
print("账号:", db.get_self_info())

print()
print("=== 文件传输助手 最近 10 条 ===")
try:
    msgs = db.get_messages("filehelper", limit=10)
    print("条数:", len(msgs))
    for m in msgs:
        print("   [%s] type=%-8s sid=%-4s %s" % (
            m.get("create_time"), m.get("type"), m.get("sender_id"),
            str(m.get("content"))[:60]))
    if not msgs:
        print("   (空 —— 说明刚才的消息没有发出去)")
except Exception as e:
    print("读取失败:", type(e).__name__, e)

print()
print("=== 所有会话(看是否有新消息摘要) ===")
try:
    for s in db.get_sessions(limit=10):
        try:
            nick = db.get_nickname(s["username"])
        except Exception:
            nick = s["username"]
        print("   %-22s last=%s  %s" % (str(nick)[:22], s.get("last_time"),
                                        str(s.get("summary") or "")[:36]))
except Exception as e:
    print("失败:", type(e).__name__, e)
