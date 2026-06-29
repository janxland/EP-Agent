import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录（backend/）
_BACKEND_DIR = Path(__file__).resolve().parent.parent

# 本地开发时自动读取 backend/.env
load_dotenv(_BACKEND_DIR / ".env")


class Config:
    def __init__(self):
        self.ADDR: str = os.getenv("APP_ADDR", "0.0.0.0:8080")
        self.LLM_API_KEY: str = os.getenv("LLM_API_KEY") or os.getenv("SILICONFLOW_API_KEY", "")
        self.LLM_BASE_URL: str = (
            os.getenv("LLM_BASE_URL")
            or os.getenv("SILICONFLOW_BASE_URL")
            or "https://api.siliconflow.cn/v1"
        )
        # strong 模型：复杂推理 / ReAct 多轮 / 创作编辑（默认 DeepSeek-V3.2）
        self.LLM_MODEL: str = (
            os.getenv("LLM_MODEL")
            or os.getenv("SILICONFLOW_MODEL")
            or "deepseek-ai/DeepSeek-V3.2"
        )
        # lite 模型：意图路由 / TODO 规划等轻量调用，成本更低（默认 DeepSeek-V4-Flash）
        # 未配置时自动回退到 LLM_MODEL（strong），保持向后兼容
        self.LLM_MODEL_LITE: str = (
            os.getenv("LLM_MODEL_LITE")
            or "deepseek-ai/DeepSeek-V4-Flash"
        )
        self.WORKSPACE_DIR: str = os.getenv("ABC_WORKSPACE_DIR", str(_BACKEND_DIR / "workspace"))

        # H5 海报输出目录（与 h5_tools.py / main.py 共享同一配置源）
        self.H5_OUTPUT_DIR: str = os.getenv("H5_OUTPUT_DIR", "/tmp/ep_agent_h5")

        # HOST / PORT 从 ADDR 解析（正确的实例属性方式）
        parts = self.ADDR.rsplit(":", 1)
        self.HOST: str = parts[0] if len(parts) == 2 else "0.0.0.0"
        self.PORT: int = int(parts[1]) if len(parts) == 2 else 8080

        # ── 音频生成 API ──────────────────────────────────────────
        # Suno AI（通过 TTAPI 第三方封装）
        self.SUNO_API_KEY: str  = os.getenv("SUNO_API_KEY", "")
        self.SUNO_BASE_URL: str = os.getenv("SUNO_BASE_URL", "https://api.ttapi.io")

        # MiniMax 官方 API
        self.MINIMAX_API_KEY: str  = os.getenv("MINIMAX_API_KEY", "")
        self.MINIMAX_BASE_URL: str = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1")

        # GPT-SoVITS（自部署服务，可选）
        # 部署参考：https://github.com/RVC-Boss/GPT-SoVITS
        self.SOVITS_BASE_URL: str = os.getenv("SOVITS_BASE_URL", "")
        self.SOVITS_API_KEY: str  = os.getenv("SOVITS_API_KEY", "")

    def validate(self):
        if not self.LLM_API_KEY:
            raise ValueError("LLM_API_KEY is required")
        # 音频 API Key 为可选项，未配置时工具返回友好错误


config = Config()
