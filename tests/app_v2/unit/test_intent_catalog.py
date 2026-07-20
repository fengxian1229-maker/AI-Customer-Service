from typing import get_args

from app_v2.infrastructure.llm.models import IntentName
from app_v2.runtime.intents import INTENT_HANDLERS


def test_intent_catalog_and_static_handlers_are_complete_and_in_sync():
    assert set(get_args(IntentName)) == set(INTENT_HANDLERS)
    assert INTENT_HANDLERS["deposit_howto"] == "knowledge"
    assert INTENT_HANDLERS["turnover_requirement_query"] == "workflow"
    assert INTENT_HANDLERS["explicit_human_request"] == "handoff"
    assert INTENT_HANDLERS["casual_chat"] == "direct_reply"
    assert INTENT_HANDLERS["clarification_needed"] == "clarification"
