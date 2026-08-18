"""运行配置：所有项支持环境变量覆盖，便于本地与部署切换。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


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

    @property
    def db_file(self) -> Path:
        return Path(self.db_path).expanduser()

    @property
    def workspace_dir(self) -> Path:
        return Path(self.workspace_root).expanduser()


# 每次调用返回新实例，测试之间不会共享状态。
def get_settings() -> Settings:
    return Settings()