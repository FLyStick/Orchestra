"""
SQLite 持久化：任务、事件与 Token 用量。
提供任务记录、事件流（供 SSE 增量拉取）与 Token 用量统计的存储能力。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts.task import TaskInput, TaskStatus

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# P2 先用 SQLite 与进程内锁完成轻量持久化，后续可替换为 Postgres/Temporal。
class SQLiteStore:
    """SQLite 存储层：任务、事件与 Token 用量的读写入口。

    线程安全：所有数据库操作通过 RLock 串行化；
    check_same_thread=False 允许在 asyncio 后台线程中访问同一连接。
    """

    def __init__(self, db_path: Path) -> None:
        self.path = db_path
        # 确保数据库所在目录存在（当前目录除外）。
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()  # 进程内锁，串行化所有写操作。
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row  # 行按列名访问。
        self._init_schema()

    def _init_schema(self) -> None:
        """建表与索引：任务、事件、Token 用量三张核心表。"""
        with self._lock:
            # 三张核心表：任务、事件、Token 用量；索引按 task_id 查询优化。
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    query TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    strategy TEXT,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    duration_ms INTEGER,
                    budget_json TEXT,
                    max_iterations INTEGER NOT NULL DEFAULT 10,
                    workspace_enabled INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id, id);
                CREATE INDEX IF NOT EXISTS idx_tokens_task ON token_usage(task_id);
                CREATE TABLE IF NOT EXISTS retry_queue (
                    key TEXT PRIMARY KEY,
                    due_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_retry_due ON retry_queue(due_at);
                """
            )
            # 兼容旧库：包 3 状态机与重试字段通过 ALTER TABLE 增量迁移。
            columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(tasks)")}
            for column, definition in (
                ("version", "INTEGER NOT NULL DEFAULT 0"),
                ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
                ("next_retry_at", "TEXT"),
            ):
                if column not in columns:
                    self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {definition}")
            self._conn.commit()

    # 落库即提交任务，后续执行通过 task_id 恢复上下文。
    def create_task(self, task: TaskInput, task_id: str | None = None) -> str:
        """创建任务记录，初始状态为 PENDING，返回 task_id。"""
        task_id = task_id or uuid.uuid4().hex
        now = _now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tasks (
                    task_id, query, session_id, user_id, context_json, strategy,
                    status, result_json, error, duration_ms, budget_json,
                    max_iterations, workspace_enabled, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    task.query,
                    task.session_id,
                    task.user_id,
                    json.dumps(task.context, ensure_ascii=False),
                    task.strategy,
                    TaskStatus.PENDING.value,
                    None,
                    None,
                    None,
                    # 预算对象序列化为 JSON 存储，读取时再还原。
                    json.dumps(
                        {
                            "total_tokens": task.budget.total_tokens,
                            "per_agent_tokens": task.budget.per_agent_tokens,
                            "allow_model_fallback": task.budget.allow_model_fallback,
                        }
                    )
                    if task.budget
                    else None,
                    task.max_iterations,
                    int(task.workspace_enabled),
                    json.dumps(task.metadata, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return task_id

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """按 task_id 查询任务，附带汇总的 Token 用量；不存在返回 None。"""
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    def _row_to_task(self, row: sqlite3.Row) -> dict[str, Any]:
        """将数据库行转换为字典：JSON 字段反序列化，并附带 Token 用量汇总。"""
        task = {
            "task_id": row["task_id"],
            "query": row["query"],
            "session_id": row["session_id"],
            "user_id": row["user_id"],
            "context": json.loads(row["context_json"] or "{}"),
            "strategy": row["strategy"],
            "status": row["status"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error"],
            "duration_ms": row["duration_ms"],
            "budget": json.loads(row["budget_json"]) if row["budget_json"] else None,
            "max_iterations": row["max_iterations"],
            "workspace_enabled": bool(row["workspace_enabled"]),
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "version": row["version"],
            "attempt_count": row["attempt_count"],
            "next_retry_at": row["next_retry_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "token_usage": {},
        }
        task["token_usage"] = self.aggregate_token_usage(row["task_id"])
        return task

    def update_task(
        self,
        task_id: str,
        *,
        status: TaskStatus,
        strategy: str | None = None,
        result: Any = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """更新任务状态与结果；strategy/duration_ms 用 COALESCE 保留旧值。"""
        now = _now()
        with self._lock:
            self._conn.execute(
                """
                UPDATE tasks
                SET status = ?, strategy = COALESCE(?, strategy), result_json = ?,
                    error = ?, duration_ms = COALESCE(?, duration_ms), updated_at = ?
                WHERE task_id = ?
                """,
                (
                    status.value,
                    strategy,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    duration_ms,
                    now,
                    task_id,
                ),
            )
            self._conn.commit()

    # 事件使用自增 id，SSE 可按 last_id 增量拉取。
    def append_event(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """追加一条事件记录，返回含自增 id 的事件字典（供 SSE 使用）。"""
        now = _now()
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO task_events (task_id, event_type, payload_json, occurred_at) VALUES (?, ?, ?, ?)",
                (task_id, event_type, json.dumps(payload or {}, ensure_ascii=False), now),
            )
            event_id = cursor.lastrowid
            self._conn.commit()
        return {"id": event_id, "task_id": task_id, "event_type": event_type, "payload": payload or {}, "occurred_at": now}

    def list_events(self, task_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        """按自增 id 增量拉取事件（after_id 之后），供 SSE 轮询使用。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, task_id, event_type, payload_json, occurred_at FROM task_events WHERE task_id = ? AND id > ? ORDER BY id",
                (task_id, after_id),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"] or "{}"),
                "occurred_at": row["occurred_at"],
            }
            for row in rows
        ]

    def record_token_usage(
        self,
        task_id: str,
        agent_id: str,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> None:
        """记录一次 LLM 调用的 Token 用量（按 agent 维度）。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO token_usage (task_id, agent_id, input_tokens, output_tokens, model, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, agent_id, input_tokens, output_tokens, model, _now()),
            )
            self._conn.commit()

    def get_token_usage(self, task_id: str) -> list[dict[str, Any]]:
        """查询任务的全部 Token 用量明细（按记录顺序）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT agent_id, input_tokens, output_tokens, model FROM token_usage WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # 汇总 Token 用量，供任务结果与成本统计使用。
    def aggregate_token_usage(self, task_id: str) -> dict[str, int]:
        """汇总任务的 Token 用量：输入/输出总量与调用次数。"""
        usage = self.get_token_usage(task_id)
        return {
            "input_tokens": sum(row["input_tokens"] for row in usage),
            "output_tokens": sum(row["output_tokens"] for row in usage),
            "calls": len(usage),
        }

    # 包 3：带状态前置条件的原子迁移，用于 Worker 幂等认领与重试调度。
    def transition_task(
        self,
        task_id: str,
        *,
        expected_statuses: tuple[TaskStatus, ...],
        status: TaskStatus,
        result: Any = None,
        error: str | None = None,
        strategy: str | None = None,
        duration_ms: int | None = None,
        increment_attempt: bool = False,
        next_retry_at: str | None = None,
    ) -> bool:
        """只有当前状态落在 expected_statuses 内才允许迁移，返回是否更新成功。"""
        now = _now()
        sets = ["status = ?", "version = version + 1", "updated_at = ?"]
        params: list[Any] = [status.value, now]
        if increment_attempt:
            sets.append("attempt_count = attempt_count + 1")
        if result is not None:
            sets.append("result_json = ?")
            params.append(json.dumps(result, ensure_ascii=False))
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if strategy is not None:
            sets.append("strategy = ?")
            params.append(strategy)
        if duration_ms is not None:
            sets.append("duration_ms = ?")
            params.append(duration_ms)
        if next_retry_at is not None:
            sets.append("next_retry_at = ?")
            params.append(next_retry_at)
        expected = tuple(item.value for item in expected_statuses)
        placeholders = ", ".join("?" * len(expected))
        params.extend([task_id, *expected])
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE tasks SET {', '.join(sets)} WHERE task_id = ? AND status IN ({placeholders})",
                params,
            )
            self._conn.commit()
        return cursor.rowcount == 1

    def list_non_terminal_tasks(self) -> list[dict[str, Any]]:
        """查询所有未完成任务，供 Worker 启动恢复与故障续跑。"""
        terminals = tuple(
            status.value
            for status in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED)
        )
        placeholders = ", ".join("?" * len(terminals))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM tasks WHERE status NOT IN ({placeholders}) ORDER BY created_at",
                terminals,
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def retry_schedule(self, key: str, due_at_ms: int) -> None:
        """登记或更新延迟重试任务，due_at_ms 使用毫秒时间戳。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO retry_queue (key, due_at) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET due_at = excluded.due_at",
                (key, int(due_at_ms)),
            )
            self._conn.commit()

    def retry_cancel(self, key: str) -> bool:
        """取消未到期的重试登记，返回是否成功移除。"""
        with self._lock:
            cursor = self._conn.execute("DELETE FROM retry_queue WHERE key = ?", (key,))
            self._conn.commit()
        return cursor.rowcount > 0

    def retry_pop_due(self, now_ms: int | None = None, limit: int = 100) -> list[str]:
        """弹出所有已到期的重试任务主键，供恢复/Worker 消费。"""
        now_ms = int(now_ms) if now_ms is not None else int(time.time() * 1000)
        with self._lock:
            rows = self._conn.execute(
                "SELECT key FROM retry_queue WHERE due_at <= ? ORDER BY due_at LIMIT ?",
                (now_ms, int(limit)),
            ).fetchall()
            keys = [row["key"] for row in rows]
            if keys:
                placeholders = ", ".join("?" * len(keys))
                self._conn.execute(f"DELETE FROM retry_queue WHERE key IN ({placeholders})", keys)
            self._conn.commit()
        return keys

    def close(self) -> None:
        """关闭数据库连接（应用退出时调用）。"""
        with self._lock:
            self._conn.close()

    @staticmethod
    def is_terminal(status: str) -> bool:
        """判断状态是否为终态（succeeded/failed/cancelled）。"""
        return status in {TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}