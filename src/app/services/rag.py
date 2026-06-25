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
    def __init__(self, knowledge_repository=None, max_docs: int = 3) -> None:
        self.knowledge_repository = knowledge_repository
        self.max_docs = max_docs

    async def answer(self, state: dict) -> dict:
        query = normalize_text(state.get("rewritten_question") or state.get("raw_user_input"))
        if _is_backend_fact_question(query):
            return {
                "matched": False,
                "answer": BACKEND_FACT_FALLBACK_ANSWER,
                "documents": [],
                "fallback_reason": "backend_fact",
            }

        if self.knowledge_repository:
            documents = await self.knowledge_repository.search(
                tenant_id=state.get("tenant_id") or "default",
                query=query,
                kb_scope=state.get("kb_scope") or "default",
                limit=self.max_docs,
            )
        else:
            documents = search_static_knowledge(
                tenant_id=state.get("tenant_id") or "default",
                query=query,
                limit=self.max_docs,
            )

        if not documents:
            return {
                "matched": False,
                "answer": RAG_FALLBACK_ANSWER,
                "documents": [],
                "fallback_reason": "no_match",
            }

        best = documents[0]
        return {
            "matched": True,
            "answer": best["content"],
            "documents": [
                {"id": doc["id"], "title": doc["title"], "score": doc["score"]}
                for doc in documents
            ],
            "fallback_reason": None,
        }


def answer_from_static_knowledge(state: dict) -> dict:
    query = normalize_text(state.get("rewritten_question") or state.get("raw_user_input"))
    if _is_backend_fact_question(query):
        return {
            "matched": False,
            "answer": BACKEND_FACT_FALLBACK_ANSWER,
            "documents": [],
            "fallback_reason": "backend_fact",
        }
    documents = search_static_knowledge(
        tenant_id=state.get("tenant_id") or "default",
        query=query,
        limit=3,
    )
    if not documents:
        return {
            "matched": False,
            "answer": RAG_FALLBACK_ANSWER,
            "documents": [],
            "fallback_reason": "no_match",
        }
    best = documents[0]
    return {
        "matched": True,
        "answer": best["content"],
        "documents": [{"id": doc["id"], "title": doc["title"], "score": doc["score"]} for doc in documents],
        "fallback_reason": None,
    }


def search_static_knowledge(tenant_id: str, query: str, limit: int = 3) -> list[dict]:
    scored = []
    for doc in DEFAULT_KNOWLEDGE_DOCUMENTS:
        if doc.get("tenant_id") not in {tenant_id, "default"}:
            continue
        score = score_knowledge_document(doc, query)
        if score > 0:
            scored.append({**doc, "score": score})
    scored.sort(key=lambda item: (-item["score"], item.get("priority", 100), item["id"]))
    return scored[:limit]


def score_knowledge_document(document: dict, query: str) -> int:
    tokens = _query_tokens(query)
    if not tokens:
        return 0
    title = normalize_text(document.get("title")).lower()
    content = normalize_text(document.get("content")).lower()
    keywords = [normalize_text(keyword).lower() for keyword in document.get("keywords") or []]
    score = 0
    for token in tokens:
        if token in title:
            score += 3
        if any(token in keyword for keyword in keywords):
            score += 2
        if token in content:
            score += 1
    lowered_query = query.lower()
    if any(keyword and keyword in lowered_query for keyword in keywords):
        score += 3
    return score


def _query_tokens(query: str) -> list[str]:
    normalized = normalize_text(query).lower()
    if not normalized:
        return []
    tokens = [token.strip(" ?!,.，。¿¡") for token in normalized.split()]
    if any("\u4e00" <= char <= "\u9fff" for char in normalized):
        tokens.append(normalized)
    return [token for token in tokens if token]


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
