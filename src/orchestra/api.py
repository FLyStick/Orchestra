"""Orchestra HTTP API：任务提交、查询、取消、SSE 订阅与 Workspace 查看。

对外暴露 REST 接口（任务生命周期管理）与 SSE 接口（事件流订阅），
前端通过 POST 提交任务、轮询/SSE 获取进度与最终答案。
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .contracts.task import TaskInput, TokenBudget
from .executor import Executor
from .llm import LLMService, create_llm_provider
from .router import RuleRouter
from .store import SQLiteStore
from .workspace.local_workspace import LocalWorkspace


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
    # 执行器：后台执行任务，串联路由、策略与存储。
    executor = Executor(
        store=store,
        llm_service=llm_service,
        router=router,
        workspace_root=settings.workspace_dir,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # 应用启动时无需额外初始化；关闭时释放数据库连接。
        yield
        store.close()

    app = FastAPI(title="Orchestra", version="0.2.0", lifespan=lifespan)
    # 把组件挂到 app.state，便于测试与中间件访问。
    app.state.store = store
    app.state.executor = executor

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
        task_id = executor.submit(task_input)
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
        if not executor.cancel(task_id):
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

    @app.get("/api/v1/scenarios")
    async def scenarios() -> list[dict[str, str]]:
        """返回预置业务场景清单，供前端展示示例入口。"""
        return [
            {"department": "人事", "scenario": "制度问答", "strategy": "simple+dag"},
            {"department": "风控", "scenario": "条款审查", "strategy": "react+dag"},
            {"department": "财务", "scenario": "报销政策问答", "strategy": "simple+dag"},
            {"department": "招采", "scenario": "合同条款问答", "strategy": "react+dag"},
        ]

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
