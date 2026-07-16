import pytest

from app.config.platforms import (
    merchant_for_livechat_group_id,
    merchant_for_platform,
    normalize_livechat_group_id,
    platform_for_livechat_group_id,
    topic_for_platform,
)
from app.core.settings import Settings
from app.services.telegram_target_resolver import resolve_telegram_target


def make_settings(**overrides):
    values = {
        "livechat_agent_access_token": "token",
        "livechat_account_id": "account",
        "livechat_allowed_group_ids": "",
        "telegram_bot_token": "secret",
        "telegram_sop_enabled": True,
        "telegram_force_no_topic": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_official_platform_group_and_topic_mapping():
    assert platform_for_livechat_group_id(28) == "ZAP69"
    assert topic_for_platform("ZAP69") == 36735
    assert platform_for_livechat_group_id(23) == "TEST"
    assert topic_for_platform("TEST") is None


def test_livechat_groups_map_to_expected_tac_merchants():
    assert {
        group_id: merchant_for_livechat_group_id(group_id)
        for group_id in (2, 11, 12, 13, 23, 24, 25, 28)
    } == {
        2: "juecopf1",
        11: "jgcops1",
        12: "gnacops1",
        13: "pagcops1",
        23: "zapcops1",
        24: "cumcops1",
        25: "concops1",
        28: "zapcops1",
    }


def test_merchant_lookup_normalizes_platform_and_rejects_unknown_values():
    assert merchant_for_platform(" pag99 ") == "pagcops1"
    assert merchant_for_platform("unknown") is None
    assert merchant_for_livechat_group_id(999) is None
    assert merchant_for_livechat_group_id("invalid") is None


@pytest.mark.parametrize("value", [13, "13", " 13 "])
def test_normalize_livechat_group_id_accepts_positive_integers(value):
    assert normalize_livechat_group_id(value) == 13


@pytest.mark.parametrize(
    "value",
    [None, "", " ", True, False, 13.0, 13.5, 0, -13, "+13", "-13", "13.5", "bad"],
)
def test_normalize_livechat_group_id_rejects_non_integer_values(value):
    assert normalize_livechat_group_id(value) is None
    assert platform_for_livechat_group_id(value) is None
    assert merchant_for_livechat_group_id(value) is None


def test_settings_default_allowed_livechat_groups_include_official_and_test():
    settings = make_settings()

    assert settings.livechat_allowed_group_id_set == {2, 11, 12, 13, 23, 24, 25, 28}


def test_resolve_telegram_target_uses_platform_finance_topic():
    target = resolve_telegram_target(
        {"payload_json": {"platform": "JG7", "slot_memory": {"account_or_phone": "andy"}}},
        make_settings(),
    )

    assert target == {
        "chat_id": "-1003181576378",
        "message_thread_id": 15371,
        "target_source": "platform_finance_topic",
    }


def test_resolve_telegram_target_uses_test_group_for_test_platform():
    target = resolve_telegram_target(
        {"payload_json": {"platform": "TEST", "slot_memory": {"account_or_phone": "andy"}}},
        make_settings(telegram_test_group="-100test"),
    )

    assert target == {
        "chat_id": "-100test",
        "message_thread_id": None,
        "target_source": "platform_test_group",
    }
