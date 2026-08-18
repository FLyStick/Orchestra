"""Workspace 抽象契约，文件与内存实现均遵循该协议。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class WorkspaceConfig:
    backend: str = "filesystem"
    root_dir: str = "./workspaces"
    redis_url: str | None = None


@runtime_checkable
# 策略层只依赖该协议，不关心底层是文件系统还是 Redis。
class Workspace(Protocol):
    @property
    def session_id(self) -> str:
        ...

    async def read(self, path: str) -> str | None:
        ...

    async def write(self, path: str, content: str) -> None:
        ...

    async def list_files(self) -> list[str]:
        ...