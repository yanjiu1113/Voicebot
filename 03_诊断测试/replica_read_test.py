# -*- coding: utf-8 -*-
"""
只读验证:wechatauto-replica 能否读取本机微信数据。
不发送任何消息,不点击界面。

步骤:
  1. 导入
  2. 定位数据目录 + 提取密钥(内存只读扫描)
  3. get_self_info / get_sessions / get_messages
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxauto_bootstrap

wxauto_bootstrap.setup()

print("=" * 66)
print("1. 导入 wechatauto")
print("=" * 66)
try:
    import wechatauto
    print("  OK, 版本:", getattr(wechatauto, "__version__", "?"))
    print("  文件:", wechatauto.__file__)
except BaseException as e:
    print("  导入失败:", type(e).__name__, e)
    traceback.print_exc()
    raise SystemExit(1)

print()
print("=" * 66)
print("2. 数据目录自动定位")
print("=" * 66)
try:
    from wechatauto import auto_detect_db_dir
    d = auto_detect_db_dir()
    print("  db_dir =", d)
except BaseException as e:
    print("  定位失败:", type(e).__name__, e)

print()
print("=" * 66)
print("3. 打开数据库 (会做内存只读扫描取密钥,首次约 6s)")
print("=" * 66)

# 从 wechatauto.db 的函数签名看,它用 tasklist 找 PID。
# 本机 tasklist 在受控环境下返回码 1、stdout 为空,因此这里用 psutil
# 提供同样的 PID 列表(只替换这一个"找进程"的调用,不碰任何逻辑)。
try:
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
    print("  [shim] 已用 psutil 替换 tasklist 检测 PIDs")
    print("  [shim] PIDs =", _find_pids_via_psutil(None))
except Exception as e:
    print("  [shim] 失败:", e)

t0 = time.time()
try:
    from wechatauto import WeChatDB
    db = WeChatDB()
    print("  成功! 耗时 %.1fs" % (time.time() - t0))
    print("  account :", getattr(db, "account", "?"))
    print("  wxid    :", getattr(db, "wxid", "?"))
except BaseException as e:
    print("  失败 (%.1fs): %s: %s" % (time.time() - t0, type(e).__name__, e))
    traceback.print_exc()
    raise SystemExit(2)

print()
print("=" * 66)
print("4. 读取账号与会话")
print("=" * 66)
try:
    info = db.get_self_info()
    print("  self_info:", info)
except BaseException as e:
    print("  get_self_info 失败:", type(e).__name__, e)

try:
    t0 = time.time()
    sess = db.get_sessions(limit=15)
    print("  会话数(前15): %d   耗时 %.2fs" % (len(sess), time.time() - t0))
    for s in sess[:12]:
        try:
            nick = db.get_nickname(s["username"])
        except Exception:
            nick = "?"
        print("     %-26s unread=%-4s %s" % (str(nick)[:26], s.get("unread"),
                                              str(s.get("summary") or "")[:34]))
except BaseException as e:
    print("  get_sessions 失败:", type(e).__name__, e)
    traceback.print_exc()

print()
print("=" * 66)
print("5. 读取「文件传输助手」最近消息")
print("=" * 66)
try:
    msgs = db.get_messages("filehelper", limit=8)
    print("  消息数: %d" % len(msgs))
    for m in msgs[-8:]:
        print("     [%s] %s | %s" % (m.get("create_time"), m.get("type"),
                                     str(m.get("content"))[:52]))
except BaseException as e:
    print("  读取失败:", type(e).__name__, e)

print()
print("=" * 66)
print(">>> 只读验证结束,未发送任何消息")
print("=" * 66)
