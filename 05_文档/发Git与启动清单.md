# 发 Git 与日常启动 —— 操作清单

> 给未来的自己 / 给克隆本项目的人。

---

## 一、发 Git 前的检查清单

### ✅ 已做好的防护

| 文件 | 作用 |
|---|---|
| `WeChatBot_WXAUTO_SE-3.28\.gitignore` | 忽略 `config.py`、`vendor_py/`、`libs/`、运行数据 |
| `VoiceBot\.gitignore` | 忽略 `vendor_py/`、音频、截图、状态文件、`*.ckpt/*.pth` |
| `WeChatBot_WXAUTO_SE-3.28\config.example.py` | 配置模板,**5 个真实 Key 已全部替换为 `YOUR_API_KEY_HERE`** |

### ⚠️ 推送前务必再确认(重要)

```powershell
# 1) 确认 config.py 真的没被跟踪(应当没有任何输出)
git ls-files | Select-String "config.py$"

# 2) 确认暂存区里没有真实 Key(应当没有任何输出)
#    通用模式:任何 sk- 开头、长度 20+ 的字符串都算可疑
git diff --cached | Select-String 'sk-[A-Za-z0-9]{20,}'

# 3) 确认仓库体积合理(应当 < 20 MB)
git count-objects -vH
```

**如果第 1 或第 2 条有输出 → 停下,先处理,别 push。**

万一已经 push 了:立刻去服务商后台**吊销那个 Key**,再考虑用
`git filter-repo` 清理历史(改历史会重写所有 commit)。

---

## 二、什么该进仓库、什么不该

| 内容 | 进仓库? | 说明 |
|---|---|---|
| `VoiceBot\01_核心模块\*.py` | ✅ | 你的核心代码 |
| `VoiceBot\02_工具面板\*.py` | ✅ | 控制面板 / 配置面板 |
| `VoiceBot\03_诊断测试\*.py` | ✅ | 各种自检脚本 |
| `VoiceBot\04_音频留存\audio_retention.py` | ✅ | 清理器 |
| `VoiceBot\05_文档\README.md` | ✅ | 文档 |
| `WeChatBot_WXAUTO_SE-3.28\*.py` + `config.example.py` | ✅ | |
| **`config.py`** | ❌ | **含真实 Key** |
| **`vendor_py/`**(312 MB) | ❌ | 用 pip 重装,见下 |
| `libs/*.whl` | ❌ | 同上 |
| 音频 `*.wav`、截图 `*.png` | ❌ | 诊断产物 |
| 模型 `*.ckpt` / `*.pth` | ❌ | 太大,单独管理 |

### 别人克隆后如何恢复依赖

```powershell
pip install wechatauto-replica pywechat127 sounddevice soundfile `
            pypinyin winsdk zstandard opencv-python imageio-ffmpeg `
            --target "<项目>\vendor_py"
```

> 注:本机是把依赖装进 `vendor_py`(因为系统 site-packages 不可写)。
> 如果你机器上可写系统目录,直接普通 `pip install` 也行。

---

## 三、日常启动步骤(开机后)

### 顺序很重要

**1. 启动 TTS 服务**(必需,否则发不出语音)
```
控制面板 → ⑥ 启动 TTS 服务
```
或手动:
```powershell
cd <GPT-SoVITS 目录>
$env:PYTHONPATH=(Get-Location).Path
.\runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```
等待 `Uvicorn running on http://127.0.0.1:9880`(约 20~60 秒)

**2. 确认音频设备**(改过默认设备后**必须重新确认**)
```
默认录音 = VoiceMeeter Output (VB-Audio VoiceMeeter VAIO)
默认播放 = 扬声器
```

**3. 微信登录,并把窗口【最大化】**

**4. 运行**
```
控制面板 → ① 环境自检 → ③ 演练模式(先看回复内容)→ ② 实际发送
```

---

## 四、容易忘的几个坑

| 坑 | 后果 | 预防 |
|---|---|---|
| 微信窗口**没最大化** | 坐标全失准,可能点错 | 自检会提示 `⚠ 窗口未最大化` |
| 改过默认音频设备 | sounddevice **索引整体重排** | 已改为**按名称自动查找**,但设备名不能变 |
| `tts_infer.yaml` 的 `version` 被改回 `v1` | 重启可能加载错误 | 用配置面板改,它会写对 |
| `GPT_weights_v4` 是空目录 | 以为有 v4 模型 | 实际只有 **v2Pro** 权重 |
| 会话行号 `row` 漂移 | 发错人 | 把对象**置顶**固定行号;发送前会 OCR 核对标题 |
| 那个 0.6 秒神秘音频 | 混进语音条 | 已用 VoiceMeeter 绕开;**别换回 Line 1** |

---

## 五、关键路径速查

```
桌面/
├── VoiceBot/                        ← 本项目
│   ├── 01_核心模块/                 voice_bot.py 等
│   ├── 02_工具面板/                 VoiceBot控制面板.pyw ← 双击入口
│   ├── 03_诊断测试/
│   ├── 04_音频留存/                 audio_retention.py
│   └── 05_文档/                     README.md / 本文件 / 配置备份
├── WeChatBot_WXAUTO_SE-3.28/        ← 主体项目
│   ├── config.py                    ← 含真实 Key(不进仓库!)
│   └── config.example.py            ← 模板
└── GPT-SoVITS-v2pro-.../            ← 语音合成
    └── GPT_SoVITS/configs/tts_infer.yaml   ← 音色配置
```
