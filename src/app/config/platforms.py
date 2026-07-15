from typing import Any


TEST_PLATFORM = "TEST"
DEFAULT_TELEGRAM_FINANCE_GROUP = "-1003181576378"

OFFICIAL_PLATFORM_CODES = (
    "JUE999",
    "GNA777",
    "JG7",
    "PAG99",
    "CUM777",
    "CON777",
    "ZAP69",
)

LIVECHAT_GROUP_TO_PLATFORM = {
    2: "JUE999",
    12: "GNA777",
    11: "JG7",
    13: "PAG99",
    24: "CUM777",
    25: "CON777",
    28: "ZAP69",
    23: TEST_PLATFORM,
}

PLATFORM_TO_LIVECHAT_GROUP = {
    platform: group_id
    for group_id, platform in LIVECHAT_GROUP_TO_PLATFORM.items()
}

PLATFORM_TOPICS = {
    "JUE999": 4,
    "GNA777": 15372,
    "CUM777": 26447,
    "CON777": 29915,
    "PAG99": 18565,
    "JG7": 15371,
    "ZAP69": 36735,
}

PLATFORM_MERCHANTS = {
    "JUE999": "juecopf1",
    "GNA777": "gnacops1",
    "JG7": "jgcops1",
    "PAG99": "pagcops1",
    "CUM777": "cumcops1",
    "CON777": "concops1",
    "ZAP69": "zapcops1",
    TEST_PLATFORM: "zapcops1",
}


def normalize_platform(platform: Any) -> str:
    return str(platform or "").strip().upper()


def platform_for_livechat_group_id(group_id: Any) -> str | None:
    try:
        key = int(group_id)
    except (TypeError, ValueError):
        return None
    return LIVECHAT_GROUP_TO_PLATFORM.get(key)


def merchant_for_platform(platform: Any) -> str | None:
    return PLATFORM_MERCHANTS.get(normalize_platform(platform))


def merchant_for_livechat_group_id(group_id: Any) -> str | None:
    platform = platform_for_livechat_group_id(group_id)
    return merchant_for_platform(platform) if platform else None


def livechat_group_for_platform(platform: Any) -> int | None:
    return PLATFORM_TO_LIVECHAT_GROUP.get(normalize_platform(platform))


def topic_for_platform(platform: Any) -> int | None:
    code = normalize_platform(platform)
    if code == TEST_PLATFORM:
        return None
    return PLATFORM_TOPICS.get(code)


def default_allowed_livechat_group_ids(include_test: bool = True) -> set[int]:
    groups = {
        group_id
        for group_id, platform in LIVECHAT_GROUP_TO_PLATFORM.items()
        if platform in OFFICIAL_PLATFORM_CODES
    }
    if include_test:
        groups.add(PLATFORM_TO_LIVECHAT_GROUP[TEST_PLATFORM])
    return groups
