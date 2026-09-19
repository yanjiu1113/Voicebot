# VoiceBot —— 微信语音自动回复

让 AI 角色用 **GPT-SoVITS 克隆音色**在微信发出**真正的语音条**。

> 全程使用微信官方语音条通道 —— **无 Hook、无协议逆向、无进程内存改写**。

---

## 一、快速开始

**双击 `02_工具面板\VoiceBot控制面板.pyw`**

| 按钮 | 作用 |
|---|---|
| ① 环境自检 | 检查微信窗口 / TTS 服务 / 虚拟线卡 / AI 接口 |
| ② 启动语音回复 | 读消息 → AI → 合成 → **实际发送** |
| ③ 演练模式 | 同上但**不发送**,确认回复内容 |
| ④ 配置面板 | 改 AI 模型、TTS 音色、留存限额 |
| ⑤ 音频留存管理 | 查看占用、清理超限文件 |
| ⑥ 启动 TTS 服务 | 启动 GPT-SoVITS 的 api_v2.py |

---

## 二、工作原理

```
微信收到消息
   └─> 解密本地数据库读取(replica,不依赖 UIA)
        └─> AI 生成回复(OpenRouter)
             └─> GPT-SoVITS 合成 WAV
                  └─> 播到虚拟线卡(VoiceMeeter Input)
                       └─> 微信默认麦克风 = 该线卡输出端 → "听到"音频
                            └─> 鼠标模拟:点语音条按钮 → 播放 → 点发送
                                 └─> 真·微信语音条发出
```

**为什么必须这样**:微信 4.1.x 聊天区**自绘渲染**(`MMUIRenderSubWindowHW`),
对 UIAutomation 完全不暴露控件 —— 实测 11 个顶层窗口仅 2 个叶子面板,
所以 wxauto / pyweixin 等 UIA 方案**架构性不可用**。

---

## 三、必须满足的前提(缺一不可)

| 项 | 要求 |
|---|---|
| 微信 | 桌面版**已登录**,窗口**最大化** |
| 默认**录音**设备 | `VoiceMeeter Output (VB-Audio VoiceMeeter VAIO)` |
| 默认**播放**设备 | **扬声器**(必须!否则听不到声音且系统声会灌进去) |
| TTS 服务 | `api_v2.py` 运行在 `127.0.0.1:9880` |
| 桌面 | 未锁屏;发送时会**抢占前台焦点** |

> ⚠️ **窗口必须最大化** —— 所有坐标按最大化标定。窗口尺寸一变,比例就失准。

---

## 四、目录结构

```
VoiceBot/
├── 01_核心模块/          程序本体
│   ├── voice_bot.py          主程序:读消息 → AI → 合成 → 发送
│   ├── wx_voice_sender.py    语音条发送(锚点坐标 + 录制校验)
│   ├── session_guard.py      会话检测:OCR 读标题 + 高亮检测(防 toggle)
│   ├── gptsovits_tts.py      GPT-SoVITS 合成客户端(路径自动探测)
│   ├── wx_sender.py          底层:窗口激活/点击/粘贴
│   ├── wxauto_bootstrap.py   依赖引导(vendor_py + comtypes 重定向)
│   ├── voice_reply.py        早期版本(发音频文件),保留备用
│   └── voice_test.py         合成测试
├── 02_工具面板/
│   ├── VoiceBot控制面板.pyw  ← 双击这个
│   └── config_tool.py        配置面板(模型/音色/留存)
├── 03_诊断测试/          各种自检脚本
├── 04_音频留存/
│   ├── audio_retention.py    自动清理
│   ├── retention_config.json 限额配置
│   └── 项目诊断音频/
├── 05_文档/
│   ├── README.md             本文件
│   ├── 配置备份/             每次改配置前的自动备份
│   └── 发送截图/             每次发送的会话标题截图(可回查发给谁)
└── voice_bot_state.json  消息水位(避免重复回复)
```

---

## 五、配置说明

> **⚠️ 所有 API Key 当前为空**,需先填写才能使用。
> 推荐用「④ 配置面板」改 —— 它会自动备份并同步修改所有相关行。

### 🔑 首次使用:填入 API Key(必做)

**位置:`WeChatBot_WXAUTO_SE-3.28\config.py`**

| 配置项 | 行号 | 现状 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 第 **22** 行 | **空** ← 主对话模型,必填 |
| `MOONSHOT_API_KEY` | 第 **42** 行 | 空(图片/表情识别,可选) |
| `ONLINE_API_KEY` | 第 **121** 行 | 空(联网搜索,可选) |
| `DEEPSEEK_BASE_URL` | 第 **24** 行 | 已填 `https://openrouter.ai/api/v1` |
| `MODEL` | 第 **4** 行 **和** 第 **26** 行 | 已填 `deepseek/deepseek-v4-flash` |

**填法(两种任选):**

1. **配置面板**(推荐):双击 `02_工具面板\VoiceBot控制面板.pyw` → ④ 配置面板
   → 「AI 模型」标签页 → 选服务商 → 填 Key → 点「测试连通」验证 → 保存
2. **手动改**:编辑 `config.py`,把第 22 行的
   `DEEPSEEK_API_KEY = ''` 改成 `DEEPSEEK_API_KEY = '你的Key'`

> 也可直接复制 `config.example.py` 覆盖为 `config.py`(模板内有说明注释),
> 再填入自己的值。`config.py` 已被 `.gitignore` 忽略,**不会进仓库**。

> ⚠️ **`MODEL` 出现两次**(第 4 行是旧配置残留),手动改时两处都要改。
> 配置面板会自动同步改所有出现的位置。

常见服务商地址:

| 服务商 | BASE_URL |
|---|---|
| OpenRouter | `https://openrouter.ai/api/v1` |
| DeepSeek 官方 | `https://api.deepseek.com` |
| 硅基流动 | `https://api.siliconflow.cn/v1` |
| 月之暗面 | `https://api.moonshot.cn/v1` |
| 阿里百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` |

实测可用模型:`deepseek/deepseek-v4-flash`(推荐)、`deepseek/deepseek-v3.2`、
`deepseek/deepseek-v4-pro`、`deepseek/deepseek-chat-v3.1`

> **v4-flash 是推理模型**:`MAX_TOKEN` 需 ≥300,否则返回空内容(当前 2000,足够)。
> 即使配错模型,`voice_bot.py` 也会**自动回退**到可用模型。

### TTS 音色(GPT + SoVITS 两个模型)

**位置:`GPT-SoVITS-v2pro-20250604-nvidia50\GPT_SoVITS\configs\tts_infer.yaml`**

```yaml
custom:
  t2s_weights_path:  .../GPT_weights_v2Pro/Elysia_v2-e15.ckpt      ← GPT 权重
  vits_weights_path: .../SoVITS_weights_v2Pro/Elysia_v2_e24_s13080.pth  ← SoVITS 权重
  version: v2Pro                                                    ← 训练版本
```

配置面板会写入此文件,并在 TTS 服务**在线时热切换(免重启)**。

可用的权重(实测):

| 角色 | GPT 权重 | SoVITS 权重 |
|---|---|---|
| 爱莉希雅 | `Elysia_v2-e15.ckpt` | `Elysia_v2_e24_s13080.pth` |
| zekai | `zekai-e15.ckpt` | `zekai_e4_s104.pth` |
| yayi | `yayi_custom_voice-e15.ckpt` | `yayi_custom_voice_e8_s200.pth` |

**⚠️ `version` 必须与权重训练版本一致** —— 当前全是 **v2Pro**。
（实测:服务启动时会把 `version` 改写成 `v1`,需注意核对）

### 音频留存(配置面板 → 音频留存)

三重限额,任一超出即从最旧的删:

| 项 | 默认 |
|---|---|
| `max_files` | 200 个 |
| `max_total_mb` | 300 MB |
| `max_age_days` | 7 天 |
| `min_age_hours` | 24(保护期,此时间内绝不删) |

### 自动回复对象(可选)

**位置:`VoiceBot\01_核心模块\voice_bot.py`** 的 `CHATS`

```python
CHATS = {
    "filehelper": {"name": "文件传输助手", "row": 1, "persona": "..."},
    "wxid_你的测试号": {"name": "测试号B", "row": 0, "persona": "..."},
}
```
`row` 是会话列表行号,**会随聊天活跃度变化**,建议把对象置顶以固定行号。
用 `python voice_bot.py --list` 可查看当前行号。

---

## 六、已知问题与踩坑记录

| 现象 | 根因 | 处理 |
|---|---|---|
| **UIA 方案全废** | 微信 4.1.x 聊天区自绘渲染 | 改走「数据库读取 + 坐标模拟」 |
| 微信 4.1.12 / 4.1.2 **无法登录** | 服务端拒绝"版本过低" | 当前用 **4.1.9.57** |
| **Line 1 线卡有神秘音频** | 来源不明:0.6 秒低沉女声,周期性播放,峰值恒 15958,会混进语音条 | **改用 VoiceMeeter**(实测三种驱动全零事件) |
| 录音 60 秒超长 | 坐标错位 918px(微信输入区**非等比缩放**) | 改用「距右/下边缘偏移」锚点 |
| 会话被误关 | 微信会话已打开时再点会 **toggle 关闭** | `session_guard` OCR 读标题,已打开则跳过点击 |
| 点击不生效 | Windows **前台锁定** | `AttachThreadInput` 附加到前台线程 |
| 设备号突然失效 | 改默认设备后 **sounddevice 索引整体重排** | 改为**按名称自动查找** |
| WASAPI 录音端报错 | 探测能过、实开却报 `Invalid sample rate` | 录音改用 **DirectSound** |
| `tasklist` 返回空 | 环境限制 | 用 `psutil` 替代 PID 检测 |

---

## 七、坐标标定(如需重新标定)

基准:微信最大化 `2584x1540`(屏幕 2560x1600,DPI 缩放 175%)

| 控件 | 距右/下边缘偏移 | 绝对坐标 |
|---|---|---|
| 语音条按钮(旋转 wifi 图标) | (223, 73) | (2349, 1455) |
| 录音中 ⬆ 发送 | (86, 76) | (2486, 1452) |
| 录音中 ✕ 取消 | (440, 76) | (2132, 1452) |

> **语音转文字的麦克风图标 ≠ 语音条按钮!**
> 语音条是**旋转 90 度的 wifi 图标**,在发送键左边。

---

## 八、安全与隔离

- 测试仅使用**文件传输助手**或**用户自己的号**(测试号B、测试号A)
- 真实联系人(Woo、薄)**绝不触碰**
- `voice_bot.py` 里的 `CHATS` 显式列出要自动回复的对象,未列出的不会回复
- **默认演练模式**,`--live` 才真正发送

---

## 九、免责声明

本工具仅用于个人学习与自动化研究。使用自动化操作微信可能违反其用户协议,
存在账号被限制的风险(实测:模拟器登录曾触发风控,桌面端较稳)。
请自行承担使用风险,建议仅在**自己有权控制的账号**上使用。
