import asyncio

from app.services.rag import (
    BACKEND_FACT_FALLBACK_ANSWER,
    RAG_FALLBACK_ANSWER,
    RagService,
    answer_from_rag_context,
    answer_from_static_knowledge,
    is_allowed_faq_document,
    rank_knowledge_document,
    search_static_knowledge,
)


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
            "title": "充值教程",
            "content": "按页面提示完成充值。",
            "metadata_json": {"intent_id": "deposit_howto", "is_canonical": True},
            "language": "multi",
            "score": 12,
            "priority": 20,
            "matched_fields": ["title", "keywords"],
            "matched_terms": ["充值教程"],
        }
    ])
    service = RagService(repository)

    result = asyncio.run(service.answer({"tenant_id": "default", "raw_user_input": "充值教程"}))

    assert result["matched"] is True
    assert result["answer"] == "按页面提示完成充值。"
    assert result["documents"] == [
        {
            "id": 1,
            "title": "充值教程",
            "score": 12,
            "priority": 20,
            "matched_fields": ["title", "keywords"],
            "matched_terms": ["充值教程"],
        }
    ]
    assert repository.calls == [("default", "充值教程", "default", 3)]


def test_rag_service_retrieve_uses_repository_search():
    repository = FakeKnowledgeRepository([
        {
            "id": 1,
            "title": "充值教程",
            "content": "按页面提示完成充值。",
            "metadata_json": {"intent_id": "deposit_howto", "is_canonical": True},
            "language": "multi",
            "score": 12,
            "priority": 20,
            "matched_fields": ["title", "keywords"],
            "matched_terms": ["充值教程"],
            "answer_blocks": [{"type": "text", "text": "按页面提示完成充值。"}],
        }
    ])
    service = RagService(repository)

    context = asyncio.run(service.retrieve({"tenant_id": "default", "raw_user_input": "充值教程"}))

    assert context["matched"] is True
    assert context["answer"] == "按页面提示完成充值。"
    assert context["source"] == "knowledge_documents"
    assert context["fallback_reason"] is None
    assert context["tenant_id"] == "default"
    assert context["kb_scope"] == "default"
    assert context["query"] == "充值教程"
    assert context["documents"][0]["matched_fields"] == ["title", "keywords"]
    assert context["answer_blocks"] == [{"type": "text", "text": "按页面提示完成充值。"}]
    assert context["documents"][0]["has_answer_blocks"] is True
    assert context["documents"][0]["block_types"] == ["text"]
    assert context["documents"][0]["asset_keys"] == []
    assert repository.calls == [("default", "充值教程", "default", 3)]


def test_rag_service_uses_router_faq_intent_as_candidate_scope():
    repository = FakeKnowledgeRepository([
        {
            "id": 1,
            "title": "提款教程",
            "content": "按页面提示提交提款申请。",
            "metadata_json": {"intent_id": "withdrawal_howto", "is_canonical": True},
            "score": 12,
            "priority": 10,
        },
        {
            "id": 2,
            "title": "充值教程",
            "content": "按页面提示完成充值。",
            "metadata_json": {"intent_id": "deposit_howto", "is_canonical": True},
            "score": 10,
            "priority": 20,
        },
    ])
    service = RagService(repository)

    context = asyncio.run(
        service.retrieve(
            {
                "tenant_id": "default",
                "intent_result": {
                    "route": "faq",
                    "intent": "deposit_howto",
                    "faq_intent": "deposit_howto",
                    "retrieval_query": "提款教程",
                },
            }
        )
    )

    assert context["matched"] is True
    assert context["answer"] == "按页面提示完成充值。"
    assert context["documents"][0]["title"] == "充值教程"
    assert repository.calls == [("default", "提款教程", "default", 10)]


def test_rag_service_does_not_answer_when_router_faq_intent_conflicts_with_documents():
    repository = FakeKnowledgeRepository([
        {
            "id": 1,
            "title": "提款教程",
            "content": "按页面提示提交提款申请。",
            "metadata_json": {"intent_id": "withdrawal_howto", "is_canonical": True},
            "score": 12,
            "priority": 10,
        }
    ])
    service = RagService(repository)

    context = asyncio.run(
        service.retrieve(
            {
                "tenant_id": "default",
                "intent_result": {
                    "route": "faq",
                    "intent": "deposit_howto",
                    "faq_intent": "deposit_howto",
                    "retrieval_query": "提款教程",
                },
            }
        )
    )

    assert context["matched"] is False
    assert context["fallback_reason"] == "no_match"
    assert context["answer"] == RAG_FALLBACK_ANSWER


def test_is_allowed_faq_document_requires_allowed_intent_and_canonical_true():
    assert is_allowed_faq_document(
        {"title": "充值教程", "metadata_json": {"intent_id": "deposit_howto", "is_canonical": True}}
    ) is True
    assert is_allowed_faq_document(
        {"title": "充值教程", "metadata_json": {"intent_id": "deposit_howto", "is_canonical": "true"}}
    ) is True
    assert is_allowed_faq_document(
        {"title": "充值教程", "metadata_json": {"intent_id": "deposit_howto", "is_canonical": "True"}}
    ) is True
    assert is_allowed_faq_document(
        {"title": "充值教程", "metadata_json": {"intent_id": "deposit_howto"}}
    ) is False
    assert is_allowed_faq_document(
        {"title": "充值教程", "metadata_json": {"intent_id": "deposit_howto", "is_canonical": False}}
    ) is False
    assert is_allowed_faq_document(
        {"title": "奖金规则说明", "metadata_json": {"intent_id": "faq_general", "is_canonical": True}}
    ) is False


def test_rag_service_filters_legacy_repository_documents_even_when_db_returns_them():
    repository = FakeKnowledgeRepository([
        {
            "id": 10,
            "title": "奖金规则说明",
            "content": "旧奖金 FAQ 不应返回。",
            "metadata_json": {"intent_id": "faq_general"},
            "score": 99,
            "priority": 1,
        },
        {
            "id": 11,
            "title": "菜单导航帮助",
            "content": "旧菜单 FAQ 不应返回。",
            "metadata_json": {"intent_id": "menu_help"},
            "score": 98,
            "priority": 2,
        },
        {
            "id": 12,
            "title": "流水要求说明",
            "content": "旧流水 FAQ 不应返回。",
            "metadata_json": {"intent_id": "rollover_explanation"},
            "score": 97,
            "priority": 3,
        },
    ])
    service = RagService(repository)

    context = asyncio.run(service.retrieve({"tenant_id": "default", "raw_user_input": "奖金规则是什么"}))

    assert context["matched"] is False
    assert context["documents"] == []
    assert context["fallback_reason"] == "no_match"


def test_rag_service_retrieves_all_canonical_faq_intents_from_repository():
    cases = [
        ("如何充值", "充值教程", "deposit_howto"),
        ("如何提款", "提款教程", "withdrawal_howto"),
        ("忘记密码", "忘记密码说明", "forgot_password_howto"),
        ("如何上传截图", "上传截图说明", "screenshot_upload_howto"),
    ]

    for query, title, intent in cases:
        repository = FakeKnowledgeRepository([
            {
                "id": 1,
                "title": title,
                "content": f"{title} answer",
                "metadata_json": {"intent_id": intent, "is_canonical": True},
                "score": 12,
                "priority": 10,
            }
        ])
        service = RagService(repository)

        context = asyncio.run(service.retrieve({"tenant_id": "default", "raw_user_input": query}))

        assert context["matched"] is True
        assert context["documents"][0]["title"] == title


def test_rag_service_does_not_match_legacy_static_faq_questions():
    service = RagService()

    for query in ("流水为什么没变", "奖金规则是什么", "菜单在哪里", "账户安全问题"):
        context = asyncio.run(service.retrieve({"tenant_id": "default", "raw_user_input": query}))

        assert context["matched"] is False
        assert context["documents"] == []


def test_static_fallback_returns_canonical_default_knowledge_documents():
    result = answer_from_static_knowledge({"raw_user_input": "如何充值"})
    documents = search_static_knowledge("default", "如何充值")

    assert result["matched"] is True
    assert "你可以在充值页面选择可用通道" in result["answer"]
    assert documents[0]["metadata_json"] == {"intent_id": "deposit_howto", "is_canonical": True}


def test_rag_service_retrieve_prefers_llm_faq_query_then_normalized_query():
    repository = FakeKnowledgeRepository([
        {
            "id": 1,
            "title": "充值教程",
            "content": "请打开充值教程。",
            "metadata_json": {"intent_id": "deposit_howto", "is_canonical": True},
            "score": 12,
            "priority": 20,
            "matched_fields": ["question_aliases"],
            "matched_terms": ["怎么存款"],
            "answer_blocks": [{"type": "text", "text": "请打开充值教程。"}],
        }
    ])
    service = RagService(repository)

    context = asyncio.run(
        service.retrieve(
            {
                "tenant_id": "default",
                "raw_user_input": "mi deposito no llegó",
                "rewritten_question": "mi deposito no llegó",
                "rewrite_result": {"normalized_query": "deposit issue"},
                "intent_result": {"faq_query": "怎么存款"},
                "rag_backend_fact_guard_enabled": False,
            }
        )
    )

    assert context["matched"] is True
    assert context["query"] == "怎么存款"
    assert repository.calls == [("default", "怎么存款", "default", 3)]


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
    assert context["answer_blocks"] == [{"type": "text", "text": BACKEND_FACT_FALLBACK_ANSWER}]
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
                        "title": "充值教程",
                        "content": "按页面提示完成充值。",
                        "metadata_json": {"intent_id": "deposit_howto", "is_canonical": True},
                        "score": 5,
                        "priority": 20,
                        "matched_fields": ["title"],
                        "matched_terms": ["充值"],
                    }
                ],
                "source": "knowledge_documents",
                "fallback_reason": None,
                "answer": "按页面提示完成充值。",
            }
        }
    )

    assert result["matched"] is True
    assert result["answer"] == "按页面提示完成充值。"
    assert result["documents"] == [
        {
            "id": 1,
            "title": "充值教程",
            "score": 5,
            "priority": 20,
            "matched_fields": ["title"],
            "matched_terms": ["充值"],
        }
    ]
    assert "content" not in result["documents"][0]


def test_answer_from_rag_context_filters_legacy_documents_before_returning_answer():
    result = answer_from_rag_context(
        {
            "rag_context": {
                "documents": [
                    {
                        "id": 1,
                        "title": "Bonus rules",
                        "content": "旧奖金 FAQ 不应返回。",
                        "metadata_json": {"intent_id": "faq_general"},
                        "score": 99,
                    }
                ],
                "source": "knowledge_documents",
                "fallback_reason": None,
                "answer": "旧奖金 FAQ 不应返回。",
            }
        }
    )

    assert result["matched"] is False
    assert result["documents"] == []
    assert result["fallback_reason"] == "no_match"


def test_answer_from_rag_context_uses_canonical_document_content_over_polluted_answer():
    result = answer_from_rag_context(
        {
            "rag_context": {
                "documents": [
                    {
                        "id": 1,
                        "title": "充值教程",
                        "content": "canonical content",
                        "metadata_json": {"intent_id": "deposit_howto", "is_canonical": True},
                        "score": 99,
                    }
                ],
                "source": "knowledge_documents",
                "fallback_reason": None,
                "answer": "污染答案",
            }
        }
    )

    assert result["matched"] is True
    assert result["answer"] == "canonical content"


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


def test_static_knowledge_retrieves_all_four_canonical_faq_intents():
    service = RagService()
    cases = [
        ("如何充值", "deposit_howto"),
        ("如何提款", "withdrawal_howto"),
        ("忘记密码", "forgot_password_howto"),
        ("如何上传截图", "screenshot_upload_howto"),
    ]

    for query, intent in cases:
        context = asyncio.run(
            service.retrieve(
                {
                    "tenant_id": "default",
                    "raw_user_input": query,
                    "intent_result": {"intent": intent, "faq_intent": intent, "faq_query": query},
                }
            )
        )

        assert context["matched"] is True
        assert context["faq_intent"] == intent
        assert context["documents"][0]["metadata_json"] == {"intent_id": intent, "is_canonical": True}


def test_static_forgot_password_answer_explains_human_handoff():
    result = asyncio.run(RagService().answer({"tenant_id": "default", "raw_user_input": "忘记密码"}))

    assert result["matched"] is True
    assert "错误截图" in result["answer"]
    assert "人工客服" in result["answer"]


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


def test_rank_knowledge_document_matches_question_aliases():
    alias_doc = rank_knowledge_document(
        {
            "id": 1,
            "title": "充值教程",
            "content": "按页面提示完成充值。",
            "keywords": ["deposit"],
            "question_aliases": ["how can I recharge", "cómo recargar"],
            "priority": 10,
            "language": "multi",
        },
        "how can I recharge",
    )
    content_doc = rank_knowledge_document(
        {
            "id": 2,
            "title": "General",
            "content": "how can I recharge appears once.",
            "keywords": [],
            "question_aliases": [],
            "priority": 1,
            "language": "multi",
        },
        "how can I recharge",
    )

    assert alias_doc["score"] > content_doc["score"]
    assert "question_aliases" in alias_doc["matched_fields"]
    assert "how can i recharge" in alias_doc["matched_terms"]


def test_rank_knowledge_document_scores_alias_contains_query():
    result = rank_knowledge_document(
        {
            "id": 1,
            "title": "提款教程",
            "content": "按页面提示申请提款。",
            "keywords": [],
            "question_aliases": ["how do I withdraw money"],
            "priority": 10,
            "language": "multi",
        },
        "withdraw money",
    )

    assert result["score"] > 0
    assert "question_aliases" in result["matched_fields"]


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
            "title": "充值教程",
            "content": "充值 appears once only.",
            "metadata_json": {"intent_id": "deposit_howto", "is_canonical": True},
            "score": 1,
            "priority": 1,
            "matched_fields": ["content"],
            "matched_terms": ["充值"],
        }
    ])
    service = RagService(repository, min_score=2)

    context = asyncio.run(service.retrieve({"tenant_id": "default", "raw_user_input": "充值"}))

    assert context["matched"] is False
    assert context["answer"] == RAG_FALLBACK_ANSWER
    assert context["answer_blocks"] == [{"type": "text", "text": RAG_FALLBACK_ANSWER}]
    assert context["fallback_reason"] == "low_score"


def test_rag_service_returns_text_block_for_legacy_document():
    repository = FakeKnowledgeRepository([
        {
            "id": 1,
            "title": "Upload screenshot",
            "content": "请上传清晰截图。",
            "metadata_json": {"intent_id": "screenshot_upload_howto", "is_canonical": True},
            "score": 12,
            "priority": 20,
            "matched_fields": ["title"],
            "matched_terms": ["upload screenshot"],
        }
    ])
    service = RagService(repository)

    context = asyncio.run(service.retrieve({"tenant_id": "default", "raw_user_input": "upload screenshot"}))

    assert context["answer_blocks"] == [{"type": "text", "text": "请上传清晰截图。"}]
    assert context["documents"][0]["has_answer_blocks"] is False


def test_answer_from_rag_context_without_context_uses_static_fallback():
    result = answer_from_rag_context({"raw_user_input": "如何充值"})

    assert result["matched"] is True
    assert result["fallback_reason"] is None
