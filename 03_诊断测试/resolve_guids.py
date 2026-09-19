# -*- coding: utf-8 -*-
"""把设备 GUID 正确映射为名称。"""
import winreg

RENDER_GUID = "24fc69b0-2ca1-4adb-ae19-df74fd26e05c"
CAPTURE_GUID = "fbb85295-2c93-45fc-8cb4-85a89816d909"

BASE = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio"


def find(kind, guid):
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, BASE + "\\" + kind) as k:
            n = winreg.QueryInfoKey(k)[0]
            for i in range(n):
                g = winreg.EnumKey(k, i)
                if g.strip("{}").lower() == guid.lower():
                    with winreg.OpenKey(k, g + r"\Properties") as pk:
                        info = {}
                        for vn in ("{a45c254e-df1c-4efd-8020-67d146a850e0},2",
                                   "{b3f8fa53-0004-438e-9003-51a46e139bfc},6",
                                   "{a45c254e-df1c-4efd-8020-67d146a850e0},14"):
                            try:
                                info[vn.split(",")[-1]] = winreg.QueryValueEx(pk, vn)[0]
                            except Exception:
                                pass
                        return g, info
    except Exception as e:
        return None, {"err": str(e)}
    return None, {}


print("=" * 72)
print("默认设备名称解析")
print("=" * 72)

for kind, guid, label in (("Render", RENDER_GUID, "默认播放"),
                          ("Capture", CAPTURE_GUID, "默认录音")):
    g, info = find(kind, guid)
    print()
    print("  【%s】%s" % (label, kind))
    if g:
        for k, v in info.items():
            print("      %-6s : %s" % (k, v))
    else:
        print("      未在注册表找到该 GUID:", guid)
        print("      info:", info)
