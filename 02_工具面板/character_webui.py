# -*- coding: utf-8 -*-
"""
character_webui.py —— 角色 / 会话 配置 Web UI

功能:
  · 可视化管理每个会话的角色设定(persona)与关联 prompt 文件
  · 一键从微信数据库导入会话(自动填 wxid / 名称)
  · 编辑行号(row)、启用开关、角色名
  · 保存后自动写回 voice_bot.py 的 CHATS(自动备份)
  · 可直接编辑 prompts/*.md 角色文件

启动:  双击本文件,或  python character_webui.py
浏览器: http://127.0.0.1:5100

仅监听本机(127.0.0.1),不对外暴露。
"""

import ast
import io
import json
import os
import re
import shutil
import sys
import threading
import time
import webbrowser

from flask import Flask, jsonify, request

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # VoiceBot/
DESKTOP = os.path.dirname(ROOT)
PROJECT = os.path.join(DESKTOP, "WeChatBot_WXAUTO_SE-3.28")
CORE = os.path.join(ROOT, "01_核心模块")
VOICE_BOT = os.path.join(CORE, "voice_bot.py")
PROMPTS_DIR = os.path.join(PROJECT, "prompts")
BACKUP_DIR = os.path.join(ROOT, "05_文档", "配置备份")

# 让 voice_bot 的依赖可被导入
for p in (CORE, PROJECT, os.path.join(PROJECT, "vendor_py")):
    if p not in sys.path:
        sys.path.insert(0, p)

app = Flask(__name__)
PORT = 5100


# ---------------------------------------------------------------------------
# CHATS 读写(解析 voice_bot.py 里的字面量,不用 import,避免副作用)
# ---------------------------------------------------------------------------

_CHATS_RE = re.compile(r"^CHATS\s*=\s*\{", re.M)


def read_chats():
    """从 voice_bot.py 解析 CHATS 字典。返回 (dict, 报错信息)。"""
    if not os.path.isfile(VOICE_BOT):
        return {}, "找不到 voice_bot.py"
    src = io.open(VOICE_BOT, encoding="utf-8").read()
    m = _CHATS_RE.search(src)
    if not m:
        return {}, "voice_bot.py 里找不到 CHATS 定义"
    start = m.end() - 1                      # 指向 '{'
    depth = 0
    for i in range(start, len(src)):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                lit = src[start:i + 1]
                try:
                    return ast.literal_eval(lit), None
                except Exception as e:
                    return {}, "解析 CHATS 失败: %s" % e
    return {}, "CHATS 括号不闭合"


def write_chats(chats, backup_tag="webui"):
    """把 CHATS 写回 voice_bot.py(只替换那个字面量,保留其余内容与注释)。"""
    src = io.open(VOICE_BOT, encoding="utf-8").read()
    m = _CHATS_RE.search(src)
    if not m:
        raise RuntimeError("找不到 CHATS 定义")

    start = m.end() - 1
    depth = 0
    end = None
    for i in range(start, len(src)):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise RuntimeError("CHATS 括号不闭合")

    # 生成新字面量(手工排版,便于人读)
    lines = ["{"]
    for k, v in chats.items():
        name = v.get("name", "")
        row = int(v.get("row", 0))
        persona = v.get("persona", "")
        enabled = v.get("enabled", True)
        lines.append("    %r: {" % k)
        lines.append("        \"name\": %r," % name)
        lines.append("        \"row\": %d," % row)
        if not enabled:
            lines.append("        \"enabled\": False,")
        lines.append("        \"persona\": %r," % persona)
        lines.append("    },")
    lines.append("}")
    new_lit = "\n".join(lines)

    # 备份
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        shutil.copy2(VOICE_BOT, os.path.join(
            BACKUP_DIR, "voice_bot.py_%s.%s.bak" % (backup_tag, stamp)))
    except Exception:
        pass

    new_src = src[:start] + new_lit + src[end:]
    io.open(VOICE_BOT, "w", encoding="utf-8").write(new_src)


# ---------------------------------------------------------------------------
# prompts 文件
# ---------------------------------------------------------------------------

def list_prompts():
    if not os.path.isdir(PROMPTS_DIR):
        return []
    return sorted(f for f in os.listdir(PROMPTS_DIR) if f.endswith((".md", ".txt")))


def read_prompt(name):
    p = os.path.join(PROMPTS_DIR, os.path.basename(name))
    if not os.path.isfile(p):
        return None
    return io.open(p, encoding="utf-8", errors="replace").read()


def write_prompt(name, content):
    os.makedirs(PROMPTS_DIR, exist_ok=True)
    p = os.path.join(PROMPTS_DIR, os.path.basename(name))
    if not p.endswith((".md", ".txt")):
        p += ".md"
    try:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        if os.path.isfile(p):
            os.makedirs(BACKUP_DIR, exist_ok=True)
            shutil.copy2(p, os.path.join(
                BACKUP_DIR, "%s.%s.bak" % (os.path.basename(p), stamp)))
    except Exception:
        pass
    io.open(p, "w", encoding="utf-8").write(content)
    return os.path.basename(p)


# ---------------------------------------------------------------------------
# 微信会话扫描(可选,需要微信在运行)
# ---------------------------------------------------------------------------

DB_CACHE = {"data": None, "ts": 0, "err": None}


def scan_wechat_chats(timeout=60):
    """读微信数据库列出会话。失败返回错误信息。"""
    if DB_CACHE["data"] and time.time() - DB_CACHE["ts"] < 120:
        return DB_CACHE["data"], None

    result = {}

    def work():
        try:
            import wxauto_bootstrap
            wxauto_bootstrap.setup()
            import psutil
            import wechatauto.db as _wdb

            def _pids(self):
                out = []
                for pr in psutil.process_iter(["pid", "name"]):
                    try:
                        if (pr.info["name"] or "").lower() == "weixin.exe":
                            out.append(pr.info["pid"])
                    except Exception:
                        pass
                return out
            _wdb.WeChatDB._find_weixin_pids = _pids
            from wechatauto import WeChatDB
            db = WeChatDB()
            rows = []
            for i, s in enumerate(db.get_sessions(limit=40)):
                u = s.get("username")
                try:
                    nick = db.get_nickname(u)
                except Exception:
                    nick = "?"
                try:
                    n = len(db.get_messages(u, limit=3))
                except Exception:
                    n = -1
                rows.append({"row": i, "username": u, "nick": str(nick), "msgs": n})
            result["rows"] = rows
            result["self"] = db.get_self_info()
        except Exception as e:
            result["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, "扫描超时(微信是否已登录?)"
    if "error" in result:
        return None, result["error"]
    DB_CACHE.update(data=result, ts=time.time(), err=None)
    return result, None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.route("/api/state")
def api_state():
    chats, err = read_chats()
    return jsonify({
        "ok": err is None,
        "error": err,
        "chats": chats,
        "prompts": list_prompts(),
        "voice_bot": VOICE_BOT,
        "prompts_dir": PROMPTS_DIR,
    })


@app.route("/api/save", methods=["POST"])
def api_save():
    payload = request.get_json(force=True) or {}
    chats = payload.get("chats")
    if not isinstance(chats, dict):
        return jsonify({"ok": False, "error": "chats 必须是对象"}), 400
    try:
        write_chats(chats)
        return jsonify({"ok": True, "count": len(chats)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/status")
def api_status():
    """报告语音回复进程是否在运行 —— 改配置后需要重启才生效。"""
    procs = []
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                nm = (p.info["name"] or "").lower()
                if "python" not in nm:
                    continue
                cl = " ".join(p.info["cmdline"] or [])
                if "voice_bot" in cl:
                    procs.append({
                        "pid": p.info["pid"],
                        "live": "--live" in cl,
                        "since": p.info.get("create_time") or 0,
                    })
            except Exception:
                continue
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:120]})
    return jsonify({"ok": True, "running": procs, "need_restart": bool(procs)})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """终止正在运行的语音回复进程(方便改完配置立刻重启)。"""
    killed = []
    try:
        import psutil
        me = os.getpid()
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if p.info["pid"] == me:
                    continue
                nm = (p.info["name"] or "").lower()
                if "python" not in nm:
                    continue
                cl = " ".join(p.info["cmdline"] or [])
                if "voice_bot" in cl:
                    p.terminate()
                    try:
                        p.wait(timeout=5)
                    except psutil.TimeoutExpired:
                        p.kill()
                    killed.append(p.info["pid"])
            except Exception:
                continue
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:150]})
    return jsonify({"ok": True, "killed": killed})


@app.route("/api/prompt/<path:name>")
def api_prompt_get(name):
    c = read_prompt(name)
    if c is None:
        return jsonify({"ok": False, "error": "不存在"}), 404
    return jsonify({"ok": True, "name": name, "content": c})


@app.route("/api/prompt", methods=["POST"])
def api_prompt_save():
    p = request.get_json(force=True) or {}
    name = (p.get("name") or "").strip()
    content = p.get("content", "")
    if not name:
        return jsonify({"ok": False, "error": "缺少文件名"}), 400
    try:
        fn = write_prompt(name, content)
        return jsonify({"ok": True, "name": fn})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/wechat")
def api_wechat():
    data, err = scan_wechat_chats()
    if err:
        return jsonify({"ok": False, "error": err})
    return jsonify({"ok": True, **data})


# ---------------------------------------------------------------------------
# 前端页面
# ---------------------------------------------------------------------------

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>VoiceBot 角色配置</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; font-family:"Microsoft YaHei",system-ui,sans-serif;
         background:#eef1f5; color:#22303f; display:flex; height:100vh; }
  #side { width:260px; background:#2d3e50; color:#dfe8f0; display:flex;
          flex-direction:column; }
  #side h1 { font-size:15px; margin:0; padding:14px 16px; background:#243444;
             border-bottom:1px solid #1b2733; }
  #list { flex:1; overflow:auto; }
  .item { padding:11px 16px; cursor:pointer; border-bottom:1px solid #26374a;
          font-size:13px; }
  .item:hover { background:#37495e; }
  .item.on { background:#3d9aa8; color:#fff; font-weight:bold; }
  .item small { display:block; color:#8fa4b8; font-size:11px; margin-top:3px; }
  .item.on small { color:#d6f0f5; }
  #side .foot { padding:10px 12px; border-top:1px solid #1b2733; }
  button { font-family:inherit; font-size:13px; border:0; border-radius:4px;
           padding:7px 12px; cursor:pointer; color:#fff; background:#4a90d9; }
  button:hover { filter:brightness(1.1); }
  button.g { background:#2fa36b; } button.r { background:#d9534f; }
  button.s { background:#8a8f98; }
  button.wide { width:100%; }
  #main { flex:1; overflow:auto; padding:20px 24px; }
  h2 { margin:0 0 4px; font-size:18px; }
  .sub { color:#7a8899; font-size:12px; margin-bottom:16px; word-break:break-all; }
  .card { background:#fff; border:1px solid #dde3ea; border-radius:8px;
          padding:16px 18px; margin-bottom:16px; }
  label { display:block; font-size:12px; color:#5a6a7a; margin:10px 0 4px;
          font-weight:bold; }
  input[type=text], input[type=number], textarea, select {
      width:100%; font-family:inherit; font-size:13px; padding:8px 10px;
      border:1px solid #cfd8e3; border-radius:4px; background:#fbfdff; }
  textarea { resize:vertical; line-height:1.6; }
  .row { display:flex; gap:14px; }
  .row > div { flex:1; }
  .hint { font-size:11px; color:#8a98a8; margin-top:4px; }
  .bar { display:flex; gap:8px; flex-wrap:wrap; margin-top:16px; }
  #toast { position:fixed; right:20px; bottom:20px; background:#22303f; color:#fff;
           padding:12px 18px; border-radius:6px; font-size:13px; opacity:0;
           transition:.25s; pointer-events:none; max-width:420px; }
  #toast.show { opacity:1; }
  #toast.err { background:#b93b36; }
  .pill { display:inline-block; background:#e6edf5; color:#42586e; border-radius:10px;
          padding:2px 9px; font-size:11px; margin-left:6px; }
  .pill.off { background:#f5e6e6; color:#a33; }
  #banner { background:#fff4e5; border:1px solid #f0c48a; color:#8a5a12;
            border-radius:6px; padding:10px 14px; margin-bottom:14px;
            font-size:13px; line-height:1.6; }
  #banner button { background:#d99a3d; padding:5px 12px; font-size:12px;
                   margin-left:8px; }
  #banner b { color:#b93b36; }
</style>
</head>
<body>
<div id="side">
  <h1>VoiceBot 角色配置</h1>
  <div id="list"></div>
  <div class="foot">
    <button class="wide g" onclick="addChar()">＋ 新增角色</button>
    <div style="height:6px"></div>
    <button class="wide" onclick="importFromWeChat()">从微信导入会话</button>
    <div style="height:6px"></div>
    <button class="wide s" onclick="delAllDisabled()">批量删除「不监视」</button>
  </div>
</div>

<div id="main">
  <div id="banner" style="display:none"></div>
  <h2 id="title">请选择左侧角色</h2>
  <div class="sub" id="subtitle"></div>
  <div id="editor" style="display:none">
    <div class="card">
      <div class="row">
        <div>
          <label>角色名(仅备注,显示用)</label>
          <input type="text" id="f_name">
        </div>
        <div>
          <label>会话标识(填微信号 / 昵称 / wxid 都行)</label>
          <input type="text" id="f_key" oninput="onKeyInput()">
          <div class="hint" id="f_key_hint">
            推荐填<b>微信号</b>(好友资料页可见)。也可填昵称或 wxid。<br>
            程序会自动解析成微信内部 ID，填哪种都能工作。
          </div>
        </div>
      </div>
      <div class="row">
        <div>
          <label>会话列表行号 row</label>
          <input type="number" id="f_row" min="0" value="0">
          <div class="hint">会话列表从上往下数的行号(0 起)。<b>会随聊天活跃漂移</b>,
            建议把对象在微信里置顶以固定。</div>
        </div>
        <div>
          <label>是否监视这个会话</label>
          <select id="f_enabled">
            <option value="1">监视并回复</option>
            <option value="0">不监视(完全忽略)</option>
          </select>
          <div class="hint">选「不监视」则该会话<b>完全不读取、不回复</b>。</div>
        </div>
      </div>
      <label>关联 prompt 文件(可选,填了可一键载入)</label>
      <select id="f_prompt" onchange="loadPromptInto()"></select>
    </div>

    <div class="card">
      <label>角色设定 persona(实际发给 AI 的系统提示词)</label>
      <textarea id="f_persona" rows="12"></textarea>
      <div class="hint">留空则用简短默认设定。想用长设定,可把 prompts 文件内容粘贴进来。</div>
      <div class="bar">
        <button class="g" onclick="saveChar()">保存</button>
        <button class="s" onclick="load()">放弃修改</button>
      </div>
    </div>

    <div class="card" style="border-color:#e8b6b3;background:#fff7f7">
      <label style="color:#b93b36">危险操作</label>
      <div class="hint" style="margin-bottom:8px">
        删除后这个角色会立刻从 <code>voice_bot.py</code> 的 CHATS 中移除，
        不再监视该会话。此操作可撤销：保存前的版本都在「05_文档\配置备份」里。
      </div>
      <button class="r" onclick="delChar()" style="font-size:14px;padding:10px 20px">
        🗑 删除这个角色（停止监视该会话）
      </button>
    </div>

    <div class="card">
      <label>prompts 文件内容(与 WeChatBot 的 prompts/ 同目录)</label>
      <textarea id="f_file" rows="10" placeholder="选择上方 prompt 文件后在此编辑"></textarea>
      <div class="bar">
        <button class="g" onclick="savePromptFile()">保存到 prompt 文件</button>
        <button onclick="loadPromptFile()">重新载入</button>
      </div>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
let STATE = { chats:{}, prompts:[] };
let CUR = null;

function toast(msg, err){
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'show' + (err ? ' err' : '');
  setTimeout(()=>{ t.className=''; }, 3200);
}

async function load(){
  const r = await fetch('/api/state').then(x=>x.json());
  if(!r.ok){ toast('读取失败: '+(r.error||''), true); return; }
  STATE = r;
  render();
  checkRunning();
}

async function checkRunning(){
  const b = document.getElementById('banner');
  try{
    const s = await fetch('/api/status').then(x=>x.json());
    if(s.ok && s.running && s.running.length){
      const live = s.running.some(x=>x.live);
      b.style.display = 'block';
      b.innerHTML = '<b>⚠ 语音回复正在运行</b>（' +
        s.running.map(x=>'PID '+x.pid+(x.live?' 实际发送':' 演练')).join('、') +
        '）<br>你在这里的修改<b>不会立刻生效</b>，需要重启语音回复进程。' +
        '<button onclick="stopBot()">立即停止它</button>' +
        '<button onclick="checkRunning()" style="background:#8a8f98">刷新状态</button>';
    } else {
      b.style.display = 'none';
    }
  }catch(e){ b.style.display = 'none'; }
}

async function stopBot(){
  if(!confirm('终止正在运行的语音回复进程？\n\n（改完配置后重新启动即可生效）')) return;
  const r = await fetch('/api/stop', {method:'POST'}).then(x=>x.json());
  if(r.ok){
    toast('已终止 ' + (r.killed.length ? r.killed.join(', ') : '(没有进程)'));
    checkRunning();
  } else {
    toast('终止失败: ' + (r.error||''), true);
  }
}

function render(){
  const L = document.getElementById('list');
  L.innerHTML = '';
  const keys = Object.keys(STATE.chats);
  if(!keys.length){
    L.innerHTML = '<div style="padding:16px;color:#8fa4b8;font-size:12px">'+
                  '还没有角色。<br><br>点下面「新增角色」或<br>「从微信导入会话」。</div>';
  }
  keys.forEach(k=>{
    const c = STATE.chats[k];
    const d = document.createElement('div');
    d.className = 'item' + (k===CUR ? ' on' : '');
    const off = (c.enabled === false)
      ? ' <span class="pill off">不监视</span>'
      : ' <span class="pill">监视中</span>';
    d.innerHTML = (c.name||'(未命名)') + off +
      '<small>' + k + ' · row ' + (c.row??0) + '</small>';
    d.onclick = ()=>{ CUR = k; renderEditor(); render(); };
    L.appendChild(d);
  });
  if(CUR && STATE.chats[CUR]) renderEditor();
}

function onKeyInput(){
  const v = document.getElementById('f_key').value.trim();
  const h = document.getElementById('f_key_hint');
  if(!v){ h.innerHTML = '推荐填<b>微信号</b>。也可填昵称或 wxid。'; return; }
  if(v.startsWith('wxid_')){
    h.innerHTML = '⚠️ 这是 wxid（内部 ID）。能工作，但普通用户看不到，'+
                  '建议改成<b>微信号</b>更直观。';
  } else if(v === 'filehelper' || v === 'weixin'){
    h.innerHTML = '这是<b>系统会话</b>（文件传输助手 / 微信团队），固定名称。';
  } else {
    h.innerHTML = '✓ 将按「' + v + '」解析成微信内部 ID。'+
                  '注意：填昵称时，对方改名后会失效。';
  }
}

function renderEditor(){
  const c = STATE.chats[CUR];
  document.getElementById('title').textContent = c.name || '(未命名)';
  document.getElementById('subtitle').textContent = CUR;
  document.getElementById('editor').style.display = 'block';
  document.getElementById('f_name').value = c.name || '';
  document.getElementById('f_key').value = CUR;
  document.getElementById('f_row').value = (c.row ?? 0);
  document.getElementById('f_enabled').value = (c.enabled===false) ? '0' : '1';
  document.getElementById('f_persona').value = c.persona || '';
  const sel = document.getElementById('f_prompt');
  sel.innerHTML = '<option value="">(不关联)</option>' +
    STATE.prompts.map(p=>'<option value="'+p+'">'+p+'</option>').join('');
  sel.value = c.prompt || '';
  document.getElementById('f_file').value = '';
}

function collect(){
  return {
    name: document.getElementById('f_name').value.trim(),
    row: parseInt(document.getElementById('f_row').value||'0',10) || 0,
    enabled: document.getElementById('f_enabled').value === '1',
    persona: document.getElementById('f_persona').value,
    prompt: document.getElementById('f_prompt').value,
  };
}

function saveChar(){
  if(!CUR){ toast('先选一个角色', true); return; }
  const v = collect();
  const newKey = document.getElementById('f_key').value.trim() || CUR;
  if(newKey !== CUR){
    delete STATE.chats[CUR];
    CUR = newKey;
  }
  STATE.chats[CUR] = v;
  push();
}

async function push(){
  // 只把需要的字段发给后端
  const out = {};
  Object.keys(STATE.chats).forEach(k=>{
    const c = STATE.chats[k];
    out[k] = {
      name: c.name||'', row: c.row||0,
      persona: c.persona||'',
      enabled: c.enabled !== false,
    };
  });
  const r = await fetch('/api/save', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({chats: out})
  }).then(x=>x.json());
  if(r.ok){ toast('已保存 '+r.count+' 个角色到 voice_bot.py'); load(); }
  else { toast('保存失败: '+(r.error||''), true); }
}

function delChar(){
  if(!CUR) return;
  const nm = STATE.chats[CUR].name || CUR;
  if(!confirm('确定删除角色「'+nm+'」？\n\n'+
              '删除后 voice_bot.py 不再监视该会话。\n'+
              '（旧版本已自动备份到 05_文档\\配置备份）')) return;
  delete STATE.chats[CUR];
  CUR = null;
  document.getElementById('editor').style.display='none';
  document.getElementById('title').textContent = '请选择左侧角色';
  document.getElementById('subtitle').textContent = '';
  push();
}

function addChar(){
  const k = prompt('新会话的标识（填微信号 / 昵称 / wxid 都行）：','');
  if(!k) return;
  const key = k.trim();
  const nm = prompt('显示名（必须与微信里显示的名字一致，发送前要用它核对标题）：','');
  STATE.chats[key] = { name:(nm||key).trim(), row:0, persona:'', enabled:true };
  CUR = key;
  push();
}

async function delAllDisabled(){
  const off = Object.keys(STATE.chats).filter(k=>STATE.chats[k].enabled===false);
  if(!off.length){ toast('没有被设为「不监视」的角色'); return; }
  if(!confirm('删除全部 '+off.length+' 个「不监视」的角色？\n\n'+off.join('\n'))) return;
  off.forEach(k=>delete STATE.chats[k]);
  if(off.includes(CUR)){ CUR = null; document.getElementById('editor').style.display='none'; }
  push();
}

async function importFromWeChat(){
  toast('正在读取微信数据库…');
  const r = await fetch('/api/wechat').then(x=>x.json());
  if(!r.ok){ toast('导入失败: '+(r.error||''), true); return; }
  const rows = r.rows || [];
  if(!rows.length){ toast('没有读到会话', true); return; }
  let added = 0;
  rows.forEach(x=>{
    if(!STATE.chats[x.username]){
      STATE.chats[x.username] = {
        name: x.nick, row: x.row, persona:'', enabled:false
      };
      added++;
    } else {
      // 已存在:只同步名称与行号
      STATE.chats[x.username].row = x.row;
      if(!STATE.chats[x.username].name) STATE.chats[x.username].name = x.nick;
    }
  });
  if(!added) toast('会话都已在列表中,已同步行号');
  CUR = rows[0].username;
  push();
}

async function loadPromptFile(){
  const n = document.getElementById('f_prompt').value;
  if(!n){ toast('先选一个 prompt 文件', true); return; }
  const r = await fetch('/api/prompt/'+encodeURIComponent(n)).then(x=>x.json());
  if(r.ok) { document.getElementById('f_file').value = r.content; toast('已载入 '+n); }
  else toast('载入失败', true);
}

async function loadPromptInto(){
  const n = document.getElementById('f_prompt').value;
  if(!n){ return; }
  const r = await fetch('/api/prompt/'+encodeURIComponent(n)).then(x=>x.json());
  if(r.ok){
    document.getElementById('f_file').value = r.content;
    if(confirm('把「'+n+'」的内容替换到 persona 里?')){
      document.getElementById('f_persona').value = r.content;
    }
  }
}

async function savePromptFile(){
  const n = document.getElementById('f_prompt').value;
  if(!n){ toast('先选或填写文件名', true); return; }
  const content = document.getElementById('f_file').value;
  const r = await fetch('/api/prompt', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name:n, content:content})
  }).then(x=>x.json());
  if(r.ok){ toast('已保存到 prompts/'+r.name); load(); }
  else toast('保存失败: '+(r.error||''), true);
}

load();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return PAGE


def main():
    # ⚠️ 关键:用 pythonw.exe 启动时没有 stdout/stderr,
    #    直接 print() 会抛 OSError 导致进程静默退出。
    #    所以这里先把标准输出重定向到日志文件。
    try:
        if sys.stdout is None or sys.stderr is None:
            logp = os.path.join(ROOT, "05_文档", "webui.log")
            os.makedirs(os.path.dirname(logp), exist_ok=True)
            f = open(logp, "a", encoding="utf-8", buffering=1)
            if sys.stdout is None:
                sys.stdout = f
            if sys.stderr is None:
                sys.stderr = f
        else:
            # 有控制台时也做一层保护,避免编码问题
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("=" * 68)
    print("  VoiceBot 角色配置 Web UI")
    print("=" * 68)
    print("  voice_bot.py :", VOICE_BOT, "存在" if os.path.isfile(VOICE_BOT) else "缺失")
    print("  prompts 目录 :", PROMPTS_DIR, "存在" if os.path.isdir(PROMPTS_DIR) else "缺失")
    print("  备份目录     :", BACKUP_DIR)
    print()
    print("  请在浏览器打开:  http://127.0.0.1:%d" % PORT)
    print("  按 Ctrl+C 退出")
    print("=" * 68)

    def open_browser():
        time.sleep(1.2)
        try:
            webbrowser.open("http://127.0.0.1:%d" % PORT)
        except Exception:
            pass
    threading.Thread(target=open_browser, daemon=True).start()

    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
