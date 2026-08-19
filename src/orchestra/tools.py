"""内置工具：RAG 检索、工作区读取与列表，供 React/DAG 策略调用。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .knowledge import DEMO_CONTRACTS, KNOWLEDGE_DOCS as SEED_KNOWLEDGE, KnowledgeDoc

@dataclass
class ToolResult:
    output: str
    success: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
# 工具协议：React 策略通过 ToolRegistry 按名称查找并异步调用。
class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]

    async def run(self, arguments: dict[str, Any], context: Any) -> ToolResult:
        ...


_LATIN_WORD = re.compile(r"[a-z0-9_]+")


def _keywords(text: str) -> set[str]:
    """提取用于检索的中文二元词组与英文关键词。"""
    words = list(_LATIN_WORD.findall(text.lower()))
    chars = [ch for ch in text if "\u4e00" <= ch <= "\u9fff"]
    words.extend("".join(chars[i : i + 2]) for i in range(len(chars) - 1))
    return {word for word in words if len(word) > 1}


def _score(doc: KnowledgeDoc, keywords: set[str]) -> int:
    lower = doc.content.lower()
    return sum(lower.count(keyword) for keyword in keywords)


class KeywordRAGTool:
    """关键词 RAG 工具：从内置制度文档中检索最相关片段并写入工作区。"""

    name = "rag_search"
    description = "在内部制度与知识库中检索与问题相关的文档片段"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "需要检索的问题或关键词"}
        },
        "required": ["query"],
    }

    def __init__(self, documents: tuple[KnowledgeDoc, ...] = SEED_KNOWLEDGE) -> None:
        self.documents = documents

    async def run(self, arguments: dict[str, Any], context: Any) -> ToolResult:
        query = str(arguments.get("query") or "")
        keywords = _keywords(query)
        ordered = sorted(self.documents, key=lambda doc: _score(doc, keywords), reverse=True)
        hits = [doc for doc in ordered if _score(doc, keywords) > 0][:2]
        if not hits:
            return ToolResult(
                output="未检索到相关制度文档，请补充更具体的关键词。",
                success=False,
                metadata={"hits": 0, "query": query},
            )
        parts: list[str] = []
        for doc in hits:
            parts.append(f"标题：{doc.title}\n来源：{doc.source}\n内容：{doc.content[:180]}")
            await context.workspace.write(f"rag/{doc.source}", doc.content)
        return ToolResult(
            output="\n\n".join(parts),
            success=True,
            metadata={"hits": len(hits), "sources": [doc.source for doc in hits]},
        )

class ContractContextTool:
    """合同上下文工具：提取演示合同中的付款、验收、违约金与争议解决条款。"""

    name = "contract_context"
    description = "提取待审合同中的付款、验收、违约金与争议解决条款"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "contract_id": {"type": "string", "description": "演示合同 ID，默认 demo"},
            "path": {"type": "string", "description": "工作区中的合同文件相对路径（可选）"}
        },
        "required": [],
    }

    async def run(self, arguments: dict[str, Any], context: Any) -> ToolResult:
        path = arguments.get("path")
        contract_id = str(arguments.get("contract_id") or "demo")
        if path:
            content = await context.workspace.read(str(path))
            if content is None:
                return ToolResult(
                    output=f"工作区中不存在合同文件：{path}",
                    success=False,
                    metadata={"path": path},
                )
            source = str(path)
            contract_id = str(path).replace("/", "_").replace("\\", "_")
        else:
            content = DEMO_CONTRACTS.get(contract_id)
            if not content:
                return ToolResult(
                    output=f"未找到演示合同：{contract_id}",
                    success=False,
                    metadata={"contract_id": contract_id},
                )
            source = f"contracts/{contract_id}.md"
        await context.workspace.write(f"contracts/{contract_id}.md", content)
        return ToolResult(
            output=f"合同来源：{source}\n\n{content}",
            success=True,
            metadata={"contract_id": contract_id, "source": source},
        )


class WorkspaceReadTool:
    """读取当前会话工作区中的文件，供多 Agent 共享上下文。"""

    name = "workspace_read"
    description = "读取当前会话工作区中的文件内容"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "工作区内相对路径"}},
        "required": ["path"],
    }

    async def run(self, arguments: dict[str, Any], context: Any) -> ToolResult:
        path = str(arguments.get("path") or "")
        content = await context.workspace.read(path)
        if content is None:
            return ToolResult(
                output=f"工作区中不存在文件：{path}",
                success=False,
                metadata={"path": path},
            )
        return ToolResult(output=content, success=True, metadata={"path": path})


class WorkspaceListTool:
    """列出当前会话工作区中的全部文件。"""

    name = "workspace_list"
    description = "列出当前会话工作区中的全部文件路径"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def run(self, arguments: dict[str, Any], context: Any) -> ToolResult:
        files = await context.workspace.list_files()
        output = "\n".join(files) if files else "工作区当前为空"
        return ToolResult(output=output, success=True, metadata={"files": files})


class ToolRegistry:
    """工具注册表：按名称查找工具，并向 LLM 输出工具模式。"""

    def __init__(self, tools: dict[str, Tool] | None = None) -> None:
        self._tools = dict(tools or {})

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in self._tools.values()
        ]


def create_tool_registry() -> ToolRegistry:
    """创建默认工具集，包含 RAG 与工作区工具。"""
    registry = ToolRegistry()
    registry.register(KeywordRAGTool())
    registry.register(ContractContextTool())
    registry.register(WorkspaceReadTool())
    registry.register(WorkspaceListTool())
    return registry
