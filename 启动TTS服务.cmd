@echo off
chcp 65001 >nul
title VoiceBot - start TTS
set "ROOT=%~dp0"
cd /d "%ROOT%..\GPT-SoVITS-v2pro-20250604-nvidia50"

echo ==================================================================
echo   启动 GPT-SoVITS  api_v2.py   (127.0.0.1:9880)
echo ==================================================================
echo.
echo   首次加载模型约 20~60 秒。看到下面这行就是好了:
echo       Uvicorn running on http://127.0.0.1:9880
echo.
echo   *** 这个窗口不要关闭,关掉服务就停了 ***
echo.

if not exist "api_v2.py" (
  echo   [错误] 找不到 api_v2.py
  echo   当前位置: %CD%
  echo.
  pause
  exit /b 1
)
if not exist "runtime\python.exe" (
  echo   [错误] 找不到 runtime\python.exe
  pause
  exit /b 1
)

set "PYTHONPATH=%CD%"
set "PYTHONIOENCODING=utf-8"
".\runtime\python.exe" api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml

echo.
echo   TTS 服务已退出。
pause
