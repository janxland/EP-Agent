@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo   GPT-SoVITS 安装器启动中...
echo.

where node >nul 2>&1
if %errorlevel% neq 0 (
    echo   [✗] 未找到 Node.js，请先安装: https://nodejs.org
    echo       或直接双击打开 index.html（模拟模式）
    start index.html
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('node --version') do set NODE_VER=%%v
echo   [✓] Node.js %NODE_VER%
echo   [→] 启动后端服务: http://localhost:3333
echo.
node server.js
pause
