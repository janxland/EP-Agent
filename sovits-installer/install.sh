#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  GPT-SoVITS 一键安装脚本 — macOS / Linux
#  EP-Agent · Voice Clone Module
#  用法: bash install.sh [--device MPS|CPU] [--source HF|HF-Mirror|ModelScope] [--dir PATH] [--uvr5]
# ═══════════════════════════════════════════════════════════════

set -e

# ── 默认参数 ─────────────────────────────────────────────────
DEVICE="CPU"
SOURCE="HF-Mirror"
INSTALL_DIR="./GPT-SoVITS"
DOWNLOAD_UVR5=false
ENV_NAME="GPTSoVits"

# ── 颜色 ─────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; PURPLE='\033[0;35m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'

log()     { echo -e "${CYAN}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[✓]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[⚠]${NC}    $1"; }
error()   { echo -e "${RED}[✗]${NC}    $1"; }
step()    { echo -e "\n${PURPLE}${BOLD}▶ $1${NC}"; }
cmd_echo(){ echo -e "${BLUE}  \$${NC} $1"; }

# ── 解析参数 ─────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --device)  DEVICE="$2";      shift 2 ;;
    --source)  SOURCE="$2";      shift 2 ;;
    --dir)     INSTALL_DIR="$2"; shift 2 ;;
    --uvr5)    DOWNLOAD_UVR5=true; shift ;;
    --help|-h)
      echo "用法: bash install.sh [选项]"
      echo "  --device  MPS|CPU|CU126|CU128  (默认: CPU)"
      echo "  --source  HF|HF-Mirror|ModelScope  (默认: HF-Mirror)"
      echo "  --dir     安装目录  (默认: ./GPT-SoVITS)"
      echo "  --uvr5    同时下载 UVR5 人声分离模型"
      exit 0 ;;
    *) warn "未知参数: $1"; shift ;;
  esac
done

# ── 检测操作系统 ──────────────────────────────────────────────
OS="linux"
if [[ "$OSTYPE" == "darwin"* ]]; then
  OS="mac"
  # Apple Silicon 自动切换 MPS
  if [[ $(uname -m) == "arm64" ]] && [[ "$DEVICE" == "CPU" ]]; then
    DEVICE="MPS"
    warn "检测到 Apple Silicon，自动切换为 MPS 设备"
  fi
fi

# ── 打印 Banner ───────────────────────────────────────────────
echo ""
echo -e "${PURPLE}${BOLD}╔═══════════════════════════════════════════╗${NC}"
echo -e "${PURPLE}${BOLD}║     GPT-SoVITS 音色克隆 · 一键安装       ║${NC}"
echo -e "${PURPLE}${BOLD}║     EP-Agent Voice Clone Module           ║${NC}"
echo -e "${PURPLE}${BOLD}╚═══════════════════════════════════════════╝${NC}"
echo ""
log "操作系统 : $OS"
log "设备类型 : $DEVICE"
log "模型来源 : $SOURCE"
log "安装目录 : $INSTALL_DIR"
log "UVR5模型 : $DOWNLOAD_UVR5"
echo ""

# ═══════════════════════════════════════════════════════════════
# 步骤 1：检查前置依赖
# ═══════════════════════════════════════════════════════════════
step "步骤 1/7 · 检查前置依赖"

check_cmd() {
  if command -v "$1" &>/dev/null; then
    success "$1 已安装 ($(command -v $1))"
    return 0
  else
    warn "$1 未找到"
    return 1
  fi
}

check_cmd git   || { error "请先安装 Git: https://git-scm.com"; exit 1; }
check_cmd conda || { error "请先安装 Conda/Miniconda: https://docs.conda.io/en/latest/miniconda.html"; exit 1; }

# ═══════════════════════════════════════════════════════════════
# 步骤 2：安装系统依赖
# ═══════════════════════════════════════════════════════════════
step "步骤 2/7 · 安装系统依赖"

if [[ "$OS" == "mac" ]]; then
  # macOS: Xcode CLI Tools + Homebrew + FFmpeg
  if ! xcode-select -p &>/dev/null; then
    log "安装 Xcode Command Line Tools..."
    cmd_echo "xcode-select --install"
    xcode-select --install 2>/dev/null || true
    log "请在弹出窗口中点击「安装」，完成后重新运行此脚本"
    read -p "Xcode CLI Tools 安装完成后按 Enter 继续..."
  else
    success "Xcode CLI Tools 已安装"
  fi

  if ! command -v brew &>/dev/null; then
    log "安装 Homebrew..."
    cmd_echo '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  else
    success "Homebrew 已安装"
  fi

  if ! command -v ffmpeg &>/dev/null; then
    log "安装 FFmpeg..."
    cmd_echo "brew install ffmpeg"
    brew install ffmpeg
  else
    success "FFmpeg 已安装"
  fi

else
  # Linux: apt
  if command -v apt-get &>/dev/null; then
    log "安装 FFmpeg 和系统依赖..."
    cmd_echo "sudo apt-get update && sudo apt-get install -y ffmpeg libsox-dev build-essential cmake"
    sudo apt-get update -qq
    sudo apt-get install -y ffmpeg libsox-dev build-essential cmake
    success "系统依赖安装完成"
  elif command -v yum &>/dev/null; then
    cmd_echo "sudo yum install -y ffmpeg"
    sudo yum install -y ffmpeg || warn "FFmpeg 安装失败，请手动安装"
  else
    warn "无法自动安装 FFmpeg，请手动安装后继续"
  fi
fi

# ═══════════════════════════════════════════════════════════════
# 步骤 3：创建 Conda 虚拟环境
# ═══════════════════════════════════════════════════════════════
step "步骤 3/7 · 创建 Conda 虚拟环境"

if conda env list | grep -q "^${ENV_NAME} "; then
  warn "环境 ${ENV_NAME} 已存在，跳过创建"
  read -p "  是否删除重建？(y/N): " REBUILD
  if [[ "$REBUILD" == "y" || "$REBUILD" == "Y" ]]; then
    cmd_echo "conda env remove -n ${ENV_NAME} -y"
    conda env remove -n "${ENV_NAME}" -y
    cmd_echo "conda create -n ${ENV_NAME} python=3.10 -y"
    conda create -n "${ENV_NAME}" python=3.10 -y
    success "环境 ${ENV_NAME} 已重建"
  fi
else
  cmd_echo "conda create -n ${ENV_NAME} python=3.10 -y"
  conda create -n "${ENV_NAME}" python=3.10 -y
  success "环境 ${ENV_NAME} 创建成功"
fi

# ═══════════════════════════════════════════════════════════════
# 步骤 4：克隆仓库
# ═══════════════════════════════════════════════════════════════
step "步骤 4/7 · 克隆 GPT-SoVITS 仓库"

if [[ -d "$INSTALL_DIR/.git" ]]; then
  warn "目录 ${INSTALL_DIR} 已存在，执行 git pull 更新..."
  cmd_echo "git -C ${INSTALL_DIR} pull"
  git -C "${INSTALL_DIR}" pull
  success "仓库已更新"
else
  log "克隆仓库到 ${INSTALL_DIR}..."
  cmd_echo "git clone --depth=1 https://github.com/RVC-Boss/GPT-SoVITS ${INSTALL_DIR}"
  git clone --depth=1 https://github.com/RVC-Boss/GPT-SoVITS "${INSTALL_DIR}"
  success "仓库克隆完成"
fi

cd "${INSTALL_DIR}"

# ═══════════════════════════════════════════════════════════════
# 步骤 5：安装 Python 依赖
# ═══════════════════════════════════════════════════════════════
step "步骤 5/7 · 安装 Python 依赖（耗时较长，请耐心等待）"

UVR5_FLAG=""
[[ "$DOWNLOAD_UVR5" == "true" ]] && UVR5_FLAG="--download-uvr5"

DEVICE_LOWER=$(echo "$DEVICE" | tr '[:upper:]' '[:lower:]')

if [[ -f "install.sh" ]]; then
  log "使用官方安装脚本..."
  cmd_echo "conda run -n ${ENV_NAME} bash install.sh --device ${DEVICE_LOWER} --source ${SOURCE} ${UVR5_FLAG}"
  conda run -n "${ENV_NAME}" bash install.sh \
    --device "${DEVICE_LOWER}" \
    --source "${SOURCE}" \
    ${UVR5_FLAG}
  success "依赖安装完成"
else
  warn "未找到 install.sh，使用手动安装..."
  cmd_echo "conda run -n ${ENV_NAME} pip install -r requirements.txt"
  conda run -n "${ENV_NAME}" pip install -r requirements.txt
  if [[ -f "extra-req.txt" ]]; then
    cmd_echo "conda run -n ${ENV_NAME} pip install -r extra-req.txt --no-deps"
    conda run -n "${ENV_NAME}" pip install -r extra-req.txt --no-deps
  fi
  success "依赖安装完成（手动模式）"
fi

# ═══════════════════════════════════════════════════════════════
# 步骤 6：验证安装
# ═══════════════════════════════════════════════════════════════
step "步骤 6/7 · 验证安装"

log "检查 PyTorch..."
TORCH_VER=$(conda run -n "${ENV_NAME}" python -c "import torch; print(torch.__version__)" 2>/dev/null || echo "FAILED")
if [[ "$TORCH_VER" == "FAILED" ]]; then
  error "PyTorch 未正确安装，请检查日志"
  exit 1
else
  success "PyTorch ${TORCH_VER} ✓"
fi

log "检查预训练模型..."
MODEL_DIR="GPT_SoVITS/pretrained_models"
if [[ -d "$MODEL_DIR" ]] && [[ -n "$(ls -A $MODEL_DIR 2>/dev/null)" ]]; then
  MODEL_COUNT=$(ls "$MODEL_DIR" | wc -l | tr -d ' ')
  success "预训练模型目录存在（${MODEL_COUNT} 个文件）✓"
else
  warn "预训练模型目录为空或不存在，首次启动时将自动下载"
fi

# ═══════════════════════════════════════════════════════════════
# 步骤 7：创建启动脚本
# ═══════════════════════════════════════════════════════════════
step "步骤 7/7 · 创建快捷启动脚本"

LAUNCH_SCRIPT="start-sovits.sh"
cat > "../${LAUNCH_SCRIPT}" << LAUNCH_EOF
#!/bin/bash
# GPT-SoVITS 快捷启动脚本
cd "${INSTALL_DIR}"
echo "启动 GPT-SoVITS WebUI..."
echo "浏览器访问: http://localhost:9872"
conda run -n ${ENV_NAME} python webui.py
LAUNCH_EOF
chmod +x "../${LAUNCH_SCRIPT}"
success "启动脚本已创建: ${LAUNCH_SCRIPT}"

# ═══════════════════════════════════════════════════════════════
# 完成
# ═══════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}${BOLD}╔═══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║         🎉 安装完成！                     ║${NC}"
echo -e "${GREEN}${BOLD}╚═══════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BOLD}启动方式：${NC}"
echo -e "  ${CYAN}方式 1（推荐）：${NC}"
echo -e "    bash start-sovits.sh"
echo ""
echo -e "  ${CYAN}方式 2（手动）：${NC}"
echo -e "    conda activate ${ENV_NAME}"
echo -e "    cd ${INSTALL_DIR}"
echo -e "    python webui.py"
echo ""
echo -e "  ${CYAN}浏览器访问：${NC}"
echo -e "    http://localhost:9872"
echo ""
