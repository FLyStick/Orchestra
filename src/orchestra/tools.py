"""内置工具：RAG 检索、工作区读取与列表，供 React 策略调用。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class KnowledgeDoc:
    title: str
    content: str
    source: str


# 内置演示知识库：后续可替换为真实制度文档或向量检索服务。
SEED_KNOWLEDGE: tuple[KnowledgeDoc, ...] = (
    KnowledgeDoc(
        title="年假管理制度",
        source="hr/leave-policy.md",
        content=(
            "公司年假制度：累计工作满 1 年享受 5 天年假，满 10 年享受 10 天，"
            "满 20 年享受 15 天。年假申请需要通过 OA 提交，并提前 3 个工作日申请；"
            "休半天需在申请单中选择上午或下午。"
        ),
    ),
    KnowledgeDoc(
        title="差旅报销标准",
        source="finance/expense-policy.md",
        content=(
            "差旅报销标准：市内交通凭发票实报实销，住宿费按城市等级设置上限；"
            "报销单需附带行程说明、发票与审批记录，缺一不可。"
        ),
    ),
    KnowledgeDoc(
        title="合同付款风险条款",
        source="risk/contract-risk.md",
        content=(
            "合同审查重点关注付款节点、验收标准、违约金比例与争议解决条款；"
            "若付款节点与验收条款未绑定，属于高风险情形，需法务复核。"
        ),
    ),
)


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
    registry.register(WorkspaceReadTool())
    registry.register(WorkspaceListTool())
    return registry
