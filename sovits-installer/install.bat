@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

:: ═══════════════════════════════════════════════════════════════
::  GPT-SoVITS 一键安装脚本 — Windows 10/11
::  EP-Agent · Voice Clone Module
::  用法: install.bat [--device CPU|CU126|CU128] [--source HF|HF-Mirror|ModelScope] [--dir PATH] [--uvr5]
:: ═══════════════════════════════════════════════════════════════

:: ── 默认参数 ─────────────────────────────────────────────────
set DEVICE=CPU
set SOURCE=HF-Mirror
set INSTALL_DIR=.\GPT-SoVITS
set DOWNLOAD_UVR5=
set ENV_NAME=GPTSoVits

:: ── 解析参数 ─────────────────────────────────────────────────
:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--device" ( set DEVICE=%~2 & shift & shift & goto :parse_args )
if /i "%~1"=="--source" ( set SOURCE=%~2 & shift & shift & goto :parse_args )
if /i "%~1"=="--dir"    ( set INSTALL_DIR=%~2 & shift & shift & goto :parse_args )
if /i "%~1"=="--uvr5"   ( set DOWNLOAD_UVR5=--DownloadUVR5 & shift & goto :parse_args )
shift & goto :parse_args
:args_done

:: ── Banner ───────────────────────────────────────────────────
echo.
echo  ╔═══════════════════════════════════════════╗
echo  ║     GPT-SoVITS 音色克隆 · 一键安装       ║
echo  ║     EP-Agent Voice Clone Module           ║
echo  ╚═══════════════════════════════════════════╝
echo.
echo  [INFO]  设备类型 : %DEVICE%
echo  [INFO]  模型来源 : %SOURCE%
echo  [INFO]  安装目录 : %INSTALL_DIR%
echo.

:: ═══════════════════════════════════════════════════════════════
:: 步骤 1：检查前置依赖
:: ═══════════════════════════════════════════════════════════════
echo.
echo  ▶ 步骤 1/6 · 检查前置依赖
echo  ─────────────────────────────────────────

where git >nul 2>&1
if %errorlevel% neq 0 (
    echo  [✗]  Git 未安装，请先安装: https://git-scm.com
    pause & exit /b 1
)
echo  [✓]  Git 已安装

where conda >nul 2>&1
if %errorlevel% neq 0 (
    echo  [✗]  Conda 未安装，请先安装 Miniconda:
    echo       https://docs.conda.io/en/latest/miniconda.html
    pause & exit /b 1
)
echo  [✓]  Conda 已安装

:: ── 自动检测 CUDA 版本 ────────────────────────────────────
echo.
echo  ▶ 自动检测 CUDA 版本
echo  ─────────────────────────────────────────

:: 仅当用户未通过 --device 手动指定 GPU 时才自动检测
if /i "%DEVICE%"=="CPU" (
    where nvidia-smi >nul 2>&1
    if %errorlevel% neq 0 (
        echo  [⚠]  未检测到 nvidia-smi，将使用 CPU 模式
    ) else (
        :: 从 nvidia-smi 输出中提取 CUDA Version
        for /f "tokens=*" %%L in ('nvidia-smi 2^>nul ^| findstr /i "CUDA Version"') do (
            set CUDA_LINE=%%L
        )
        :: 解析主版本号（如 "CUDA Version: 12.8" → "12.8"，取整数部分 "12"）
        for /f "tokens=3 delims=: " %%V in ("!CUDA_LINE!") do set CUDA_VER_FULL=%%V
        for /f "tokens=1 delims=." %%M in ("!CUDA_VER_FULL!") do set CUDA_MAJOR=%%M
        for /f "tokens=2 delims=." %%N in ("!CUDA_VER_FULL!") do set CUDA_MINOR=%%N

        :: 优先匹配 12.8，其次 12.6，否则保持 CPU
        if "!CUDA_MAJOR!"=="12" (
            if !CUDA_MINOR! GEQ 8 (
                set DEVICE=CU128
                echo  [✓]  检测到 CUDA !CUDA_VER_FULL!，自动选择设备: CU128
            ) else if !CUDA_MINOR! GEQ 6 (
                set DEVICE=CU126
                echo  [✓]  检测到 CUDA !CUDA_VER_FULL!，自动选择设备: CU126
            ) else (
                echo  [⚠]  检测到 CUDA !CUDA_VER_FULL!（低于 12.6），将使用 CPU 模式
            )
        ) else if "!CUDA_MAJOR!" GTR "12" (
            set DEVICE=CU128
            echo  [✓]  检测到 CUDA !CUDA_VER_FULL!（高于 12.8），自动选择设备: CU128
        ) else (
            echo  [⚠]  检测到 CUDA !CUDA_VER_FULL!（低于 12.6），将使用 CPU 模式
        )
    )
) else (
    echo  [INFO]  已手动指定设备: %DEVICE%，跳过自动检测
)
echo  [INFO]  最终设备类型: %DEVICE%

:: ═══════════════════════════════════════════════════════════════
:: 步骤 2：安装 FFmpeg（如未安装）
:: ═══════════════════════════════════════════════════════════════
echo.
echo  ▶ 步骤 2/6 · 检查 FFmpeg
echo  ─────────────────────────────────────────

where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
    echo  [⚠]  FFmpeg 未找到，尝试通过 winget 安装...
    winget install --id=Gyan.FFmpeg -e --silent >nul 2>&1
    if %errorlevel% neq 0 (
        echo  [⚠]  自动安装失败，请手动下载 ffmpeg.exe 放到 %INSTALL_DIR%\
        echo       下载地址: https://www.gyan.dev/ffmpeg/builds/
    ) else (
        echo  [✓]  FFmpeg 安装成功
    )
) else (
    echo  [✓]  FFmpeg 已安装
)

:: ═══════════════════════════════════════════════════════════════
:: 步骤 3：创建 Conda 虚拟环境
:: ═══════════════════════════════════════════════════════════════
echo.
echo  ▶ 步骤 3/6 · 创建 Conda 虚拟环境
echo  ─────────────────────────────────────────

conda env list | findstr /C:"%ENV_NAME%" >nul 2>&1
if %errorlevel% equ 0 (
    echo  [⚠]  环境 %ENV_NAME% 已存在，跳过创建
) else (
    echo  [INFO]  创建 Python 3.10 环境: %ENV_NAME%
    echo  $  conda create -n %ENV_NAME% python=3.10 -y
    conda create -n %ENV_NAME% python=3.10 -y
    if %errorlevel% neq 0 (
        echo  [✗]  Conda 环境创建失败
        pause & exit /b 1
    )
    echo  [✓]  环境 %ENV_NAME% 创建成功
)

:: ═══════════════════════════════════════════════════════════════
:: 步骤 4：克隆仓库
:: ═══════════════════════════════════════════════════════════════
echo.
echo  ▶ 步骤 4/6 · 克隆 GPT-SoVITS 仓库
echo  ─────────────────────────────────────────

if exist "%INSTALL_DIR%\.git" (
    echo  [⚠]  目录已存在，执行 git pull 更新...
    git -C "%INSTALL_DIR%" pull
    echo  [✓]  仓库已更新
) else (
    echo  $  git clone --depth=1 https://github.com/RVC-Boss/GPT-SoVITS %INSTALL_DIR%
    git clone --depth=1 https://github.com/RVC-Boss/GPT-SoVITS "%INSTALL_DIR%"
    if %errorlevel% neq 0 (
        echo  [✗]  仓库克隆失败，请检查网络连接
        pause & exit /b 1
    )
    echo  [✓]  仓库克隆完成
)

cd /d "%INSTALL_DIR%"

:: ═══════════════════════════════════════════════════════════════
:: 步骤 5：安装 Python 依赖
:: ═══════════════════════════════════════════════════════════════
echo.
echo  ▶ 步骤 5/6 · 安装 Python 依赖（耗时较长，请耐心等待）
echo  ─────────────────────────────────────────

if exist "install.ps1" (
    echo  [INFO]  使用官方 PowerShell 安装脚本...

    :: 检测 pwsh (PowerShell 7+) 是否可用，否则回退到系统内置 powershell
    set PS_EXE=powershell
    where pwsh >nul 2>&1
    if %errorlevel% equ 0 (
        set PS_EXE=pwsh
        echo  [INFO]  检测到 PowerShell 7+，使用 pwsh
    ) else (
        echo  [INFO]  未检测到 pwsh，使用内置 powershell 5.x
    )

    echo  $  conda run -n %ENV_NAME% %PS_EXE% -ExecutionPolicy Bypass -File install.ps1 --Device %DEVICE% --Source %SOURCE% %DOWNLOAD_UVR5%
    conda run -n %ENV_NAME% %PS_EXE% -ExecutionPolicy Bypass -File install.ps1 --Device %DEVICE% --Source %SOURCE% %DOWNLOAD_UVR5%
    if %errorlevel% neq 0 (
        echo  [✗]  依赖安装失败，尝试手动安装...
        conda run -n %ENV_NAME% pip install -r requirements.txt
    )
) else (
    echo  [⚠]  未找到 install.ps1，使用 pip 手动安装...
    conda run -n %ENV_NAME% pip install -r requirements.txt
)
echo  [✓]  依赖安装完成

:: ═══════════════════════════════════════════════════════════════
:: 步骤 6：创建快捷启动脚本
:: ═══════════════════════════════════════════════════════════════
echo.
echo  ▶ 步骤 6/6 · 创建快捷启动脚本
echo  ─────────────────────────────────────────

cd /d "%~dp0"

(
echo @echo off
echo chcp 65001 ^>nul
echo echo 启动 GPT-SoVITS WebUI...
echo echo 浏览器访问: http://localhost:9872
echo cd /d "%INSTALL_DIR%"
echo call conda activate %ENV_NAME%
echo python webui.py
echo pause
) > "start-sovits.bat"

echo  [✓]  启动脚本已创建: start-sovits.bat

:: ═══════════════════════════════════════════════════════════════
:: 完成
:: ═══════════════════════════════════════════════════════════════
echo.
echo  ╔═══════════════════════════════════════════╗
echo  ║         🎉  安装完成！                    ║
echo  ╚═══════════════════════════════════════════╝
echo.
echo  启动方式：
echo    方式 1（推荐）: 双击 start-sovits.bat
echo    方式 2（手动）: conda activate %ENV_NAME%
echo                    cd %INSTALL_DIR%
echo                    python webui.py
echo.
echo  浏览器访问: http://localhost:9872
echo.
pause
