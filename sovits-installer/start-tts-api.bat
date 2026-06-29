@echo off
chcp 65001 >nul 2>&1
title GPT-SoVITS TTS API
cd /d "%~dp0GPT-SoVITS"
echo.
echo  ╔═══════════════════════════════════════════════════╗
echo  ║      GPT-SoVITS TTS API 服务启动器               ║
echo  ║      EP-Agent Voice Clone Module                  ║
echo  ╚═══════════════════════════════════════════════════╝
echo.
echo  绑定地址: http://0.0.0.0:9880
echo  EP-Agent 对接端口: 9880
echo  API 文档: http://localhost:9880/docs
echo.
echo  启动中（首次加载模型约需 1-2 分钟）...
echo.
call conda activate GPTSoVits
python api_v2.py -a 0.0.0.0 -p 9880
pause
