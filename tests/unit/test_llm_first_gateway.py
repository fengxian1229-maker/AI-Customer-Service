import asyncio

from app.schemas.events import InboundEvent
from app.services.llm_first_gateway import LLMFirstGatewayService


class FakeRepo:
    async def get_or_create(self, *a, **k):
        return {
            "conversation_id": "livechat:chat-1",
            "status": "AI_ACTIVE",
            "active_workflow": None,
            "workflow_stage": None,
        }


class FakeOut:
    def __init__(self):
        self.inserted = []
    async def insert(self, m):
        self.inserted.append(m)


class FakeInbound:
    async def mark_processed(self, id):
        pass


class FakeGraph:
    def invoke(self, state, config):
        return {**state, "route": state.get("route", "faq"), "response_text": "ok"}


class FakeLLM:
    async def route(self, payload):
        text = payload.get("raw_user_input") or ""
        if "human" in text:
            return {
                "rewritten_question": text,
                "normalized_query": text,
                "language": "en",
                "intent": "explicit_human_request",
                "route": "human_handoff",
                "confidence": 0.9,
                "reason": "human",
                "requires_human": True,
                "requires_backend": False,
                "preserved_entities": [],
            }
        return {
            "rewritten_question": "how to deposit",
            "normalized_query": "how to deposit",
            "language": "en",
            "intent": "deposit_howto",
            "route": "faq",
            "confidence": 0.95,
            "reason": "ok",
            "requires_human": False,
            "requires_backend": False,
            "preserved_entities": [],
        }


async def run_case(text):
    svc = LLMFirstGatewayService(
        inbound_repository=FakeInbound(),
        conversation_repository=FakeRepo(),
        outbound_repository=FakeOut(),
        workflow_graph=FakeGraph(),
        llm_intent_service=FakeLLM(),
        llm_router_mode="guarded_authoritative",
    )

    event = InboundEvent(
        source="t",
        raw_action="t",
        chat_id="c",
        thread_id="t",
        event_id="e",
        event_type="message",
        standard_event_type="MESSAGE_CREATED",
        author_id="u",
        sender_role="external",
        occurred_at="2026-06-01",
        dedup_key="k",
        payload_json={"event": {"text": text}},
        ignored=False,
    )
    return await svc.process_event(1, event)


async def run_tests():
    r1 = await run_case("how to deposit")
    assert r1["graph_state"]["route"] == "faq"

    r2 = await run_case("I want human agent")
    assert r2["graph_state"]["route"] == "human_handoff"


def test_llm_first_gateway_routes_with_guarded_authoritative_router():
    asyncio.run(run_tests())


if __name__ == "__main__":
    asyncio.run(run_tests())
