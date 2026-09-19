# 发 Git 与日常启动 —— 操作清单

> 给未来的自己 / 给克隆本项目的人。

---

## 一、发 Git 前的检查清单

### ✅ 已做好的防护

| 文件 | 作用 |
|---|---|
| `WeChatBot_WXAUTO_SE-3.28\.gitignore` | 忽略 `config.py`、`vendor_py/`、`libs/`、运行数据 |
| `VoiceBot\.gitignore` | 忽略 `vendor_py/`、音频、截图、运行日志、状态文件、`*.ckpt/*.pth` |
| `VoiceBot\.gitignore` | 忽略 **`01_核心模块/voice_bot.py`**(含真实微信号/昵称) |
| `WeChatBot_WXAUTO_SE-3.28\config.example.py` | 配置模板,**5 个真实 Key 已全部替换为 `YOUR_API_KEY_HERE`** |
| `VoiceBot\01_核心模块\voice_bot.example.py` | 角色配置模板(`CHATS` 全为占位) |

> 模板由 `02_工具面板\生成发布模板.py` 生成 —— 它会**自动扫描敏感串**,
> 发现真实微信号/昵称/Key 就直接中止,不会写出模板。

### ⚠️ 推送前务必再确认(重要)

```powershell
# 1) 确认 config.py 没被跟踪(应当没有任何输出)
git ls-files | Select-String "config.py$"

# 2) 确认 voice_bot.py 没被跟踪(应当没有任何输出)
git ls-files | Select-String "voice_bot\.py$|voice_bot_config"

# 3) 扫已提交内容里有没有真实标识 / Key(应当没有任何输出)
git grep -n -E "wxid_[a-z0-9]{10,}|sk-[A-Za-z0-9]{20,}"
git grep -n -E "DEEPSEEK_API_KEY *=" 

# 4) 直接扫**远程分支**的整棵树(最可靠,能发现历史里遗留的文件)
git grep -n -I -E "sk-[A-Za-z0-9]{20,}" origin/main

# 5) 确认仓库体积合理(应当 < 20 MB)
git count-objects -vH
```

**如果第 1~4 条有输出 → 停下,先处理,别 push。**

万一已经 push 了:立刻去服务商后台**吊销那个 Key**,
再考虑用 `git filter-repo` 清理历史(改历史会重写所有 commit)。

> ⚠️ 还要注意:`git grep head` 默认只扫当前分支。**文件被 `.gitignore` 忽略
> 不等于它不在历史里** —— 要像第 4 条那样显式扫 `origin/main`。

---

## 二、什么该进仓库、什么不该

| 内容 | 进仓库? | 说明 |
|---|---|---|
| `VoiceBot\01_核心模块\*.py` | ✅ | 你的核心代码 |
| `VoiceBot\02_工具面板\*.py` | ✅ | 控制面板 / 配置面板 / 角色 WebUI |
| `VoiceBot\03_诊断测试\*.py` | ✅ | 各种自检脚本 |
| `VoiceBot\04_音频留存\audio_retention.py` | ✅ | 清理器 |
| `VoiceBot\05_文档\README.md`、`CHANGELOG.md` | ✅ | 文档 |
| `VoiceBot\VERSION` | ✅ | 版本号 |
| `VoiceBot\启动*.cmd` | ✅ | 启动器 |
| `VoiceBot\01_核心模块\voice_bot.example.py` | ✅ | 角色配置**模板** |
| `WeChatBot_WXAUTO_SE-3.28\*.py` + `config.example.py` | ✅ | |
| **`config.py`** | ❌ | **含真实 Key** |
| **`01_核心模块\voice_bot.py`** | ❌ | **含真实微信号 / 昵称 / 角色 prompt** |
| `reference_config.json`、`参考音频/` | ❌ | 个人素材 |
| **`vendor_py/`**(312 MB) | ❌ | 用 pip 重装,见下 |
| `libs/*.whl` | ❌ | 同上 |
| 音频 `*.wav`、截图 `*.png` | ❌ | 诊断产物 |
| `05_文档/运行日志/*.log` | ❌ | 运行产物 |
| 模型 `*.ckpt` / `*.pth` | ❌ | 太大,单独管理 |

> **`voice_bot.py` 被忽略后,别人克隆下来会没有它** —— 所以加了
> `02_工具面板\初始化配置.py`,首次运行时会自动从 `voice_bot.example.py`
> 生成一份。`启动.cmd`、控制面板、角色 WebUI 都会先调它,不用手动操作。

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
启动.cmd → [4] 启动 TTS 服务
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

**3. 微信登录,并把窗口【最大化】,并且保持在最前面**

> ⚠️ **别让任何窗口盖住微信**(尤其是右下角工具栏区域)。
> 点击是"屏幕绝对坐标",被盖住时会点到别的程序上。

**4. 运行**
```
启动.cmd → [5] 环境自检 → [6] 演练模式(先看回复内容)→ [7] 实际发送
```
或双击 `02_工具面板\VoiceBot控制面板.pyw` 用图形界面。

**5. 出问题看日志**
```
05_文档\运行日志\voicebot_年月日.log     ← 每步都有 [1/5]…[5/5],失败标明卡在哪步
05_文档\发送截图\xxx_fail_*.png          ← 失败瞬间的截图
```

---

## 四、容易忘的几个坑

| 坑 | 后果 | 预防 |
|---|---|---|
| 微信窗口**没最大化** | 坐标全失准,可能点错 | 自检会提示 `⚠ 窗口未最大化` |
| **微信被别的窗口盖住** | 点击落到别的程序上:微信里"录了音"但发送键没被点到,鼠标停着不动 | 每次点击前做归属校验,不属于微信就抢回前台;仍不行则拒绝点击并报错 |
| **重试时补点语音条按钮** | 把正在进行的录音**直接取消**(绿色工具栏有 1~3 秒延迟,容易误判"没录上") | 已改为"先固定等待→播放→播放后校验",且只有确认不在录音态时才补点 |
| 改过默认音频设备 | sounddevice **索引整体重排** | 已改为**按名称自动查找**,但设备名不能变 |
| `tts_infer.yaml` 的 `version` 被改回 `v1` | 重启可能加载错误 | 用配置面板改,它会写对 |
| `GPT_weights_v4` 是空目录 | 以为有 v4 模型 | 实际只有 **v2Pro** 权重 |
| 会话行号 `row` 漂移 | 发错人 | 把对象**置顶**固定行号;发送前会 OCR 核对标题 |
| 那个 0.6 秒神秘音频 | 混进语音条 | 已用 VoiceMeeter 绕开;**别换回 Line 1** |
| 诊断录音开着 | 抢占微信的录音端点 → 发出**空语音条** | 默认关闭;`--debug` 也**不会**打开它,要显式传 `diag_record=True` |

---

## 五、关键路径速查

```
桌面/
├── VoiceBot/                        ← 本项目
│   ├── VERSION / CHANGELOG.md       ← 版本与更新日志
│   ├── 启动.cmd                     ← 统一启动菜单(推荐入口)
│   ├── 重要说明.txt                 ← 给使用者的说明 + FAQ
│   ├── 01_核心模块/                 voice_bot.py 等
│   │   └── voice_bot.example.py     ← 角色配置模板(克隆后首次运行自动复制)
│   ├── 02_工具面板/                 VoiceBot控制面板.pyw ← 图形入口
│   │   ├── character_webui.py       角色配置 Web UI(127.0.0.1:5100)
│   │   ├── config_tool.py           AI / TTS / 留存配置
│   │   ├── 初始化配置.py            首次运行引导
│   │   └── 生成发布模板.py          发布前生成 voice_bot.example.py
│   ├── 03_诊断测试/
│   ├── 04_音频留存/                 audio_retention.py
│   └── 05_文档/                     README.md / 本文件 / 运行日志 / 发送截图
├── WeChatBot_WXAUTO_SE-3.28/        ← 主体项目
│   ├── config.py                    ← 含真实 Key(不进仓库!)
│   └── config.example.py            ← 模板
└── GPT-SoVITS-v2pro-.../            ← 语音合成
    └── GPT_SoVITS/configs/tts_infer.yaml   ← 音色配置
```
