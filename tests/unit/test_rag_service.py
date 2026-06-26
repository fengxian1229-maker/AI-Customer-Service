import asyncio

from app.services.rag import BACKEND_FACT_FALLBACK_ANSWER, RAG_FALLBACK_ANSWER, RagService, answer_from_rag_context, rank_knowledge_document


class FakeKnowledgeRepository:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls = []

    async def search(self, tenant_id: str, query: str, kb_scope: str = "default", limit: int = 3):
        self.calls.append((tenant_id, query, kb_scope, limit))
        return self.rows[:limit]


def test_rag_service_returns_matched_answer_from_knowledge_document():
    repository = FakeKnowledgeRepository([
        {
            "id": 1,
            "title": "Bonus rules",
            "content": "奖金规则以活动页面说明为准。",
            "score": 12,
            "priority": 20,
            "matched_fields": ["title", "keywords"],
            "matched_terms": ["bonus rules"],
        }
    ])
    service = RagService(repository)

    result = asyncio.run(service.answer({"tenant_id": "default", "raw_user_input": "bonus rules"}))

    assert result["matched"] is True
    assert result["answer"] == "奖金规则以活动页面说明为准。"
    assert result["documents"] == [
        {
            "id": 1,
            "title": "Bonus rules",
            "score": 12,
            "priority": 20,
            "matched_fields": ["title", "keywords"],
            "matched_terms": ["bonus rules"],
        }
    ]
    assert repository.calls == [("default", "bonus rules", "default", 3)]


def test_rag_service_retrieve_uses_repository_search():
    repository = FakeKnowledgeRepository([
        {
            "id": 1,
            "title": "Bonus rules",
            "content": "奖金规则以活动页面说明为准。",
            "score": 12,
            "priority": 20,
            "matched_fields": ["title", "keywords"],
            "matched_terms": ["bonus rules"],
        }
    ])
    service = RagService(repository)

    context = asyncio.run(service.retrieve({"tenant_id": "default", "raw_user_input": "bonus rules"}))

    assert context["matched"] is True
    assert context["answer"] == "奖金规则以活动页面说明为准。"
    assert context["source"] == "knowledge_documents"
    assert context["fallback_reason"] is None
    assert context["tenant_id"] == "default"
    assert context["kb_scope"] == "default"
    assert context["query"] == "bonus rules"
    assert context["documents"][0]["matched_fields"] == ["title", "keywords"]
    assert repository.calls == [("default", "bonus rules", "default", 3)]


def test_rag_service_returns_safe_fallback_when_no_match():
    service = RagService(FakeKnowledgeRepository([]))

    result = asyncio.run(service.answer({"tenant_id": "default", "raw_user_input": "unknown"}))

    assert result["matched"] is False
    assert result["answer"] == RAG_FALLBACK_ANSWER
    assert result["documents"] == []
    assert result["fallback_reason"] in {"no_match", "low_score"}


def test_rag_service_does_not_answer_backend_fact_questions():
    repository = FakeKnowledgeRepository([
        {"id": 1, "title": "Deposit", "content": "如何充值说明。", "score": 3}
    ])
    service = RagService(repository)

    result = asyncio.run(service.answer({"tenant_id": "default", "raw_user_input": "my deposit did not arrive"}))

    assert result["matched"] is False
    assert result["answer"] == BACKEND_FACT_FALLBACK_ANSWER
    assert result["fallback_reason"] == "backend_fact"
    assert repository.calls == []


def test_rag_service_retrieve_does_not_query_repository_for_backend_fact_questions():
    repository = FakeKnowledgeRepository([
        {"id": 1, "title": "Deposit", "content": "如何充值说明。", "score": 3}
    ])
    service = RagService(repository)

    context = asyncio.run(service.retrieve({"tenant_id": "default", "raw_user_input": "my deposit did not arrive"}))

    assert context["matched"] is False
    assert context["answer"] == BACKEND_FACT_FALLBACK_ANSWER
    assert context["documents"] == []
    assert context["source"] == "guardrail"
    assert context["fallback_reason"] == "backend_fact"
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
                        "priority": 20,
                        "matched_fields": ["title"],
                        "matched_terms": ["bonus"],
                    }
                ],
                "source": "knowledge_documents",
                "fallback_reason": None,
                "answer": "奖金规则以活动页面说明为准。",
            }
        }
    )

    assert result["matched"] is True
    assert result["answer"] == "奖金规则以活动页面说明为准。"
    assert result["documents"] == [
        {
            "id": 1,
            "title": "Bonus rules",
            "score": 5,
            "priority": 20,
            "matched_fields": ["title"],
            "matched_terms": ["bonus"],
        }
    ]
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


def test_rank_knowledge_document_prefers_exact_title_match():
    best = rank_knowledge_document(
        {
            "id": 1,
            "title": "Bonus rules",
            "content": "奖金规则以活动页面说明为准。",
            "keywords": ["bonus"],
            "priority": 20,
            "language": "multi",
        },
        "bonus rules",
    )
    weak = rank_knowledge_document(
        {
            "id": 2,
            "title": "General help",
            "content": "This page mentions bonus rules once.",
            "keywords": [],
            "priority": 1,
            "language": "multi",
        },
        "bonus rules",
    )

    assert best["score"] > weak["score"]
    assert best["matched_fields"] == ["title", "keywords"]


def test_rank_knowledge_document_prefers_exact_keyword_over_content_match():
    keyword_doc = rank_knowledge_document(
        {
            "id": 1,
            "title": "Promotion center",
            "content": "See general help.",
            "keywords": ["bonus rules"],
            "priority": 50,
            "language": "multi",
        },
        "bonus rules",
    )
    content_doc = rank_knowledge_document(
        {
            "id": 2,
            "title": "General help",
            "content": "The bonus rules are shown somewhere in content.",
            "keywords": [],
            "priority": 1,
            "language": "multi",
        },
        "bonus rules",
    )

    assert keyword_doc["score"] > content_doc["score"]


def test_rank_knowledge_document_supports_chinese_query():
    result = rank_knowledge_document(
        {
            "id": 1,
            "title": "奖金规则说明",
            "content": "奖金规则以活动页面说明为准。",
            "keywords": ["奖金", "活动规则"],
            "priority": 20,
            "language": "zh",
        },
        "奖金规则",
        language="zh",
    )

    assert result["score"] > 0
    assert "title" in result["matched_fields"]


def test_rag_service_empty_query_does_not_query_repository():
    repository = FakeKnowledgeRepository([
        {"id": 1, "title": "Bonus rules", "content": "奖金规则以活动页面说明为准。", "score": 12}
    ])
    service = RagService(repository)

    context = asyncio.run(service.retrieve({"tenant_id": "default", "raw_user_input": "   "}))

    assert context["matched"] is False
    assert context["fallback_reason"] == "empty_query"
    assert repository.calls == []


def test_rag_service_low_score_returns_fallback():
    repository = FakeKnowledgeRepository([
        {
            "id": 1,
            "title": "General help",
            "content": "bonus appears once only.",
            "score": 1,
            "priority": 1,
            "matched_fields": ["content"],
            "matched_terms": ["bonus"],
        }
    ])
    service = RagService(repository, min_score=2)

    context = asyncio.run(service.retrieve({"tenant_id": "default", "raw_user_input": "bonus"}))

    assert context["matched"] is False
    assert context["answer"] == RAG_FALLBACK_ANSWER
    assert context["fallback_reason"] == "low_score"


def test_answer_from_rag_context_without_context_uses_static_fallback():
    result = answer_from_rag_context({"raw_user_input": "如何充值"})

    assert result["matched"] is True
    assert "充值" in result["answer"]
