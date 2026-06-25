@echo off
chcp 65001 >nul
echo 启动 GPT-SoVITS WebUI...
echo 浏览器访问: http://localhost:9872
cd /d "%~dp0GPT-SoVITS"
call conda activate GPTSoVits
python webui.py --train
pause
