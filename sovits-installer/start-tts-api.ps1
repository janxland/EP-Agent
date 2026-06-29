# GPT-SoVITS TTS API 启动脚本
# EP-Agent Voice Clone Module

$ErrorActionPreference = 'Stop'

$sovitsDir = Join-Path $PSScriptRoot "GPT-SoVITS"
$env:CONDA_DEFAULT_ENV = "GPTSoVits"

Write-Host ""
Write-Host "╔═══════════════════════════════════════════════════╗"
Write-Host "║      GPT-SoVITS TTS API 服务启动器               ║"
Write-Host "║      EP-Agent Voice Clone Module                  ║"
Write-Host "╚═══════════════════════════════════════════════════╝"
Write-Host ""
Write-Host "  绑定地址 : http://0.0.0.0:9880"
Write-Host "  EP-Agent : SOVITS_BASE_URL=http://localhost:9880"
Write-Host "  API 文档 : http://localhost:9880/docs"
Write-Host ""
Write-Host "  首次加载模型约需 1-2 分钟，请耐心等待..."
Write-Host ""

Set-Location $sovitsDir

conda run -n GPTSoVits --cwd $sovitsDir python api_v2.py -a 0.0.0.0 -p 9880
