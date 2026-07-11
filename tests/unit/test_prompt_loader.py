import pytest


def test_load_prompt_reads_non_empty_utf8_file(tmp_path, monkeypatch):
    from app.prompts import loader

    (tmp_path / "example.md").write_text("客服提示词\n", encoding="utf-8")
    monkeypatch.setattr(loader, "PROMPT_DIR", tmp_path)
    loader.load_prompt.cache_clear()

    assert loader.load_prompt("example.md") == "客服提示词"


def test_load_prompt_rejects_missing_file(tmp_path, monkeypatch):
    from app.prompts import loader

    monkeypatch.setattr(loader, "PROMPT_DIR", tmp_path)
    loader.load_prompt.cache_clear()

    with pytest.raises(FileNotFoundError, match="missing.md"):
        loader.load_prompt("missing.md")


def test_load_prompt_rejects_empty_file(tmp_path, monkeypatch):
    from app.prompts import loader

    (tmp_path / "empty.md").write_text("  \n", encoding="utf-8")
    monkeypatch.setattr(loader, "PROMPT_DIR", tmp_path)
    loader.load_prompt.cache_clear()

    with pytest.raises(ValueError, match="empty.md"):
        loader.load_prompt("empty.md")


def test_render_prompt_requires_exact_placeholders(tmp_path, monkeypatch):
    from app.prompts import loader

    (tmp_path / "router.md").write_text(
        "Allowed intents:\n{allowed_intent_contract}\n\n{faq_knowledge_targets}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "PROMPT_DIR", tmp_path)
    loader.load_prompt.cache_clear()

    assert loader.render_prompt(
        "router.md",
        allowed_intent_contract="deposit_howto",
        faq_knowledge_targets="deposit tutorial",
    ) == "Allowed intents:\ndeposit_howto\n\ndeposit tutorial"

    with pytest.raises(ValueError, match="missing values.*faq_knowledge_targets"):
        loader.render_prompt("router.md", allowed_intent_contract="deposit_howto")

    with pytest.raises(ValueError, match="unexpected values.*extra"):
        loader.render_prompt(
            "router.md",
            allowed_intent_contract="deposit_howto",
            faq_knowledge_targets="deposit tutorial",
            extra="unused",
        )


def test_runtime_prompt_files_are_present_and_non_empty():
    from app.prompts.loader import load_prompt

    prompt_files = {
        "rewrite_system.md",
        "intent_shadow_system.md",
        "intent_router_system.md",
        "sop_slot_extractor_system.md",
        "sop_dialogue_planner_system.md",
        "image_analysis_system.md",
        "final_reply_semantic_constraints.md",
        "final_reply_system.md",
        "text_streaming_output.md",
    }

    for filename in prompt_files:
        assert load_prompt(filename)
