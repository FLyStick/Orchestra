from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class WorkspaceConfig:
    backend: str = "filesystem"
    root_dir: str = "./workspaces"
    redis_url: str | None = None


@runtime_checkable
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