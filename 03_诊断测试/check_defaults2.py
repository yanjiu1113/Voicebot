# -*- coding: utf-8 -*-
"""核实当前默认播放/录音设备,判断是否存在自环风险。"""
import os
import sys
import comtypes
comtypes.CoInitialize()

WD = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "WeChatBot_WXAUTO_SE-3.28")
sys.path.insert(0, os.path.join(WD, "vendor_py"))

from comtypes import GUID, COMMETHOD, IUnknown, HRESULT
from ctypes import c_int, POINTER, c_wchar_p
from ctypes.wintypes import DWORD

CLSID_ENUM = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_ENUM = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Activate",
                  (["in"], GUID, "iid"), (["in"], DWORD, "ctx"),
                  (["in"], POINTER(DWORD), "params"),
                  (["out"], POINTER(POINTER(IUnknown)), "pp")),
        COMMETHOD([], HRESULT, "OpenPropertyStore",
                  (["in"], DWORD, "stgm"),
                  (["out"], POINTER(POINTER(IUnknown)), "pp")),
        COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(c_wchar_p), "pp")),
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(DWORD), "pdw")),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = IID_ENUM
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints",
                  (["in"], c_int, "flow"), (["in"], DWORD, "mask"),
                  (["out"], POINTER(POINTER(IUnknown)), "pp")),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                  (["in"], c_int, "flow"), (["in"], c_int, "role"),
                  (["out"], POINTER(POINTER(IMMDevice)), "pp")),
        COMMETHOD([], HRESULT, "GetDevice",
                  (["in"], c_wchar_p, "id"),
                  (["out"], POINTER(POINTER(IMMDevice)), "pp")),
    ]


from comtypes.client import CreateObject
enum = CreateObject(CLSID_ENUM, interface=IMMDeviceEnumerator)

render = enum.GetDefaultAudioEndpoint(0, 0).GetId()
capture = enum.GetDefaultAudioEndpoint(1, 0).GetId()

print("=" * 72)
print("当前默认设备")
print("=" * 72)
print("  默认播放(eRender)  :", render)
print("  默认录音(eCapture) :", capture)
print()

# 从注册表把 GUID 映射成名字
import winreg
def name_of(guid_full):
    guid = guid_full.strip("{}").split("}.")[-1].strip("{}").lower()
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio"
    for kind in ("Render", "Capture"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + "\\" + kind) as k:
                n = winreg.QueryInfoKey(k)[0]
                for i in range(n):
                    g = winreg.EnumKey(k, i)
                    if g.lower() == guid:
                        with winreg.OpenKey(k, g + r"\Properties") as pk:
                            for vn in ("{a45c254e-df1c-4efd-8020-67d146a850e0},2",
                                       "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"):
                                try:
                                    return winreg.QueryValueEx(pk, vn)[0]
                                except Exception:
                                    pass
        except Exception:
            pass
    return "?"

rn = name_of(render)
cn = name_of(capture)
print("  播放设备名称 :", rn)
print("  录音设备名称 :", cn)
print()

print("=" * 72)
print("风险判断")
print("=" * 72)
r_is_line1 = "line 1" in str(rn).lower()
c_is_line1 = "line 1" in str(cn).lower()
print("  播放端是 Line 1 ? ", r_is_line1)
print("  录音端是 Line 1 ? ", c_is_line1)
print()
if r_is_line1 and c_is_line1:
    print("  >>> 警告:两端都是 Line 1,存在【自环】风险")
    print("      系统所有声音(含微信提示音)会灌进 Line 1,")
    print("      然后被 Line 1 自己录进去 -> 可能啸叫/杂音")
    print("      并且:你听不到任何声音(没有监听输出)")
    print()
    print("      建议:播放端改回扬声器,只保留录音端 = Line 1")
else:
    print("  >>> 无自环风险")
