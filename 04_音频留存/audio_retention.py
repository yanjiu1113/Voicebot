# -*- coding: utf-8 -*-
"""
audio_retention.py —— 音频文件留存管理(自动清理,防止占满磁盘)

策略(三重限制,任一超出即清理最旧文件):
    MAX_FILES     最大文件数
    MAX_TOTAL_MB  最大总占用(MB)
    MAX_AGE_DAYS  最长保留天数

被管理的目录:
    · 音频目录(合成的 wav、诊断录音)
    · 发送截图目录

保护规则:
    · 永不删除 24 小时内生成的文件(避免误删正在用的)
    · 永不删除显式列入 KEEP 的文件
    · 只删白名单后缀(.wav/.png/.jpg)

用法:
    from audio_retention import cleanup
    cleanup()                      # 按配置清理
    cleanup(dry_run=True)          # 只报告不删除
    python audio_retention.py --report
    python audio_retention.py
"""

import argparse
import glob
import json
import os
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_HERE, "retention_config.json")

DEFAULTS = {
    "enabled": True,
    "max_files": 200,          # 最多保留多少个音频/截图文件
    "max_total_mb": 300,       # 总占用上限(MB)
    "max_age_days": 7,         # 最长保留天数
    "min_age_hours": 24,       # 这个时间内绝不删除
    "extensions": [".wav", ".png", ".jpg", ".jpeg"],
    "keep_names": [],          # 显式保留的文件名(不含路径)
    # 被管理的目录(相对本文件所在目录,或绝对路径)
    "dirs": [
        "04_音频留存",
        "..",
        "../WeChatBot_WXAUTO_SE-3.28/Voice_Temp",
        "../voice_bot_shots",
        "../WeChatBot_WXAUTO_SE-3.28/voice_bot_shots",
    ],
}


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                cfg.update(user)
        except Exception as e:
            print("[留存] 配置读取失败,使用默认值:", e)
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _resolve_dirs(cfg):
    out = []
    for d in cfg.get("dirs", []):
        p = d if os.path.isabs(d) else os.path.normpath(os.path.join(_HERE, d))
        if os.path.isdir(p) and p not in out:
            out.append(p)
    return out


def collect(cfg):
    """收集所有被管理的文件,返回 [(path, size, mtime), ...] 按时间升序。"""
    exts = tuple(e.lower() for e in cfg.get("extensions", [".wav"]))
    keep = set(cfg.get("keep_names") or [])
    files = []
    for d in _resolve_dirs(cfg):
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            if not name.lower().endswith(exts):
                continue
            if name in keep:
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            files.append((p, st.st_size, st.st_mtime))
    files.sort(key=lambda x: x[2])          # 旧的在前
    return files


def plan(cfg, files):
    """算出要删除哪些文件。返回 (删除列表, 统计信息)。"""
    now = time.time()
    min_age = cfg.get("min_age_hours", 24) * 3600
    max_age = cfg.get("max_age_days", 7) * 86400
    max_files = int(cfg.get("max_files", 200))
    max_bytes = float(cfg.get("max_total_mb", 300)) * 1024 * 1024

    protected, victims = [], []
    for p, size, mtime in files:
        if now - mtime < min_age:
            protected.append((p, size, mtime))
        elif now - mtime > max_age:
            victims.append((p, size, mtime))
        else:
            protected.append((p, size, mtime))

    # 在"未超龄"的文件里,按数量与总量继续裁剪(最旧的先删)
    rest = list(protected)
    total = sum(s for _, s, _ in rest)
    while rest and (len(rest) > max_files or total > max_bytes):
        p, s, m = rest.pop(0)
        victims.append((p, s, m))
        total -= s

    kept = rest
    info = {
        "total_files": len(files),
        "total_mb": sum(s for _, s, _ in files) / 1048576.0,
        "victims": len(victims),
        "victims_mb": sum(s for _, s, _ in victims) / 1048576.0,
        "kept": len(kept),
        "kept_mb": sum(s for _, s, _ in kept) / 1048576.0,
        "protected_recent": len([1 for _, _, m in files if now - m < min_age]),
    }
    return victims, info


def cleanup(dry_run=False, verbose=True, cfg=None):
    cfg = cfg or load_config()
    if not cfg.get("enabled", True):
        if verbose:
            print("[留存] 已禁用,跳过清理")
        return {"disabled": True}

    files = collect(cfg)
    victims, info = plan(cfg, files)

    if verbose:
        print("=" * 66)
        print("音频留存清理%s" % ("(演练)" if dry_run else ""))
        print("=" * 66)
        print("  管理目录: %d 个" % len(_resolve_dirs(cfg)))
        print("  现有文件: %d 个, 共 %.1f MB" % (info["total_files"], info["total_mb"]))
        print("  限额    : %d 个 / %.0f MB / %d 天" % (
            cfg["max_files"], cfg["max_total_mb"], cfg["max_age_days"]))
        print("  受保护(%.0fh 内): %d 个" % (cfg["min_age_hours"], info["protected_recent"]))
        print("  待删除  : %d 个, %.1f MB" % (info["victims"], info["victims_mb"]))
        print("  清理后  : %d 个, %.1f MB" % (info["kept"], info["kept_mb"]))

    deleted = 0
    freed = 0
    for p, size, _ in victims:
        if dry_run:
            if verbose:
                print("   [演练] 将删除 %s (%.1f KB)" % (os.path.basename(p), size / 1024.0))
            continue
        try:
            os.remove(p)
            deleted += 1
            freed += size
        except OSError as e:
            if verbose:
                print("   [失败] %s: %s" % (os.path.basename(p), e))

    if verbose:
        if dry_run:
            print("\n  演练结束,未删除任何文件")
        else:
            print("\n  已删除 %d 个文件,释放 %.1f MB" % (deleted, freed / 1048576.0))
    return {"deleted": deleted, "freed_mb": freed / 1048576.0, "info": info}


def report(cfg=None):
    cfg = cfg or load_config()
    files = collect(cfg)
    print("=" * 66)
    print("音频留存现状")
    print("=" * 66)
    for d in _resolve_dirs(cfg):
        n = len([1 for p, _, _ in files if os.path.dirname(p) == d])
        sz = sum(s for p, s, _ in files if os.path.dirname(p) == d)
        print("  %-52s %3d 个  %7.1f MB" % (d.replace(_HERE, ".")[:52], n, sz / 1048576.0))
    print("  " + "-" * 62)
    print("  合计 %d 个, %.1f MB" % (len(files), sum(s for _, s, _ in files) / 1048576.0))
    print()
    print("  限额: %d 个 / %.0f MB / %d 天 (%.0fh 内受保护)" % (
        cfg["max_files"], cfg["max_total_mb"], cfg["max_age_days"], cfg["min_age_hours"]))
    # 最旧的几个
    if files:
        print()
        print("  最旧的 5 个:")
        for p, s, m in files[:5]:
            print("    %s  %.1f KB  %s" % (
                os.path.basename(p), s / 1024.0,
                time.strftime("%m-%d %H:%M", time.localtime(m))))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="音频留存管理")
    ap.add_argument("--report", action="store_true", help="只显示现状")
    ap.add_argument("--dry-run", action="store_true", help="演练,不真删")
    args = ap.parse_args()

    if args.report:
        report()
    else:
        cleanup(dry_run=args.dry_run)
