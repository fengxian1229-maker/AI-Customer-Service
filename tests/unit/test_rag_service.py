import asyncio

from app.services.rag import RagService, answer_from_rag_context


class FakeKnowledgeRepository:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls = []

    async def search(self, tenant_id: str, query: str, kb_scope: str = "default", limit: int = 3):
        self.calls.append((tenant_id, query, kb_scope, limit))
        return self.rows[:limit]


def test_rag_service_returns_matched_answer_from_knowledge_document():
    repository = FakeKnowledgeRepository([
        {"id": 1, "title": "Bonus rules", "content": "奖金规则以活动页面说明为准。", "score": 3}
    ])
    service = RagService(repository)

    result = asyncio.run(service.answer({"tenant_id": "default", "raw_user_input": "bonus rules"}))

    assert result["matched"] is True
    assert result["answer"] == "奖金规则以活动页面说明为准。"
    assert result["documents"] == [{"id": 1, "title": "Bonus rules", "score": 3}]
    assert repository.calls == [("default", "bonus rules", "default", 3)]


def test_rag_service_retrieve_uses_repository_search():
    repository = FakeKnowledgeRepository([
        {"id": 1, "title": "Bonus rules", "content": "奖金规则以活动页面说明为准。", "score": 3}
    ])
    service = RagService(repository)

    context = asyncio.run(service.retrieve({"tenant_id": "default", "raw_user_input": "bonus rules"}))

    assert context == {
        "documents": [{"id": 1, "title": "Bonus rules", "content": "奖金规则以活动页面说明为准。", "score": 3}],
        "source": "knowledge_documents",
        "fallback_reason": None,
    }
    assert repository.calls == [("default", "bonus rules", "default", 3)]


def test_rag_service_returns_safe_fallback_when_no_match():
    service = RagService(FakeKnowledgeRepository([]))

    result = asyncio.run(service.answer({"tenant_id": "default", "raw_user_input": "unknown"}))

    assert result["matched"] is False
    assert "暂时没有在知识库中找到" in result["answer"]
    assert result["documents"] == []
    assert result["fallback_reason"] == "no_match"


def test_rag_service_does_not_answer_backend_fact_questions():
    repository = FakeKnowledgeRepository([
        {"id": 1, "title": "Deposit", "content": "如何充值说明。", "score": 3}
    ])
    service = RagService(repository)

    result = asyncio.run(service.answer({"tenant_id": "default", "raw_user_input": "my deposit did not arrive"}))

    assert result["matched"] is False
    assert result["fallback_reason"] == "backend_fact"
    assert repository.calls == []


def test_rag_service_retrieve_does_not_query_repository_for_backend_fact_questions():
    repository = FakeKnowledgeRepository([
        {"id": 1, "title": "Deposit", "content": "如何充值说明。", "score": 3}
    ])
    service = RagService(repository)

    context = asyncio.run(service.retrieve({"tenant_id": "default", "raw_user_input": "my deposit did not arrive"}))

    assert context == {
        "documents": [],
        "source": "guardrail",
        "fallback_reason": "backend_fact",
    }
    assert repository.calls == []


def test_answer_from_rag_context_returns_document_answer_and_summary_without_content():
    result = answer_from_rag_context(
        {
            "rag_context": {
                "documents": [
                    {
                        "id": 1,
                        "title": "Bonus rules",
                        "content": "奖金规则以活动页面说明为准。",
                        "score": 5,
                    }
                ],
                "source": "knowledge_documents",
                "fallback_reason": None,
            }
        }
    )

    assert result["matched"] is True
    assert result["answer"] == "奖金规则以活动页面说明为准。"
    assert result["documents"] == [{"id": 1, "title": "Bonus rules", "score": 5}]
    assert "content" not in result["documents"][0]


def test_answer_from_rag_context_returns_no_match_fallback_without_documents():
    result = answer_from_rag_context({"rag_context": {"documents": [], "fallback_reason": None}})

    assert result["matched"] is False
    assert "暂时没有在知识库中找到" in result["answer"]
    assert result["fallback_reason"] == "no_match"


def test_answer_from_rag_context_returns_backend_fact_fallback():
    result = answer_from_rag_context({"rag_context": {"documents": [], "fallback_reason": "backend_fact"}})

    assert result["matched"] is False
    assert "需要查询账户或订单状态" in result["answer"]
    assert result["fallback_reason"] == "backend_fact"


def test_rag_service_matches_multilingual_static_queries():
    service = RagService()

    english = asyncio.run(service.answer({"tenant_id": "default", "raw_user_input": "how to deposit"}))
    spanish = asyncio.run(service.answer({"tenant_id": "default", "raw_user_input": "cómo recargar"}))
    chinese = asyncio.run(service.answer({"tenant_id": "default", "raw_user_input": "如何充值"}))

    assert english["matched"] is True
    assert spanish["matched"] is True
    assert chinese["matched"] is True
    assert "充值" in chinese["answer"]
