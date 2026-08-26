"""运行配置：从 .env 或进程环境变量读取，支持本地与部署切换。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录下的 .env；已存在的进程环境变量优先级更高。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    """解析 true/false/1/0 等常见布尔写法。"""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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
    # 包 2：RAG 总开关与 Embedding / ChromaDB / Rerank 配置。
    rag_enabled: bool = field(default_factory=lambda: _env_bool("ORCHESTRA_RAG_ENABLED", False))
    embedding_provider: str = field(default_factory=lambda: os.getenv("ORCHESTRA_EMBEDDING_PROVIDER", "openai"))
    embedding_model: str = field(default_factory=lambda: os.getenv("ORCHESTRA_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"))
    embedding_dim: str = field(default_factory=lambda: os.getenv("ORCHESTRA_EMBEDDING_DIM", "0"))
    embedding_api_key: str = field(default_factory=lambda: os.getenv("ORCHESTRA_EMBEDDING_API_KEY", ""))
    embedding_base_url: str = field(default_factory=lambda: os.getenv("ORCHESTRA_EMBEDDING_BASE_URL", "https://api.openai.com/v1"))
    chroma_path: str = field(default_factory=lambda: os.getenv("ORCHESTRA_CHROMA_PATH", "data/chroma"))
    chroma_host: str = field(default_factory=lambda: os.getenv("ORCHESTRA_CHROMA_HOST", ""))
    chroma_port: int = field(default_factory=lambda: int(os.getenv("ORCHESTRA_CHROMA_PORT", "8001")))
    collection_prefix: str = field(default_factory=lambda: os.getenv("ORCHESTRA_COLLECTION_PREFIX", "orchestra"))
    knowledge_source_dir: str = field(default_factory=lambda: os.getenv("ORCHESTRA_KNOWLEDGE_SOURCE_DIR", "data/knowledge"))
    retrieval_top_k: int = field(default_factory=lambda: int(os.getenv("ORCHESTRA_RETRIEVAL_TOP_K", "5")))
    retrieval_mode: str = field(default_factory=lambda: os.getenv("ORCHESTRA_RETRIEVAL_MODE", "hybrid"))
    retrieval_min_score: float = field(default_factory=lambda: float(os.getenv("ORCHESTRA_RETRIEVAL_MIN_SCORE", "0.0")))
    rerank_enabled: bool = field(default_factory=lambda: _env_bool("ORCHESTRA_RERANK_ENABLED", False))
    rerank_model: str = field(default_factory=lambda: os.getenv("ORCHESTRA_RERANK_MODEL", "gte-rerank-v2"))
    rerank_api_key: str = field(default_factory=lambda: os.getenv("ORCHESTRA_RERANK_API_KEY", ""))
    rerank_base_url: str = field(default_factory=lambda: os.getenv("ORCHESTRA_RERANK_BASE_URL", ""))
    rerank_top_n: int = field(default_factory=lambda: int(os.getenv("ORCHESTRA_RERANK_TOP_N", "5")))
    rag_chunk_size: int = field(default_factory=lambda: int(os.getenv("ORCHESTRA_RAG_CHUNK_SIZE", "512")))
    rag_chunk_overlap: int = field(default_factory=lambda: int(os.getenv("ORCHESTRA_RAG_CHUNK_OVERLAP", "64")))
    rag_manifest_path: str = field(default_factory=lambda: os.getenv("ORCHESTRA_RAG_MANIFEST_PATH", "data/rag_manifest.json"))
    # 包 3：工作流驱动与 Redis Streams / 延迟重试配置。
    workflow_driver: str = field(default_factory=lambda: os.getenv("ORCHESTRA_WORKFLOW_DRIVER", "sqlite"))
    redis_url: str = field(default_factory=lambda: os.getenv("ORCHESTRA_REDIS_URL", "redis://127.0.0.1:6379/0"))
    redis_stream_prefix: str = field(default_factory=lambda: os.getenv("ORCHESTRA_REDIS_STREAM_PREFIX", "orchestra"))
    redis_consumer_group: str = field(default_factory=lambda: os.getenv("ORCHESTRA_REDIS_CONSUMER_GROUP", "orchestra-workers"))
    worker_concurrency: int = field(default_factory=lambda: int(os.getenv("ORCHESTRA_WORKER_CONCURRENCY", "4")))
    retry_max_attempts: int = field(default_factory=lambda: int(os.getenv("ORCHESTRA_RETRY_MAX_ATTEMPTS", "3")))
    retry_base_delay_ms: int = field(default_factory=lambda: int(os.getenv("ORCHESTRA_RETRY_BASE_DELAY_MS", "1000")))
    retry_max_delay_ms: int = field(default_factory=lambda: int(os.getenv("ORCHESTRA_RETRY_MAX_DELAY_MS", "60000")))
    retry_jitter_ms: int = field(default_factory=lambda: int(os.getenv("ORCHESTRA_RETRY_JITTER_MS", "200")))

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

    @property
    def chroma_dir(self) -> Path:
        """ChromaDB 本地持久化目录。"""
        return Path(self.chroma_path).expanduser()

    @property
    def knowledge_dir(self) -> Path:
        """部门知识文档根目录。"""
        return Path(self.knowledge_source_dir).expanduser()

    @property
    def rag_manifest_file(self) -> Path:
        """文档索引清单文件路径。"""
        return Path(self.rag_manifest_path).expanduser()

    @property
    def embedding_vector_dim(self) -> int | None:
        """Embedding 向量维度；未配置或为 0 时由服务自动识别。"""
        raw = (self.embedding_dim or "").strip()
        if not raw or raw == "0":
            return None
        return int(raw)


# 每次调用返回新实例，测试之间不会共享状态。
def get_settings() -> Settings:
    return Settings()
