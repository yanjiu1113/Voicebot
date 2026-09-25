@echo off
chcp 65001 >nul
title VoiceBot - start TTS
set "ROOT=%~dp0"
set "CORE=%ROOT%01_核心模块"

rem ---- 定位 Python(只用来读取路径配置,不写死路径) ----
set "PY="
for %%I in (python.exe) do if not defined PY set "PY=%%~$PATH:I"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PY if exist "C:\Python310\python.exe" set "PY=C:\Python310\python.exe"

rem ---- 解析 GPT-SoVITS 目录 ----
rem 与控制面板用的是同一份来源(部署路径.json),所以在面板里改过路径,
rem 这里也会跟着变。用临时文件传值,避免 Python 路径含空格时 for /f 出错。
set "TTSDIR="
if not defined PY goto :norun
"%PY%" -X utf8 "%CORE%\部署路径.py" --print tts > "%TEMP%\voicebot_tts_dir.txt" 2>nul
set /p TTSDIR=<"%TEMP%\voicebot_tts_dir.txt"
del "%TEMP%\voicebot_tts_dir.txt" >nul 2>nul
:norun
if not defined TTSDIR set "TTSDIR=%ROOT%..\GPT-SoVITS-v2pro-20250604-nvidia50"

echo ==================================================================
echo   启动 GPT-SoVITS  api_v2.py    127.0.0.1:9880
echo ==================================================================
echo.
echo   目录: %TTSDIR%
echo.
echo   首次加载模型约 20~60 秒。看到下面这行就是好了:
echo       Uvicorn running on http://127.0.0.1:9880
echo.
echo   *** 这个窗口不要关闭,关掉服务就停了 ***
echo.

cd /d "%TTSDIR%" 2>nul
if not exist "api_v2.py" goto :missing
if not exist "runtime\python.exe" goto :missing

set "PYTHONPATH=%CD%"
set "PYTHONIOENCODING=utf-8"
".\runtime\python.exe" api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml

echo.
echo   TTS 服务已退出。
pause
exit /b 0

:missing
echo   [错误] 这个目录里找不到 api_v2.py 或 runtime\python.exe
echo.
echo   当前设置: %TTSDIR%
echo   当前目录: %CD%
echo.
echo   两种可能:
echo     1. 还没部署 GPT-SoVITS     VoiceBot 不含它,需要自行下载 v2Pro 整合包
echo     2. TTS 路径不正确          目录指到了别的地方
echo.
echo   解决办法: 打开 VoiceBot 控制面板,在下方 部署路径 区点 选择... 指定正确目录。
echo   也可以直接编辑: %ROOT%部署路径.json
echo.
pause
exit /b 1
