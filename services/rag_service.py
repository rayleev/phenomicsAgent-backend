"""Built-in RAG query service.

Calls the existing paperRAG query API at http://10.33.105.145:8001/query.
"""

import httpx

from backend.services.base import BaseService, ServiceResult


class RAGQueryService(BaseService):
    """Service that queries the paper RAG knowledge base."""

    name = "rag_query"
    description = "检索论文RAG知识库，查询学术论文和相关研究资料，适用于生物学、育种、表型组学等领域的文献检索"

    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "查询内容，如研究主题、基因名称、技术方法等",
            },
            "enable_web_search": {
                "type": "boolean",
                "description": "是否启用联网搜索以补充本地知识库",
                "default": True,
            },
        },
        "required": ["query"],
    }

    def __init__(self, base_url: str = "http://10.33.105.145:8001", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def invoke(self, **kwargs) -> ServiceResult:
        query = kwargs.get("query", "")
        if not query:
            return ServiceResult(success=False, error="query parameter is required")

        enable_web_search = kwargs.get("enable_web_search", True)

        payload = {
            "query": query,
            "session_id": kwargs.get("session_id", ""),
            "enable_web_search": enable_web_search,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/query",
                    json=payload,
                )
                if resp.is_success:
                    return ServiceResult(
                        success=True,
                        data=resp.json(),
                        status_code=resp.status_code,
                    )
                else:
                    return ServiceResult(
                        success=False,
                        error=f"RAG service returned HTTP {resp.status_code}: {resp.text[:500]}",
                        status_code=resp.status_code,
                    )
        except httpx.TimeoutException:
            return ServiceResult(
                success=False,
                error=f"RAG service timed out after {self.timeout}s",
            )
        except httpx.RequestError as e:
            return ServiceResult(
                success=False,
                error=f"Failed to reach RAG service: {e}",
            )
