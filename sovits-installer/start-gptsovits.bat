@echo off
chcp 65001 >nul
title GPT-SoVITS
cd /d "%~dp0GPT-SoVITS"
call conda activate GPTSoVits
python webui.py --train
pause
