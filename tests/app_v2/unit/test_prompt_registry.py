import pytest

from app_v2.prompts.registry import PromptRegistry


@pytest.mark.parametrize(
    "prompt_name",
    [
        "normalize_turn",
        "intent_classification",
        "workflow_interpretation",
        "compose_reply",
        "multimodal_analysis",
    ],
)
def test_prompt_registry_loads_versioned_nonempty_prompt(prompt_name):
    prompt = PromptRegistry().load(prompt_name)

    assert prompt.name == prompt_name
    assert prompt.version == "v1"
    assert len(prompt.text) >= 100


def test_prompt_registry_rejects_unregistered_prompt():
    with pytest.raises(KeyError):
        PromptRegistry().load("unknown_prompt")


def test_compose_prompt_does_not_assign_conversation_close_to_ai():
    prompt = PromptRegistry().load("compose_reply")

    assert "close status" not in prompt.text.lower()
    assert "closing message" not in prompt.text.lower()
