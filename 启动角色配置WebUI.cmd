@echo off
chcp 65001 >nul
title VoiceBot - character Web UI
set "ROOT=%~dp0"
rem ---- 定位 Python(不写死路径,换机器也能用)----
set "PYW="
for %%I in (pythonw.exe) do if not defined PYW set "PYW=%%~$PATH:I"
if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe"
if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Python39\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python39\pythonw.exe"
if not defined PYW set "PYW=pythonw"

echo ==================================================================
echo   启动「角色配置 Web UI」
echo ==================================================================
echo.
echo   浏览器会自动打开:  http://127.0.0.1:5100
echo.
echo   在里面可以:
echo     - 为每个会话写角色设定^(persona^)
echo     - 一键从微信导入会话^(需要微信已登录^)
echo     - 设置会话行号、启用/停用
echo.
echo   若端口被占用^(说明已在运行^),直接访问上面的网址即可。
echo.
start "" "%PYW%" "%ROOT%02_工具面板\character_webui.py"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:5100"
exit /b 0
