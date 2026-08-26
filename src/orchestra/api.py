"""Orchestra HTTP API：任务提交、查询、取消、SSE 订阅与 Workspace 查看。

对外暴露 REST 接口（任务生命周期管理）与 SSE 接口（事件流订阅），
前端通过 POST 提交任务、轮询/SSE 获取进度与最终答案。
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .contracts.task import TaskInput, TokenBudget
from .rag.service import create_rag_stack
from .executor import Executor
from .workflow.driver import SqliteWorkflowDriver, WorkflowDriver
from .workflow.event_bus import SqliteEventBus
from .workflow.retry import RetryPolicy
from .llm import LLMService, create_llm_provider
from .router import RuleRouter
from .scenarios import ALL_SCENARIOS
from .store import SQLiteStore
from .tools import create_tool_registry
from .workspace.local_workspace import LocalWorkspace

logger = logging.getLogger(__name__)


class BudgetModel(BaseModel):
    """Token 预算请求模型：控制单任务与单 Agent 的 token 上限。"""

    total_tokens: int = 100_000
    per_agent_tokens: int = 20_000
    allow_model_fallback: bool = True


class CreateTaskRequest(BaseModel):
    """创建任务的请求体：前端提交问题与执行参数。"""

    query: str = Field(min_length=1)  # 用户问题，必填且非空。
    session_id: str = Field(min_length=1)  # 会话标识，用于隔离工作区。
    user_id: str = "anonymous"  # 用户标识，默认匿名。
    context: dict[str, Any] = Field(default_factory=dict)  # 附加上下文（如部门、权限）。
    strategy: str | None = None  # 显式指定策略；不传则由路由器自动决策。
    budget: BudgetModel | None = None  # Token 预算；不传则使用默认值。
    max_iterations: int = Field(default=10, ge=1, le=100)  # 最大迭代次数（预留）。
    workspace_enabled: bool = True  # 是否启用文件工作区。
    metadata: dict[str, Any] = Field(default_factory=dict)  # 附加元数据。



class KnowledgeSearchRequest(BaseModel):
    """包 2 知识检索请求：query 必填，department/mode 可选。"""

    query: str = Field(min_length=1)
    department: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    mode: str | None = None


# 将 HTTP 请求模型转换为核心契约 TaskInput。
def _to_task_input(request: CreateTaskRequest) -> TaskInput:
    """把 API 请求模型转换为内部契约 TaskInput。

    Args:
        request: FastAPI 校验后的创建任务请求。

    Returns:
        核心契约 TaskInput，供执行器使用。
    """
    budget = None
    # 请求带预算时转换为 TokenBudget 契约对象。
    if request.budget:
        budget = TokenBudget(
            total_tokens=request.budget.total_tokens,
            per_agent_tokens=request.budget.per_agent_tokens,
            allow_model_fallback=request.budget.allow_model_fallback,
        )
    return TaskInput(
        query=request.query,
        session_id=request.session_id,
        user_id=request.user_id,
        context=request.context,
        strategy=request.strategy,
        budget=budget,
        max_iterations=request.max_iterations,
        workspace_enabled=request.workspace_enabled,
        metadata=request.metadata,
    )


def _safe_session_id(session_id: str) -> None:
    """校验 session_id 合法性，防止路径穿越攻击。

    Args:
        session_id: 待校验的会话标识。

    Raises:
        HTTPException: session_id 为空或包含路径分隔符/.. 时返回 400。
    """
    if (
        not session_id
        or "/" in session_id
        or "\\" in session_id
        or ".." in session_id
        or session_id in {".", " "}
    ):
        raise HTTPException(status_code=400, detail="invalid session_id")


# 组装存储、LLM、路由与执行器；settings 可注入以便测试。
def create_app(settings: Settings | None = None) -> FastAPI:
    """创建 FastAPI 应用，组装各核心组件。

    Args:
        settings: 运行配置；不传则从环境变量/.env 读取（便于测试注入）。

    Returns:
        配置完成的 FastAPI 应用实例。
    """
    settings = settings or get_settings()
    # 存储：SQLite 持久化任务、事件与 token 用量。
    store = SQLiteStore(settings.db_file)
    # LLM：按配置创建 mock 或 openai 兼容 Provider，并包装为服务。
    provider = create_llm_provider(settings)
    llm_service = LLMService(provider, settings.llm_model, settings.fallback_model)
    # 路由：规则路由（复杂度评分 + 策略选择）。
    router = RuleRouter()
    # 包 2：按配置创建真实 RAG 服务；未启用时保留 Mock 关键词兜底。
    retrieval_service, ingestion_service = create_rag_stack(settings)
    tool_registry = create_tool_registry(retrieval_service=retrieval_service)
    # 执行器：后台执行任务，串联路由、策略与存储。
    executor = Executor(
        store=store,
        llm_service=llm_service,
        router=router,
        workspace_root=settings.workspace_dir,
        tool_registry=tool_registry,
    )
    retry_policy = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_delay_ms=settings.retry_base_delay_ms,
        max_delay_ms=settings.retry_max_delay_ms,
        jitter_ms=settings.retry_jitter_ms,
    )
    sqlite_driver = SqliteWorkflowDriver(
        store,
        executor,
        event_bus=SqliteEventBus(store),
        retry_policy=retry_policy,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        selected_driver: WorkflowDriver = sqlite_driver
        redis_driver = None
        worker = None
        if settings.workflow_driver == "redis":
            import redis.asyncio as aioredis

            from .workflow.redis_driver import RedisStreamWorkflowDriver
            from .workflow.worker import RedisWorkflowWorker

            try:
                redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
                redis_driver = RedisStreamWorkflowDriver(
                    store,
                    executor,
                    redis_client,
                    settings,
                    retry_policy=retry_policy,
                )
                await redis_driver.start()
                selected_driver = redis_driver
                worker = RedisWorkflowWorker(
                    driver=redis_driver,
                    redis=redis_client,
                    commands_stream=redis_driver.commands_stream,
                    consumer_group=redis_driver.consumer_group,
                    consumer_name=redis_driver.consumer_name,
                    retry_scheduler=redis_driver.retry_scheduler,
                    concurrency=settings.worker_concurrency,
                )
                await worker.start()
            except Exception as exc:
                # Redis 未部署/连接失败时回退 SQLite 驱动，本地开发不阻塞。
                logger.warning("Redis 工作流不可用，回退 SQLite 驱动：%s", exc)
                if worker:
                    try:
                        await worker.stop()
                    except Exception:
                        pass
                    worker = None
                if redis_driver:
                    try:
                        await redis_driver.close()
                    except Exception:
                        pass
                    redis_driver = None
                selected_driver = sqlite_driver
        app.state.workflow_driver = selected_driver
        await selected_driver.start()
        yield
        if worker:
            await worker.stop()
        if redis_driver:
            await redis_driver.close()
        else:
            await selected_driver.close()
        store.close()

    app = FastAPI(title="Orchestra", version="0.2.0", lifespan=lifespan)
    # 把组件挂到 app.state，便于测试与中间件访问。
    app.state.store = store
    app.state.executor = executor
    app.state.retrieval_service = retrieval_service
    app.state.ingestion_service = ingestion_service
    app.state.workflow_driver = sqlite_driver

    @app.get("/")
    async def root() -> dict[str, str]:
        """服务根路径：返回服务名与文档地址。"""
        return {"service": "orchestra", "docs": "/docs"}

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """健康检查：供负载均衡/探活使用。"""
        return {"status": "ok"}

    # 异步提交：立即返回 task_id，结果通过查询或事件获取。
    @app.post("/api/v1/tasks", status_code=202)
    async def create_task(payload: CreateTaskRequest) -> dict[str, str]:
        """提交任务：立即返回 task_id，后台异步执行。

        Args:
            payload: 创建任务请求体。

        Returns:
            包含 task_id 的字典，前端凭此轮询或订阅事件。
        """
        task_input = _to_task_input(payload)
        task_id = await app.state.workflow_driver.submit(task_input)
        return {"task_id": task_id}

    @app.get("/api/v1/tasks/{task_id}")
    async def get_task(task_id: str) -> dict[str, Any]:
        """查询任务状态与结果。

        Args:
            task_id: 任务标识。

        Returns:
            任务完整信息（状态、结果、token 用量等）。

        Raises:
            HTTPException: 任务不存在时返回 404。
        """
        task = store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return task

    @app.delete("/api/v1/tasks/{task_id}")
    async def cancel_task(task_id: str) -> dict[str, str]:
        """取消任务（仅对未终止任务生效）。

        Args:
            task_id: 任务标识。

        Returns:
            取消结果。

        Raises:
            HTTPException: 任务不存在返回 404；已终止返回 409。
        """
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="task not found")
        if not await app.state.workflow_driver.cancel(task_id):
            raise HTTPException(status_code=409, detail="task already terminal")
        return {"task_id": task_id, "status": "cancelled"}

    @app.get("/api/v1/tasks/{task_id}/events")
    async def task_events(task_id: str):
        """订阅任务事件流（SSE）：实时推送任务进度与结果。

        Args:
            task_id: 任务标识。

        Returns:
            text/event-stream 响应，事件按 id 增量推送，终态后自动断开。
        """
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="task not found")
        return StreamingResponse(
            sse_stream(store, task_id),
            media_type="text/event-stream",
        )

    # 查看会话工作区，验证多 Agent 中间产物共享与最终答案落盘。
    @app.get("/api/v1/sessions/{session_id}/workspace")
    async def workspace_files(session_id: str) -> dict[str, Any]:
        """列出会话工作区内的所有文件及其内容。

        Args:
            session_id: 会话标识。

        Returns:
            文件列表与内容映射。
        """
        _safe_session_id(session_id)
        workspace = LocalWorkspace(settings.workspace_dir, session_id)
        files = await workspace.list_files()
        contents: dict[str, str] = {}
        for path in files:
            content = await workspace.read(path)
            if content is not None:
                contents[path] = content
        return {"session_id": session_id, "files": files, "contents": contents}

    @app.get("/api/v1/sessions/{session_id}/workspace/files/{file_path:path}")
    async def workspace_file(session_id: str, file_path: str) -> dict[str, str]:
        """读取工作区内的单个文件内容。

        Args:
            session_id: 会话标识。
            file_path: 工作区内相对路径（支持嵌套）。

        Returns:
            文件路径与内容。

        Raises:
            HTTPException: 路径非法返回 400；文件不存在返回 404。
        """
        _safe_session_id(session_id)
        workspace = LocalWorkspace(settings.workspace_dir, session_id)
        try:
            content = await workspace.read(file_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if content is None:
            raise HTTPException(status_code=404, detail="workspace file not found")
        return {"path": file_path, "content": content}

    @app.post("/api/v2/documents", status_code=201)
    async def upload_document(
        file: UploadFile = File(...),
        department: str = Form(...),
        title: str | None = Form(None),
    ) -> dict[str, Any]:
        """上传单个知识文档到指定部门并建立向量索引。"""
        if ingestion_service is None:
            raise HTTPException(status_code=503, detail="RAG 未启用，请检查 .env 的 Embedding/ChromaDB 配置")
        from .rag.departments import normalize_department

        normalized = normalize_department(department)
        filename = Path(file.filename or "document.txt").name
        target = settings.knowledge_dir / normalized / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(await file.read())
        record = await ingestion_service.index_file(target, department=normalized, title=title)
        return record.to_dict()

    @app.get("/api/v2/documents")
    async def list_documents(department: str | None = None) -> list[dict[str, Any]]:
        """列出已索引知识文档，可按部门筛选。"""
        if ingestion_service is None:
            raise HTTPException(status_code=503, detail="RAG 未启用，请检查 .env 的 Embedding/ChromaDB 配置")
        return ingestion_service.list_documents(department)

    @app.post("/api/v2/documents/ingest", status_code=202)
    async def ingest_knowledge_directory(department: str | None = None) -> dict[str, Any]:
        """扫描 data/knowledge/{department} 目录并增量索引全部支持文件。"""
        if ingestion_service is None:
            raise HTTPException(status_code=503, detail="RAG 未启用，请检查 .env 的 Embedding/ChromaDB 配置")
        records, errors = await ingestion_service.index_directory(department)
        return {
            "records": [record.to_dict() for record in records],
            "errors": errors,
        }

    @app.delete("/api/v2/documents/{document_id}")
    async def delete_document(document_id: str) -> dict[str, str]:
        """删除文档清单与对应向量块。"""
        if ingestion_service is None:
            raise HTTPException(status_code=503, detail="RAG 未启用，请检查 .env 的 Embedding/ChromaDB 配置")
        if not ingestion_service.delete(document_id):
            raise HTTPException(status_code=404, detail="document not found")
        return {"document_id": document_id, "status": "deleted"}

    @app.post("/api/v2/knowledge/search")
    async def search_knowledge(payload: KnowledgeSearchRequest) -> dict[str, Any]:
        """执行包 2 混合检索，返回命中的知识块、来源与置信度。"""
        if retrieval_service is None:
            raise HTTPException(status_code=503, detail="RAG 未启用，请检查 .env 的 Embedding/ChromaDB 配置")
        try:
            result = await retrieval_service.search(
                payload.query,
                department=payload.department,
                top_k=payload.top_k,
                mode=payload.mode,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"检索失败：{exc}")
        return result.to_dict()

    @app.get("/api/v1/scenarios")
    async def scenarios() -> list[dict[str, object]]:
        """返回预置业务场景清单（含策略、工具与 DAG 子任务）。"""
        return [scenario.to_dict() for scenario in ALL_SCENARIOS]

    return app


# SSE 增量推送：每次只取 id 大于 last_id 的事件，任务终态且发完后自动断开。
async def sse_stream(store: SQLiteStore, task_id: str) -> AsyncGenerator[str, None]:
    """SSE 事件流生成器：按事件 id 增量推送，任务终态后断开。

    Args:
        store: SQLite 存储，用于读取事件与任务状态。
        task_id: 任务标识。

    Yields:
        SSE 格式的事件文本（id/event/data 三行）。
    """
    last_id = 0
    while True:
        # 增量拉取：只取 id 大于 last_id 的新事件。
        events = store.list_events(task_id, after_id=last_id)
        for event in events:
            last_id = event["id"]
            data = json.dumps(event, ensure_ascii=False)
            # SSE 协议格式：id 行 + event 行 + data 行 + 空行分隔。
            yield f"id: {event['id']}\nevent: {event['event_type']}\ndata: {data}\n\n"
        task = store.get_task(task_id)
        if task is None:
            break
        # 任务已终态且事件全部发完时断开连接。
        if store.is_terminal(task["status"]) and not store.list_events(task_id, after_id=last_id):
            break
        # 无新事件时短暂休眠，避免空轮询打满 CPU。
        await asyncio.sleep(0.2)
