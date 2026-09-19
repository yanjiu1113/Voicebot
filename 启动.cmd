@echo off
chcp 65001 >nul
setlocal
title VoiceBot Launcher

set "PYW=C:\Users\sduser\AppData\Local\Programs\Python\Python310\pythonw.exe"
set "PY=C:\Users\sduser\AppData\Local\Programs\Python\Python310\python.exe"
if not exist "%PY%" set "PY=python"
if not exist "%PYW%" set "PYW=%PY%"

set "ROOT=%~dp0"
set "TOOLS=%ROOT%02_工具面板"
set "CORE=%ROOT%01_核心模块"

:MENU
cls
echo ==================================================================
echo                        VoiceBot  启动菜单
echo ==================================================================
echo.
echo   [1] 控制面板              ^(总入口:自检 / 发送 / 配置^)
echo   [2] 角色配置 Web UI        ^(浏览器里管理角色 prompt^)
echo   [3] 配置面板              ^(AI 模型 / TTS 音色 / 留存^)
echo.
echo   [4] 启动 TTS 服务         ^(api_v2.py, 9880 端口^)
echo   [5] 环境自检
echo.
echo   [6] 语音回复 - 演练模式    ^(不发送^)
echo   [7] 语音回复 - 实际发送    ^(会真的发语音条^)
echo.
echo   [8] 音频留存管理
echo   [9] 打开项目目录
echo   [0] 退出
echo.
echo ==================================================================
set /p "C= 请选择 [0-9]: "

if "%C%"=="1" goto CTRL
if "%C%"=="2" goto WEBUI
if "%C%"=="3" goto CONFIG
if "%C%"=="4" goto TTS
if "%C%"=="5" goto CHECK
if "%C%"=="6" goto DRY
if "%C%"=="7" goto LIVE
if "%C%"=="8" goto RET
if "%C%"=="9" goto OPENDIR
if "%C%"=="0" exit /b 0
goto MENU

:CTRL
start "" "%PYW%" "%TOOLS%\VoiceBot控制面板.pyw"
exit /b 0

:WEBUI
echo.
echo   启动角色配置 Web UI ...
echo   浏览器将自动打开 http://127.0.0.1:5100
echo   关闭本窗口不会停止服务;要停止请用任务管理器结束 python。
start "" "%PYW%" "%TOOLS%\character_webui.py"
exit /b 0

:CONFIG
start "" "%PYW%" "%TOOLS%\config_tool.py"
exit /b 0

:TTS
echo.
echo   启动 GPT-SoVITS api_v2.py
echo   首次加载模型约 20~60 秒,出现下面这行即就绪:
echo       Uvicorn running on http://127.0.0.1:9880
echo.
echo   *** 这个窗口不要关闭,关掉服务就停了 ***
echo.
cd /d "%ROOT%..\GPT-SoVITS-v2pro-20250604-nvidia50"
if not exist "api_v2.py" (
  echo   [错误] 找不到 api_v2.py
  echo   预期位置: %CD%
  pause
  goto MENU
)
set "PYTHONPATH=%CD%"
set "PYTHONIOENCODING=utf-8"
".\runtime\python.exe" api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
echo.
echo   TTS 服务已退出。
pause
goto MENU

:CHECK
echo.
"%PY%" -X utf8 "%CORE%\voice_bot.py" --check
echo.
pause
goto MENU

:DRY
echo.
echo   演练模式:完整跑一遍但不发送。Ctrl+C 退出。
echo.
"%PY%" -X utf8 "%CORE%\voice_bot.py"
echo.
pause
goto MENU

:LIVE
echo.
echo   *** 实际发送模式:会真的发出语音条! ***
echo.
echo   请确认:
echo     - 微信已登录且窗口【最大化】
echo     - 默认录音设备 = VoiceMeeter Output
echo     - 默认播放设备 = 扬声器
echo     - CHATS 里没有真实联系人
echo.
set /p "OK=  继续? (y/N): "
if /i not "%OK%"=="y" goto MENU
echo.
"%PY%" -X utf8 "%CORE%\voice_bot.py" --live
echo.
pause
goto MENU

:RET
echo.
"%PY%" -X utf8 "%ROOT%04_音频留存\audio_retention.py" --report
echo.
set /p "CL=  立即清理超限文件? (y/N): "
if /i "%CL%"=="y" "%PY%" -X utf8 "%ROOT%04_音频留存\audio_retention.py"
echo.
pause
goto MENU

:OPENDIR
start "" explorer "%ROOT%"
exit /b 0
