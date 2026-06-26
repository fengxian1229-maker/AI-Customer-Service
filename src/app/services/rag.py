from app.workflows.slot_extractors import normalize_text


RAG_FALLBACK_ANSWER = "我暂时没有在知识库中找到对应答案。请补充更具体的问题，或我可以为你转接真人客服。"
BACKEND_FACT_FALLBACK_ANSWER = "这个问题需要查询账户或订单状态，我不能只根据知识库判断。请补充资料，或我可以为你转接真人客服。"


DEFAULT_KNOWLEDGE_DOCUMENTS = [
    {
        "id": 1,
        "tenant_id": "default",
        "kb_scope": "default",
        "title": "充值方式说明",
        "content": "你可以在充值页面选择可用通道并按页面提示完成充值。实际到账状态需要后台或人工确认。",
        "keywords": ["how to deposit", "deposit", "recargar", "cómo recargar", "como recargar", "如何充值", "充值"],
        "language": "multi",
        "priority": 10,
    },
    {
        "id": 2,
        "tenant_id": "default",
        "kb_scope": "default",
        "title": "奖金规则说明",
        "content": "奖金规则以活动页面说明为准，请确认活动条件、有效投注要求和领取期限。",
        "keywords": ["bonus", "bonus rules", "promotion", "bono", "奖金", "活动规则"],
        "language": "multi",
        "priority": 20,
    },
]


class RagService:
    def __init__(self, knowledge_repository=None, max_docs: int = 3, min_score: int = 2) -> None:
        self.knowledge_repository = knowledge_repository
        self.max_docs = max_docs
        self.min_score = min_score

    async def retrieve(self, state: dict) -> dict:
        tenant_id = state.get("tenant_id") or "default"
        kb_scope = state.get("kb_scope") or "default"
        query = normalize_text(state.get("rewritten_question") or state.get("raw_user_input"))
        language = ((state.get("rewrite_result") or {}).get("language")) or None

        if _is_backend_fact_question(query):
            return _fallback_context(
                answer=BACKEND_FACT_FALLBACK_ANSWER,
                fallback_reason="backend_fact",
                source="guardrail",
                query=query,
                tenant_id=tenant_id,
                kb_scope=kb_scope,
            )

        if not query:
            return _fallback_context(
                answer=RAG_FALLBACK_ANSWER,
                fallback_reason="empty_query",
                source="knowledge_documents" if self.knowledge_repository else "static_knowledge",
                query=query,
                tenant_id=tenant_id,
                kb_scope=kb_scope,
            )

        if self.knowledge_repository:
            documents = await self.knowledge_repository.search(
                tenant_id=tenant_id,
                query=query,
                kb_scope=kb_scope,
                limit=self.max_docs,
            )
            source = "knowledge_documents"
        else:
            documents = search_static_knowledge(
                tenant_id=tenant_id,
                query=query,
                kb_scope=kb_scope,
                limit=self.max_docs,
                language=language,
            )
            source = "static_knowledge"

        if not documents:
            return _fallback_context(
                answer=RAG_FALLBACK_ANSWER,
                fallback_reason="no_match",
                source=source,
                query=query,
                tenant_id=tenant_id,
                kb_scope=kb_scope,
            )

        best = documents[0]
        if best.get("score", 0) < self.min_score:
            return _fallback_context(
                answer=RAG_FALLBACK_ANSWER,
                fallback_reason="low_score",
                source=source,
                query=query,
                tenant_id=tenant_id,
                kb_scope=kb_scope,
            )

        return {
            "matched": True,
            "answer": best.get("content") or RAG_FALLBACK_ANSWER,
            "documents": [_rag_document_payload(document) for document in documents],
            "fallback_reason": None,
            "source": source,
            "query": query,
            "tenant_id": tenant_id,
            "kb_scope": kb_scope,
        }

    async def answer(self, state: dict) -> dict:
        context = await self.retrieve(state)
        return answer_from_rag_context({**state, "rag_context": context})


def answer_from_static_knowledge(state: dict) -> dict:
    query = normalize_text(state.get("rewritten_question") or state.get("raw_user_input"))
    if _is_backend_fact_question(query):
        return _fallback_answer("backend_fact", BACKEND_FACT_FALLBACK_ANSWER)
    if not query:
        return _fallback_answer("empty_query", RAG_FALLBACK_ANSWER)
    documents = search_static_knowledge(
        tenant_id=state.get("tenant_id") or "default",
        query=query,
        kb_scope=state.get("kb_scope") or "default",
        limit=3,
        language=((state.get("rewrite_result") or {}).get("language")) or None,
    )
    if not documents:
        return _fallback_answer("no_match", RAG_FALLBACK_ANSWER)
    return _build_answer_from_context(
        {
            "matched": True,
            "answer": documents[0]["content"],
            "documents": [_rag_document_payload(document) for document in documents],
            "fallback_reason": None,
        }
    )


def answer_from_rag_context(state: dict) -> dict:
    context = state.get("rag_context")
    if context is None:
        return answer_from_static_knowledge(state)
    return _build_answer_from_context(context)


def search_static_knowledge(
    tenant_id: str,
    query: str,
    kb_scope: str = "default",
    limit: int = 3,
    language: str | None = None,
) -> list[dict]:
    scored = []
    for document in DEFAULT_KNOWLEDGE_DOCUMENTS:
        if document.get("tenant_id") not in {tenant_id, "default"}:
            continue
        if document.get("kb_scope", "default") != kb_scope:
            continue
        ranked = rank_knowledge_document(document, query, language=language)
        if ranked["score"] > 0:
            scored.append({**document, **ranked})
    scored.sort(key=lambda item: (-item["score"], item.get("priority", 100), item.get("id", 0)))
    return scored[:limit]


def rank_knowledge_document(document: dict, query: str, language: str | None = None) -> dict:
    normalized_query = normalize_text(query).lower()
    tokens = _query_tokens(query)
    if not normalized_query or not tokens:
        return {
            "score": 0,
            "matched_fields": [],
            "matched_terms": [],
            "reason": "empty_query",
        }

    title = normalize_text(document.get("title")).lower()
    content = normalize_text(document.get("content")).lower()
    keywords = [normalize_text(keyword).lower() for keyword in document.get("keywords") or []]

    score = 0
    matched_fields: list[str] = []
    matched_terms: list[str] = []
    reasons: list[str] = []

    if normalized_query and normalized_query in title:
        score += 8
        matched_fields.append("title")
        matched_terms.append(normalized_query)
        reasons.append("exact_title_match")

    for keyword in keywords:
        if keyword and normalized_query == keyword:
            score += 7
            matched_fields.append("keywords")
            matched_terms.append(keyword)
            reasons.append("exact_keyword_match")
            break

    for token in tokens:
        if token in title:
            score += 4
            matched_fields.append("title")
            matched_terms.append(token)
        if any(token in keyword for keyword in keywords):
            score += 3
            matched_fields.append("keywords")
            matched_terms.append(token)
        if token in content:
            score += 1
            matched_fields.append("content")
            matched_terms.append(token)

    doc_language = (document.get("language") or "").lower()
    normalized_language = (language or "").lower()
    if normalized_language and doc_language and normalized_language in doc_language and score > 0:
        score += 1
        reasons.append("language_match")

    return {
        "score": score,
        "matched_fields": _unique(matched_fields),
        "matched_terms": _unique(matched_terms),
        "reason": reasons[0] if reasons else "token_match" if score > 0 else "no_match",
    }


def score_knowledge_document(document: dict, query: str) -> int:
    return rank_knowledge_document(document, query).get("score", 0)


def _fallback_context(
    answer: str,
    fallback_reason: str,
    source: str,
    query: str,
    tenant_id: str,
    kb_scope: str,
) -> dict:
    return {
        "matched": False,
        "answer": answer,
        "documents": [],
        "fallback_reason": fallback_reason,
        "source": source,
        "query": query,
        "tenant_id": tenant_id,
        "kb_scope": kb_scope,
    }


def _fallback_answer(reason: str, answer: str) -> dict:
    return {
        "matched": False,
        "answer": answer,
        "documents": [],
        "fallback_reason": reason,
    }


def _build_answer_from_context(context: dict) -> dict:
    fallback_reason = context.get("fallback_reason")
    if fallback_reason == "backend_fact":
        return _fallback_answer("backend_fact", BACKEND_FACT_FALLBACK_ANSWER)
    if fallback_reason == "empty_query":
        return _fallback_answer("empty_query", RAG_FALLBACK_ANSWER)

    answer = context.get("answer")
    documents = context.get("documents") or []
    if answer and documents:
        return {
            "matched": True,
            "answer": answer,
            "documents": [_rag_document_summary(document) for document in documents],
            "fallback_reason": None,
        }

    if documents:
        return {
            "matched": True,
            "answer": documents[0].get("content") or RAG_FALLBACK_ANSWER,
            "documents": [_rag_document_summary(document) for document in documents],
            "fallback_reason": None,
        }

    return _fallback_answer(fallback_reason or "no_match", RAG_FALLBACK_ANSWER)


def _rag_document_payload(document: dict) -> dict:
    return {
        "id": document.get("id"),
        "title": document.get("title"),
        "score": document.get("score", 0),
        "priority": document.get("priority", 100),
        "matched_fields": list(document.get("matched_fields") or []),
        "matched_terms": list(document.get("matched_terms") or []),
        "content": document.get("content") or "",
    }


def _rag_document_summary(document: dict) -> dict:
    return {
        "id": document.get("id"),
        "title": document.get("title"),
        "score": document.get("score", 0),
        "priority": document.get("priority", 100),
        "matched_fields": list(document.get("matched_fields") or []),
        "matched_terms": list(document.get("matched_terms") or []),
    }


def _query_tokens(query: str) -> list[str]:
    normalized = normalize_text(query).lower()
    if not normalized:
        return []
    tokens = [token.strip(" ?!,.，。¿¡") for token in normalized.split()]
    if any("\u4e00" <= char <= "\u9fff" for char in normalized):
        tokens.extend(_zh_substrings(normalized))
    return [token for token in _unique(tokens + [normalized]) if token]


def _zh_substrings(text: str) -> list[str]:
    compact = text.replace(" ", "")
    if len(compact) < 2:
        return [compact]
    return [compact[index:index + 2] for index in range(len(compact) - 1)] + [compact]


def _is_backend_fact_question(query: str) -> bool:
    lowered = normalize_text(query).lower()
    fact_markers = (
        "did not arrive",
        "no llegó",
        "no llego",
        "not arrived",
        "withdrawal status",
        "deposit status",
        "order status",
        "balance",
        "流水",
        "余额",
        "未到账",
        "没到账",
        "提款状态",
        "订单",
    )
    return any(marker in lowered for marker in fact_markers)


def _unique(values: list[str]) -> list[str]:
    seen = set()
    ordered = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
