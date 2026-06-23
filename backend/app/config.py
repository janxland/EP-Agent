import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（backend/）
_BACKEND_DIR = Path(__file__).resolve().parent.parent

# 启动时自动加载 backend/.env，方便本地开发
load_dotenv(_BACKEND_DIR / ".env")


class Config:
    def __init__(self):
        self.ADDR: str = os.getenv("APP_ADDR", "0.0.0.0:8080")
        self.LLM_API_KEY: str = os.getenv("LLM_API_KEY") or os.getenv("SILICONFLOW_API_KEY", "")
        self.LLM_BASE_URL: str = (
            os.getenv("LLM_BASE_URL")
            or os.getenv("SILICONFLOW_BASE_URL")
            or "https://api.openai.com/v1"
        )
        self.LLM_MODEL: str = os.getenv("LLM_MODEL") or os.getenv("SILICONFLOW_MODEL", "gpt-4o-mini")
        # 默认使用内嵌的 sky-music-tools，支持环境变量覆盖
        self.SKILL_DIR: str = os.getenv(
            "SKILL_DIR",
            os.getenv("ABC_SKILLS_DIR", str(_BACKEND_DIR / "sky-music-tools"))
        )
        # 如果传入的是 skills 根目录，则自动指向 sky-music-tools
        skill_dir_path = Path(self.SKILL_DIR)
        if skill_dir_path.name != "sky-music-tools" and (skill_dir_path / "sky-music-tools").is_dir():
            self.SKILL_DIR = str(skill_dir_path / "sky-music-tools")
        self.WORKSPACE_DIR: str = os.getenv("ABC_WORKSPACE_DIR", str(_BACKEND_DIR / "workspace"))

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

    def validate(self):
        if not self.LLM_API_KEY:
            raise ValueError("LLM_API_KEY is required")
        if not self.SKILL_DIR:
            raise ValueError("SKILL_DIR is required")
        # 音频 API Key 为可选项，未配置时工具返回友好错误


config = Config()
