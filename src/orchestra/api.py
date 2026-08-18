"""Orchestra HTTP API：任务提交、查询、取消与 SSE 事件订阅。"""
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


class BudgetModel(BaseModel):
    total_tokens: int = 100_000
    per_agent_tokens: int = 20_000
    allow_model_fallback: bool = True


class CreateTaskRequest(BaseModel):
    query: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    user_id: str = "anonymous"
    context: dict[str, Any] = Field(default_factory=dict)
    strategy: str | None = None
    budget: BudgetModel | None = None
    max_iterations: int = Field(default=10, ge=1, le=100)
    workspace_enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


    # 将 HTTP 请求模型转换为核心契约 TaskInput。
def _to_task_input(request: CreateTaskRequest) -> TaskInput:
    budget = None
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


    # 组装存储、LLM、路由与执行器；settings 可注入以便测试。
def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = SQLiteStore(settings.db_file)
    provider = create_llm_provider(settings)
    llm_service = LLMService(provider, settings.llm_model, settings.fallback_model)
    router = RuleRouter()
    executor = Executor(
        store=store,
        llm_service=llm_service,
        router=router,
        workspace_root=settings.workspace_dir,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        store.close()

    app = FastAPI(title="Orchestra", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.executor = executor

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "orchestra", "docs": "/docs"}

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # 异步提交：立即返回 task_id，结果通过查询或事件获取。
    @app.post("/api/v1/tasks", status_code=202)
    async def create_task(payload: CreateTaskRequest) -> dict[str, str]:
        task_input = _to_task_input(payload)
        task_id = executor.submit(task_input)
        return {"task_id": task_id}

    @app.get("/api/v1/tasks/{task_id}")
    async def get_task(task_id: str) -> dict[str, Any]:
        task = store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return task

    @app.delete("/api/v1/tasks/{task_id}")
    async def cancel_task(task_id: str) -> dict[str, str]:
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="task not found")
        if not executor.cancel(task_id):
            raise HTTPException(status_code=409, detail="task already terminal")
        return {"task_id": task_id, "status": "cancelled"}

    @app.get("/api/v1/tasks/{task_id}/events")
    async def task_events(task_id: str):
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="task not found")
        return StreamingResponse(
            sse_stream(store, task_id),
            media_type="text/event-stream",
        )

    @app.get("/api/v1/scenarios")
    async def scenarios() -> list[dict[str, str]]:
        return [
            {"department": "人事", "scenario": "制度问答", "strategy": "simple+dag"},
            {"department": "风控", "scenario": "条款审查", "strategy": "dag+react"},
            {"department": "财务", "scenario": "报销政策问答", "strategy": "simple+dag"},
            {"department": "招采", "scenario": "合同条款问答", "strategy": "simple+dag"},
        ]

    return app


# SSE 增量推送：每次只取 id 大于 last_id 的事件，任务终态且发完后自动断开。
async def sse_stream(store: SQLiteStore, task_id: str) -> AsyncGenerator[str, None]:
    last_id = 0
    while True:
        events = store.list_events(task_id, after_id=last_id)
        for event in events:
            last_id = event["id"]
            data = json.dumps(event, ensure_ascii=False)
            yield f"id: {event['id']}\nevent: {event['event_type']}\ndata: {data}\n\n"
        task = store.get_task(task_id)
        if task is None:
            break
        if store.is_terminal(task["status"]) and not store.list_events(task_id, after_id=last_id):
            break
        await asyncio.sleep(0.2)