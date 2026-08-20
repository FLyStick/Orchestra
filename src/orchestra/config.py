"""运行配置：从 .env 或进程环境变量读取，支持本地与部署切换。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录下的 .env；已存在的进程环境变量优先级更高。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


# 配置类为不可变对象，避免运行期被意外修改。
@dataclass(frozen=True)
class Settings:
    db_path: str = field(default_factory=lambda: os.getenv("ORCHESTRA_DB_PATH", "data/orchestra.db"))
    workspace_root: str = field(default_factory=lambda: os.getenv("ORCHESTRA_WORKSPACE_ROOT", "data/workspaces"))
    llm_provider: str = field(default_factory=lambda: os.getenv("ORCHESTRA_LLM_PROVIDER", "mock"))
    openai_base_url: str = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("ORCHESTRA_LLM_MODEL", "gpt-4o-mini"))
    fallback_model: str | None = field(default_factory=lambda: os.getenv("ORCHESTRA_FALLBACK_MODEL") or None)
    host: str = field(default_factory=lambda: os.getenv("ORCHESTRA_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.getenv("ORCHESTRA_PORT", "8000")))
    # 包 1：路由评测集路径与低置信复核区间。
    routing_golden_path: str = field(default_factory=lambda: os.getenv("ORCHESTRA_ROUTING_GOLDEN_PATH", "docs/golden/routing-cases.json"))
    routing_ambiguous_band: str = field(default_factory=lambda: os.getenv("ORCHESTRA_ROUTING_AMBIGUOUS_BAND", "0.25,0.35"))
    hr_scenario_threshold: float = field(default_factory=lambda: float(os.getenv("ORCHESTRA_HR_SCENARIO_THRESHOLD", "0.30")))

    @property
    def db_file(self) -> Path:
        return Path(self.db_path).expanduser()

    @property
    def workspace_dir(self) -> Path:
        return Path(self.workspace_root).expanduser()

    @property
    def routing_golden_file(self) -> Path:
        """路由评测集路径，供路由评测器读取。"""
        return Path(self.routing_golden_path).expanduser()

    @property
    def ambiguous_band(self) -> tuple[float, float]:
        """低置信复核区间，例如 0.25,0.35 -> (0.25, 0.35)。"""
        parts = self.routing_ambiguous_band.split(",")
        low = float(parts[0])
        high = float(parts[1]) if len(parts) > 1 else 0.35
        return low, high


# 每次调用返回新实例，测试之间不会共享状态。
def get_settings() -> Settings:
    return Settings()