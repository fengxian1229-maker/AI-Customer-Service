import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace

import pytest


def test_gemini_translator_extracts_only_visible_text_from_response_blocks():
    from app.reporting.daily_chat_report.translation import GeminiTraditionalChineseTranslator

    translator = GeminiTraditionalChineseTranslator(settings=object())
    translator._model = SimpleNamespace(
        invoke=lambda _prompt: SimpleNamespace(
            content=[
                {
                    "type": "text",
                    "text": "繁體結果",
                    "extras": {"signature": "secret"},
                }
            ]
        )
    )

    assert translator.translate("简体输入") == "繁體結果"


@pytest.mark.parametrize(
    "response_content",
    [
        "",
        [],
        {"type": "text", "text": ""},
        [{"type": "text", "text": ""}],
    ],
    ids=["empty-string", "empty-list", "empty-block", "empty-block-list"],
)
def test_gemini_translator_falls_back_to_input_when_response_has_no_visible_text(response_content):
    from app.reporting.daily_chat_report.translation import GeminiTraditionalChineseTranslator

    translator = GeminiTraditionalChineseTranslator(settings=object())
    translator._model = SimpleNamespace(
        invoke=lambda _prompt: SimpleNamespace(content=response_content)
    )

    assert translator.translate("保留原始输入") == "保留原始输入"


def test_format_message_content_extracts_only_visible_text_from_livechat_blocks():
    from app.reporting.daily_chat_report.formatting import format_message_content
    from app.reporting.daily_chat_report.models import ReportMessage
    from app.reporting.daily_chat_report.translation import NullTranslator

    message = ReportMessage(
        id=1,
        chat_id="chat-1",
        thread_id="thread-1",
        sender_role="customer",
        message_type="text",
        text_content="[{'type': 'text', 'text': '你好', 'extras': {'signature': 'xxxxx'}}]",
        attachment_refs=[],
        source="lingxi_dialogue_messages2",
        occurred_at=datetime(2026, 7, 10, 1, 0, 0),
        created_at=datetime(2026, 7, 10, 1, 0, 0),
    )

    rendered = format_message_content(message, NullTranslator())

    assert rendered == "你好"


def test_format_message_content_extracts_visible_text_from_json_blocks():
    from app.reporting.daily_chat_report.formatting import format_message_content
    from app.reporting.daily_chat_report.models import ReportMessage
    from app.reporting.daily_chat_report.translation import NullTranslator

    message = ReportMessage(
        id=1,
        chat_id="chat-1",
        thread_id="thread-1",
        sender_role="customer",
        message_type="text",
        text_content='[{"type":"text","text":"你好","extras":{"signature":"xxxxx","verified":true},"metadata":null}]',
        attachment_refs=[],
        source="lingxi_dialogue_messages2",
        occurred_at=datetime(2026, 7, 10, 1, 0, 0),
        created_at=datetime(2026, 7, 10, 1, 0, 0),
    )

    assert format_message_content(message, NullTranslator()) == "你好"


@pytest.mark.parametrize(
    "text_content",
    [
        [{"type": "text", "text": "你好", "extras": {"signature": "xxxxx"}}],
        {"type": "text", "text": "你好", "metadata": {"private": True}},
    ],
)
def test_format_message_content_accepts_livechat_list_and_dict_values(text_content):
    from app.reporting.daily_chat_report.formatting import format_message_content
    from app.reporting.daily_chat_report.models import ReportMessage
    from app.reporting.daily_chat_report.translation import NullTranslator

    message = ReportMessage(
        id=1,
        chat_id="chat-1",
        thread_id="thread-1",
        sender_role="customer",
        message_type="text",
        text_content=text_content,
        attachment_refs=[],
        source="lingxi_dialogue_messages2",
        occurred_at=datetime(2026, 7, 10, 1, 0, 0),
        created_at=datetime(2026, 7, 10, 1, 0, 0),
    )

    assert format_message_content(message, NullTranslator()) == "你好"


def test_format_message_content_keeps_plain_text_unchanged():
    from app.reporting.daily_chat_report.formatting import format_message_content
    from app.reporting.daily_chat_report.models import ReportMessage
    from app.reporting.daily_chat_report.translation import NullTranslator

    message = ReportMessage(
        id=1,
        chat_id="chat-1",
        thread_id="thread-1",
        sender_role="customer",
        message_type="text",
        text_content="hello world",
        attachment_refs=[],
        source="lingxi_dialogue_messages2",
        occurred_at=datetime(2026, 7, 10, 1, 0, 0),
        created_at=datetime(2026, 7, 10, 1, 0, 0),
    )

    assert format_message_content(message, NullTranslator()) == "hello world"


@pytest.mark.parametrize(
    "text_content",
    [
        '{"foo":"bar"}',
        '{"text":"hello","details":"important"}',
        "[1,2,3]",
        '[{"foo":"bar"},{"text":"hello"}]',
        '[{"type":"text","text":"unfinished"}',
    ],
    ids=["json-dict", "json-text-field", "json-array", "mixed-structure", "malformed-json"],
)
def test_format_message_content_keeps_non_livechat_json_unchanged(text_content):
    from app.reporting.daily_chat_report.formatting import format_message_content
    from app.reporting.daily_chat_report.models import ReportMessage
    from app.reporting.daily_chat_report.translation import NullTranslator

    message = ReportMessage(
        id=1,
        chat_id="chat-1",
        thread_id="thread-1",
        sender_role="customer",
        message_type="text",
        text_content=text_content,
        attachment_refs=[],
        source="lingxi_dialogue_messages2",
        occurred_at=datetime(2026, 7, 10, 1, 0, 0),
        created_at=datetime(2026, 7, 10, 1, 0, 0),
    )

    assert format_message_content(message, NullTranslator()) == text_content


def test_pdf_renderer_fails_loudly_when_no_cjk_font_is_available(tmp_path, monkeypatch, caplog):
    from pathlib import Path

    from reportlab.pdfbase import pdfmetrics

    from app.reporting.daily_chat_report.pdf_renderer import render_daily_chat_report_pdf
    from app.reporting.daily_chat_report.translation import NullTranslator

    original_exists = Path.exists

    def exists_without_system_fonts(path):
        if str(path).endswith((".ttf", ".ttc", ".otf")):
            return False
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", exists_without_system_fonts)
    monkeypatch.setattr(pdfmetrics, "getRegisteredFontNames", lambda: [])

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="CJK font"):
        render_daily_chat_report_pdf(
            [],
            start_at=datetime(2026, 7, 10, 0, 0, 0),
            end_at=datetime(2026, 7, 11, 0, 0, 0),
            output_path=tmp_path / "report.pdf",
            translator=NullTranslator(),
        )

    assert "CJK font" in caplog.text


def test_pdf_message_rows_use_three_aligned_columns():
    from reportlab.platypus import Paragraph, Table

    from app.reporting.daily_chat_report.models import ReportMessage
    from app.reporting.daily_chat_report.pdf_renderer import _message_story, _styles
    from app.reporting.daily_chat_report.translation import NullTranslator

    message = ReportMessage(
        id=1,
        chat_id="chat-1",
        thread_id="thread-1",
        sender_role="customer",
        speaker_name="Cliente",
        message_type="text",
        text_content="hello world",
        attachment_refs=[],
        source="lingxi_dialogue_messages2",
        occurred_at=datetime(2026, 7, 10, 1, 2, 3),
        created_at=datetime(2026, 7, 10, 1, 2, 3),
    )

    flowables = _message_story(message, _styles("Helvetica"), NullTranslator())
    message_tables = [flowable for flowable in flowables if isinstance(flowable, Table)]

    assert len(message_tables) == 1
    assert message_tables[0]._ncols == 3
    assert isinstance(message_tables[0]._cellvalues[0][2], Paragraph)
    assert message_tables[0]._cellvalues[0][2].getPlainText() == "hello world"


def test_aggregate_threads_filters_groups_and_classifies_robot_handoff():
    from app.reporting.daily_chat_report.aggregation import aggregate_threads
    from app.reporting.daily_chat_report.models import ReportCategory

    messages = [
        {
            "id": 1,
            "conversation_id": "livechat:chat-1:thread-1",
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "sender_role": "customer",
            "author_id": "u1",
            "message_type": "text",
            "text_content": "Necesito ayuda",
            "attachment_refs": [],
            "source": "inbound_event",
            "occurred_at": datetime(2026, 6, 19, 1, 0, 0),
            "created_at": datetime(2026, 6, 19, 1, 0, 0),
        },
        {
            "id": 2,
            "conversation_id": "livechat:chat-1:thread-1",
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "sender_role": "assistant",
            "author_id": "bot-1",
            "message_type": "text",
            "text_content": "Transferring to human support.",
            "attachment_refs": [],
            "source": "sender_worker",
            "occurred_at": datetime(2026, 6, 19, 1, 0, 2),
            "created_at": datetime(2026, 6, 19, 1, 0, 2),
        },
        {
            "id": 3,
            "conversation_id": "livechat:chat-2:thread-2",
            "chat_id": "chat-2",
            "thread_id": "thread-2",
            "sender_role": "customer",
            "message_type": "text",
            "text_content": "test group message",
            "attachment_refs": [],
            "source": "inbound_event",
            "occurred_at": datetime(2026, 6, 19, 2, 0, 0),
            "created_at": datetime(2026, 6, 19, 2, 0, 0),
        },
    ]
    metadata = [
        {
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "payload_json": {
                "livechat_group_id": 13,
                "platform": "PAG99",
                "chat_users": [
                    {"id": "u1", "type": "customer", "name": "3016218184"},
                    {"id": "bot-1", "type": "agent", "name": "Ai Jtest"},
                ],
            },
        },
        {
            "chat_id": "chat-2",
            "thread_id": "thread-2",
            "payload_json": {"livechat_group_id": 23, "platform": "TEST"},
        },
    ]
    command_rows = [
        {
            "chat_id": "chat-1",
            "thread_id": "thread-1",
            "command_type": "livechat.handoff_to_human",
            "payload_json": {},
        }
    ]

    threads = aggregate_threads(
        messages,
        metadata_rows=metadata,
        command_rows=command_rows,
        state_rows=[],
        allowed_group_ids={13},
        excluded_group_ids={23},
    )

    assert len(threads) == 1
    assert threads[0].customer_name == "3016218184"
    assert threads[0].group_id == 13
    assert threads[0].platform == "PAG99"
    assert threads[0].category == ReportCategory.ROBOT_HANDOFF
    assert "Ai Jtest" in threads[0].category_reason
    assert threads[0].messages[0].speaker_name == "3016218184"
    assert threads[0].messages[1].speaker_name == "Ai Jtest"


def test_speaker_name_falls_back_to_thread_customer_name_and_plain_bot_name():
    from app.reporting.daily_chat_report.aggregation import aggregate_threads

    threads = aggregate_threads(
        [
            {
                "id": 1,
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "customer",
                "message_type": "text",
                "text_content": "hola",
                "attachment_refs": [],
                "source": "inbound_event",
                "occurred_at": datetime(2026, 6, 19, 1, 0, 0),
                "created_at": datetime(2026, 6, 19, 1, 0, 0),
            },
            {
                "id": 2,
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "assistant",
                "message_type": "text",
                "text_content": "hello",
                "attachment_refs": [],
                "source": "sender_worker",
                "occurred_at": datetime(2026, 6, 19, 1, 0, 1),
                "created_at": datetime(2026, 6, 19, 1, 0, 1),
            },
        ],
        metadata_rows=[
            {
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "payload_json": {"livechat_group_id": 13, "last_thread_summary": {"customer_name": "Cliente"}},
            }
        ],
        command_rows=[],
        state_rows=[],
        allowed_group_ids={13},
        excluded_group_ids=set(),
    )

    assert threads[0].messages[0].speaker_name == "Cliente"
    assert threads[0].messages[1].speaker_name == "Ai Jtest"


def test_classifies_customer_manual_handoff_from_customer_text_without_robot_signal():
    from app.reporting.daily_chat_report.aggregation import aggregate_threads
    from app.reporting.daily_chat_report.models import ReportCategory

    threads = aggregate_threads(
        [
            {
                "id": 1,
                "conversation_id": "livechat:chat-1:thread-1",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "customer",
                "message_type": "text",
                "text_content": "人工客服",
                "attachment_refs": [],
                "source": "inbound_event",
                "occurred_at": datetime(2026, 6, 19, 1, 0, 0),
                "created_at": datetime(2026, 6, 19, 1, 0, 0),
            }
        ],
        metadata_rows=[
            {"chat_id": "chat-1", "thread_id": "thread-1", "payload_json": {"livechat_group_id": 13}}
        ],
        command_rows=[],
        state_rows=[],
        allowed_group_ids={13},
        excluded_group_ids=set(),
    )

    assert threads[0].category == ReportCategory.CUSTOMER_MANUAL_HANDOFF
    assert "主動選擇人工服務" in threads[0].category_reason


def test_lingxi_robot_handoff_reason_uses_lingxi_name():
    from app.reporting.daily_chat_report.aggregation import aggregate_threads
    from app.reporting.daily_chat_report.models import ReportCategory

    threads = aggregate_threads(
        [
            {
                "id": 1,
                "conversation_id": "livechat:chat-1:thread-1",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "customer",
                "message_type": "text",
                "text_content": "Necesito ayuda",
                "attachment_refs": [],
                "source": "lingxi_dialogue_messages2",
                "occurred_at": datetime(2026, 7, 9, 1, 0, 0),
                "created_at": datetime(2026, 7, 9, 1, 0, 0),
            }
        ],
        metadata_rows=[
            {
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "payload_json": {
                    "livechat_group_id": 25,
                    "lingxi_agent_participated": True,
                    "lingxi_agent_names": ["Cess"],
                },
            }
        ],
        command_rows=[
            {
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "command_type": "livechat.handoff_to_human",
                "payload_json": {},
            }
        ],
        state_rows=[],
        allowed_group_ids={25},
        excluded_group_ids=set(),
        require_agent_participation=True,
        bot_name="LingXi",
    )

    assert threads[0].category == ReportCategory.ROBOT_HANDOFF
    assert "LingXi 判定問題需要真人客服" in threads[0].category_reason


def test_lingxi_robot_handoff_can_be_classified_from_transcript_text():
    from app.reporting.daily_chat_report.aggregation import aggregate_threads
    from app.reporting.daily_chat_report.models import ReportCategory

    threads = aggregate_threads(
        [
            {
                "id": 1,
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "agent",
                "speaker_name": "LingXi",
                "message_type": "text",
                "text_content": "I'm transferring you to a live agent.",
                "attachment_refs": [],
                "source": "lingxi_dialogue_messages2",
                "occurred_at": datetime(2026, 7, 9, 1, 0, 0),
                "created_at": datetime(2026, 7, 9, 1, 0, 0),
            },
            {
                "id": 2,
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "agent",
                "speaker_name": "Cess",
                "message_type": "text",
                "text_content": "Hola, soy soporte.",
                "attachment_refs": [],
                "source": "lingxi_dialogue_messages2",
                "occurred_at": datetime(2026, 7, 9, 1, 1, 0),
                "created_at": datetime(2026, 7, 9, 1, 1, 0),
            },
        ],
        metadata_rows=[
            {
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "payload_json": {
                    "livechat_group_id": 25,
                    "lingxi_agent_participated": True,
                    "lingxi_agent_names": ["Cess"],
                },
            }
        ],
        command_rows=[],
        state_rows=[],
        allowed_group_ids={25},
        excluded_group_ids=set(),
        require_agent_participation=True,
        bot_name="LingXi",
    )

    assert threads[0].category == ReportCategory.ROBOT_HANDOFF


def test_lingxi_report_requires_agent_participation_but_keeps_three_category_classification():
    from app.reporting.daily_chat_report.aggregation import aggregate_threads
    from app.reporting.daily_chat_report.formatting import speaker_label
    from app.reporting.daily_chat_report.models import ReportCategory

    threads = aggregate_threads(
        [
            {
                "id": "m1",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "customer",
                "speaker_name": None,
                "message_type": "text",
                "text_content": "hola",
                "attachment_refs": [],
                "source": "lingxi_dialogue_messages2",
                "occurred_at": datetime(2026, 7, 8, 1, 0, 0),
                "created_at": datetime(2026, 7, 8, 1, 0, 0),
            },
            {
                "id": "m2",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "agent",
                "speaker_name": "Cess",
                "message_type": "text",
                "text_content": "hola, soy soporte",
                "attachment_refs": [],
                "source": "lingxi_dialogue_messages2",
                "occurred_at": datetime(2026, 7, 8, 1, 1, 0),
                "created_at": datetime(2026, 7, 8, 1, 1, 0),
            },
            {
                "id": "m3",
                "chat_id": "chat-2",
                "thread_id": "thread-2",
                "sender_role": "customer",
                "speaker_name": None,
                "message_type": "text",
                "text_content": "solo cliente",
                "attachment_refs": [],
                "source": "lingxi_dialogue_messages2",
                "occurred_at": datetime(2026, 7, 8, 2, 0, 0),
                "created_at": datetime(2026, 7, 8, 2, 0, 0),
            },
        ],
        metadata_rows=[
            {
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "payload_json": {
                    "livechat_group_id": 25,
                    "chat_users": [
                        {"id": "Cliente", "type": "customer", "name": "Cliente"},
                        {"id": "Cess", "type": "agent", "name": "Cess"},
                    ],
                    "lingxi_agent_names": ["Cess"],
                    "lingxi_agent_participated": True,
                },
            },
            {
                "chat_id": "chat-2",
                "thread_id": "thread-2",
                "payload_json": {
                    "livechat_group_id": 25,
                    "chat_users": [{"id": "Cliente 2", "type": "customer", "name": "Cliente 2"}],
                    "lingxi_agent_names": [],
                    "lingxi_agent_participated": False,
                },
            },
        ],
        command_rows=[],
        state_rows=[],
        allowed_group_ids={25},
        excluded_group_ids=set(),
        require_agent_participation=True,
    )

    assert len(threads) == 1
    assert threads[0].category == ReportCategory.CUSTOMER_MANUAL_HANDOFF
    assert threads[0].messages[0].speaker_name == "Cliente"
    assert threads[0].messages[1].speaker_name == "Cess"
    assert speaker_label(threads[0].messages[1]) == "真人客服（Cess）"


def test_lingxi_bot_thread_does_not_require_human_agent_participation():
    from app.reporting.daily_chat_report.aggregation import aggregate_threads
    from app.reporting.daily_chat_report.models import ReportCategory

    threads = aggregate_threads(
        [
            {
                "id": "customer-1",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "customer",
                "speaker_name": "Cliente",
                "message_type": "text",
                "text_content": "hola",
                "attachment_refs": [],
                "source": "inbound_event",
                "occurred_at": datetime(2026, 7, 9, 1, 0, 0),
                "created_at": datetime(2026, 7, 9, 1, 0, 0),
            },
            {
                "id": "lingxi-1",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "assistant",
                "author_id": "lingxi@goetm.com",
                "speaker_name": "LingXi",
                "message_type": "text",
                "text_content": "Hola, soy LingXi.",
                "attachment_refs": [],
                "source": "inbound_event_self",
                "occurred_at": datetime(2026, 7, 9, 1, 0, 1),
                "created_at": datetime(2026, 7, 9, 1, 0, 1),
            },
        ],
        metadata_rows=[
            {
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "payload_json": {"livechat_group_id": 25, "chat_users": [{"id": "Cliente", "type": "customer", "name": "Cliente"}]},
            }
        ],
        command_rows=[],
        state_rows=[],
        allowed_group_ids={25},
        excluded_group_ids=set(),
        require_assistant_participation=True,
        bot_name="LingXi",
    )

    assert len(threads) == 1
    assert threads[0].category == ReportCategory.BOT_COMPLETED
    assert threads[0].messages[1].sender_role == "assistant"


def test_lingxi_report_labels_assistant_messages_as_lingxi():
    from app.reporting.daily_chat_report.aggregation import aggregate_threads

    threads = aggregate_threads(
        [
            {
                "id": "lingxi-1",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "assistant",
                "speaker_name": "Ai Jtest",
                "message_type": "text",
                "text_content": "Hola.",
                "attachment_refs": [],
                "source": "sender_worker",
                "occurred_at": datetime(2026, 7, 9, 1, 0, 1),
                "created_at": datetime(2026, 7, 9, 1, 0, 1),
            },
        ],
        metadata_rows=[{"chat_id": "chat-1", "thread_id": "thread-1", "payload_json": {"livechat_group_id": 25}}],
        command_rows=[],
        state_rows=[],
        allowed_group_ids={25},
        excluded_group_ids=set(),
        require_assistant_participation=True,
        bot_name="LingXi",
    )

    assert threads[0].messages[0].speaker_name == "LingXi"


def test_lingxi_report_omits_blank_assistant_messages():
    from app.reporting.daily_chat_report.aggregation import aggregate_threads

    threads = aggregate_threads(
        [
            {
                "id": "customer-1",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "customer",
                "message_type": "text",
                "text_content": "hola",
                "attachment_refs": [],
                "source": "inbound_event",
                "occurred_at": datetime(2026, 7, 9, 1, 0, 0),
                "created_at": datetime(2026, 7, 9, 1, 0, 0),
            },
            {
                "id": "lingxi-blank",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "assistant",
                "message_type": "text",
                "text_content": "   ",
                "attachment_refs": [],
                "source": "inbound_event_self",
                "occurred_at": datetime(2026, 7, 9, 1, 0, 1),
                "created_at": datetime(2026, 7, 9, 1, 0, 1),
            },
            {
                "id": "lingxi-text",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "assistant",
                "message_type": "text",
                "text_content": "Hola, soy LingXi.",
                "attachment_refs": [],
                "source": "inbound_event_self",
                "occurred_at": datetime(2026, 7, 9, 1, 0, 2),
                "created_at": datetime(2026, 7, 9, 1, 0, 2),
            },
        ],
        metadata_rows=[{"chat_id": "chat-1", "thread_id": "thread-1", "payload_json": {"livechat_group_id": 25}}],
        command_rows=[],
        state_rows=[],
        allowed_group_ids={25},
        excluded_group_ids=set(),
        require_assistant_participation=True,
        bot_name="LingXi",
    )

    assert [message.id for message in threads[0].messages] == ["customer-1", "lingxi-text"]


def test_lingxi_report_omits_internal_backend_action_messages():
    from app.reporting.daily_chat_report.aggregation import aggregate_threads

    threads = aggregate_threads(
        [
            {
                "id": "lingxi-1",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "assistant",
                "message_type": "text",
                "text_content": "Hola, soy LingXi.",
                "attachment_refs": [],
                "source": "inbound_event_self",
                "occurred_at": datetime(2026, 7, 9, 1, 0, 0),
                "created_at": datetime(2026, 7, 9, 1, 0, 0),
            },
            {
                "id": "backend-query",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "backend",
                "message_type": "text",
                "text_content": "后台查询成功，已生成可回复摘要",
                "attachment_refs": [],
                "source": "external_result_consumer",
                "occurred_at": datetime(2026, 7, 9, 1, 0, 1),
                "created_at": datetime(2026, 7, 9, 1, 0, 1),
            },
            {
                "id": "telegram-case",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "telegram",
                "message_type": "text",
                "text_content": "案件已建立，case_id=tg:59271",
                "attachment_refs": [],
                "source": "external_result_consumer",
                "occurred_at": datetime(2026, 7, 9, 1, 0, 2),
                "created_at": datetime(2026, 7, 9, 1, 0, 2),
            },
            {
                "id": "system-summary",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "system",
                "message_type": "text",
                "text_content": "Telegram 人工客服回复已润色并准备回写用户。",
                "attachment_refs": [],
                "source": "message_history",
                "occurred_at": datetime(2026, 7, 9, 1, 0, 3),
                "created_at": datetime(2026, 7, 9, 1, 0, 3),
            },
        ],
        metadata_rows=[{"chat_id": "chat-1", "thread_id": "thread-1", "payload_json": {"livechat_group_id": 25}}],
        command_rows=[],
        state_rows=[],
        allowed_group_ids={25},
        excluded_group_ids=set(),
        require_assistant_participation=True,
        bot_name="LingXi",
    )

    assert [message.id for message in threads[0].messages] == ["lingxi-1"]


def test_lingxi_real_agent_signal_classifies_manual_handoff():
    from app.reporting.daily_chat_report.aggregation import aggregate_threads
    from app.reporting.daily_chat_report.models import ReportCategory

    threads = aggregate_threads(
        [
            {
                "id": "lingxi-1",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "assistant",
                "author_id": "lingxi@goetm.com",
                "speaker_name": "LingXi",
                "message_type": "text",
                "text_content": "Hola, soy LingXi.",
                "attachment_refs": [],
                "source": "inbound_event_self",
                "occurred_at": datetime(2026, 7, 9, 1, 0, 1),
                "created_at": datetime(2026, 7, 9, 1, 0, 1),
            },
            {
                "id": "agent-1",
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "sender_role": "agent",
                "author_id": "cess@xyo.email",
                "speaker_name": "Cess",
                "message_type": "text",
                "text_content": "Hola, soy soporte.",
                "attachment_refs": [],
                "source": "inbound_event_agent",
                "occurred_at": datetime(2026, 7, 9, 1, 1, 0),
                "created_at": datetime(2026, 7, 9, 1, 1, 0),
            },
        ],
        metadata_rows=[
            {
                "chat_id": "chat-1",
                "thread_id": "thread-1",
                "payload_json": {
                    "livechat_group_id": 25,
                    "chat_users": [
                        {"id": "lingxi@goetm.com", "type": "agent", "name": "LingXi"},
                        {"id": "cess@xyo.email", "type": "agent", "name": "Cess"},
                    ],
                },
            }
        ],
        command_rows=[],
        state_rows=[],
        allowed_group_ids={25},
        excluded_group_ids=set(),
        require_assistant_participation=True,
        bot_name="LingXi",
    )

    assert len(threads) == 1
    assert threads[0].category == ReportCategory.CUSTOMER_MANUAL_HANDOFF


def test_lingxi_source_uses_livechat_repository():
    from app.reporting.daily_chat_report.repository import LingxiLiveChatApiReportReadRepository
    from app.reporting.daily_chat_report.runner import _build_read_repository

    class Settings:
        livechat_api_base = "https://livechat.example/v3.6"
        livechat_account_id = "account"
        livechat_agent_access_token = "token"
        livechat_agent_email = None
        livechat_self_author_id_set = {"lingxi@goetm.com"}

    repository = _build_read_repository(pool=object(), settings=Settings(), source="lingxi")

    assert isinstance(repository, LingxiLiveChatApiReportReadRepository)


def test_lingxi_agent_messages_after_handoff_attach_to_lingxi_thread():
    from app.reporting.daily_chat_report.repository import _attach_followup_messages_to_lingxi_threads

    rows = [
        {
            "id": "lingxi-1",
            "chat_id": "chat-1",
            "thread_id": "lingxi-thread",
            "sender_role": "assistant",
            "occurred_at": datetime(2026, 7, 9, 1, 0, 0),
            "created_at": datetime(2026, 7, 9, 1, 0, 0),
        },
        {
            "id": "agent-1",
            "chat_id": "chat-1",
            "thread_id": "human-thread",
            "sender_role": "agent",
            "occurred_at": datetime(2026, 7, 9, 1, 1, 0),
            "created_at": datetime(2026, 7, 9, 1, 1, 0),
        },
        {
            "id": "customer-2",
            "chat_id": "chat-1",
            "thread_id": "human-thread",
            "sender_role": "customer",
            "occurred_at": datetime(2026, 7, 9, 1, 2, 0),
            "created_at": datetime(2026, 7, 9, 1, 2, 0),
        },
        {
            "id": "lingxi-2",
            "chat_id": "chat-1",
            "thread_id": "later-lingxi-thread",
            "sender_role": "assistant",
            "occurred_at": datetime(2026, 7, 9, 2, 0, 0),
            "created_at": datetime(2026, 7, 9, 2, 0, 0),
        },
        {
            "id": "staff-reply-1",
            "chat_id": "chat-1",
            "thread_id": "staff-reply-thread",
            "sender_role": "agent",
            "source": "outbound_staff_reply",
            "occurred_at": datetime(2026, 7, 9, 3, 0, 0),
            "created_at": datetime(2026, 7, 9, 3, 0, 0),
        },
    ]

    mapped = _attach_followup_messages_to_lingxi_threads(rows)

    assert mapped[1]["thread_id"] == "lingxi-thread"
    assert mapped[1]["original_thread_id"] == "human-thread"
    assert mapped[2]["thread_id"] == "lingxi-thread"
    assert mapped[2]["original_thread_id"] == "human-thread"
    assert mapped[3]["thread_id"] == "lingxi-thread"
    assert mapped[3]["original_thread_id"] == "later-lingxi-thread"
    assert mapped[4]["thread_id"] == "lingxi-thread"
    assert mapped[4]["original_thread_id"] == "staff-reply-thread"


def test_lingxi_outbound_staff_reply_maps_to_real_agent_message():
    from app.reporting.daily_chat_report.repository import _message_from_outbound_staff_reply

    message = _message_from_outbound_staff_reply(
        {
            "id": 2571,
            "chat_id": "chat-1",
            "thread_id": "human-thread",
            "message_type": "text",
            "payload_json": {"type": "message", "text": "Estamos revisando su caso."},
            "sent_at": datetime(2026, 7, 10, 6, 58, 5),
            "created_at": datetime(2026, 7, 10, 6, 58, 1),
        }
    )

    assert message["sender_role"] == "agent"
    assert message["speaker_name"] == "真人客服"
    assert message["text_content"] == "Estamos revisando su caso."


def test_lingxi_livechat_api_chat_detail_maps_real_agent_messages():
    from app.reporting.daily_chat_report.repository import _message_rows_from_livechat_chat

    rows = _message_rows_from_livechat_chat(
        {
            "id": "TH2I7WH683",
            "access": {"group_ids": [13]},
            "users": [
                {"id": "lingxi@goetm.com", "type": "agent", "name": "Lingxi", "email": "lingxi@goetm.com"},
                {"id": "prez@xyo.email", "type": "agent", "name": "Prez", "email": "prez@xyo.email"},
                {"id": "customer-1", "type": "customer", "name": "Kelly Sarmiento"},
            ],
            "threads": [
                {
                    "id": "TH2V7R801L",
                    "events": [
                        {
                            "id": "e1",
                            "type": "message",
                            "author_id": "lingxi@goetm.com",
                            "text": "Hola.",
                            "created_at": "2026-07-08T23:38:45.047006Z",
                        },
                        {
                            "id": "e2",
                            "type": "message",
                            "author_id": "customer-1",
                            "text": "Buenas tardes",
                            "created_at": "2026-07-08T23:39:00.286000Z",
                        },
                        {
                            "id": "e3",
                            "type": "message",
                            "author_id": "prez@xyo.email",
                            "text": "Thank you for your patience. My name is Prez.",
                            "created_at": "2026-07-08T23:41:00.000000Z",
                        },
                    ],
                }
            ],
        },
        self_author_ids={"lingxi@goetm.com"},
    )

    assert [row["sender_role"] for row in rows] == ["assistant", "customer", "agent"]
    assert rows[2]["speaker_name"] == "Prez"
    assert rows[2]["text_content"] == "Thank you for your patience. My name is Prez."


def test_speaker_label_does_not_duplicate_real_agent_label():
    from app.reporting.daily_chat_report.formatting import speaker_label
    from app.reporting.daily_chat_report.models import ReportMessage

    message = ReportMessage(
        id=1,
        chat_id="chat-1",
        thread_id="thread-1",
        sender_role="agent",
        speaker_name="真人客服",
        message_type="text",
        text_content="Estamos revisando su caso.",
        attachment_refs=[],
        source="outbound_staff_reply",
        occurred_at=datetime(2026, 7, 10, 6, 58, 5),
        created_at=datetime(2026, 7, 10, 6, 58, 1),
    )

    assert speaker_label(message) == "真人客服"


def test_translation_preserves_urls_account_like_values_and_attachment_formatting():
    from app.reporting.daily_chat_report.translation import NullTranslator
    from app.reporting.daily_chat_report.formatting import format_message_content
    from app.reporting.daily_chat_report.models import ReportMessage

    message = ReportMessage(
        id=1,
        chat_id="chat-1",
        thread_id="thread-1",
        sender_role="customer",
        message_type="file",
        text_content="My user is 3016218184 https://example.test/pay",
        attachment_refs=[{"filename": "reply.jpg", "url": "https://cdn.example/reply.jpg"}],
        source="inbound_event",
        occurred_at=datetime(2026, 6, 19, 1, 0, 0),
        created_at=datetime(2026, 6, 19, 1, 0, 0),
    )

    rendered = format_message_content(message, translator=NullTranslator())

    assert "3016218184" in rendered
    assert "https://example.test/pay" not in rendered
    assert "[URL]" in rendered
    assert "[圖片] reply.jpg [URL]" in rendered


def test_pdf_renderer_outputs_expected_report_text(tmp_path):
    from pypdf import PdfReader

    from app.reporting.daily_chat_report.models import ReportCategory, ReportMessage, ReportThread
    from app.reporting.daily_chat_report.pdf_renderer import render_daily_chat_report_pdf
    from app.reporting.daily_chat_report.translation import NullTranslator

    output = tmp_path / "report.pdf"
    thread = ReportThread(
        chat_id="chat-1",
        thread_id="thread-1",
        customer_name="Cliente",
        group_id=13,
        platform="PAG99",
        start_at=datetime(2026, 6, 19, 1, 0, 0),
        end_at=datetime(2026, 6, 19, 1, 1, 0),
        category=ReportCategory.BOT_COMPLETED,
        category_reason="未由真人接管。",
        messages=[
            ReportMessage(
                id=1,
                chat_id="chat-1",
                thread_id="thread-1",
                sender_role="customer",
                message_type="text",
                text_content="hello",
                attachment_refs=[],
                source="inbound_event",
                occurred_at=datetime(2026, 6, 19, 1, 0, 0),
                created_at=datetime(2026, 6, 19, 1, 0, 0),
            )
        ],
    )

    render_daily_chat_report_pdf(
        [thread],
        start_at=datetime(2026, 6, 19, 0, 0, 0),
        end_at=datetime(2026, 6, 20, 0, 0, 0),
        output_path=output,
        translator=NullTranslator(),
    )

    reader = PdfReader(str(output))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    fonts = [
        font.get_object()
        for page in reader.pages
        for font in (page["/Resources"].get("/Font") or {}).values()
    ]
    assert "LingXi 正式群組對話紀錄" in text
    assert "LingXi 實際有發出訊息的 thread" in text
    assert "分類定義" in text
    assert "統計" in text
    assert "機器人獨立完成" in text
    assert "Chat ID：chat-1" in text
    assert "■" not in text
    assert any(font.get("/FontDescriptor") or font.get("/DescendantFonts") for font in fonts)


def test_lingxi_pdf_renderer_outputs_three_category_sections(tmp_path):
    from pypdf import PdfReader

    from app.reporting.daily_chat_report.models import ReportCategory, ReportMessage, ReportThread
    from app.reporting.daily_chat_report.pdf_renderer import render_daily_chat_report_pdf
    from app.reporting.daily_chat_report.translation import NullTranslator

    output = tmp_path / "lingxi-report.pdf"
    thread = ReportThread(
        chat_id="chat-1",
        thread_id="thread-1",
        customer_name="Cliente",
        group_id=25,
        platform="CON777",
        start_at=datetime(2026, 7, 9, 1, 0, 0),
        end_at=datetime(2026, 7, 9, 1, 1, 0),
        category=ReportCategory.CUSTOMER_MANUAL_HANDOFF,
        category_reason="真人客服已參與對話，且未發現機器人判定轉接訊號。",
        messages=[
            ReportMessage(
                id=1,
                chat_id="chat-1",
                thread_id="thread-1",
                sender_role="agent",
                speaker_name="Cess",
                message_type="text",
                text_content="hello",
                attachment_refs=[],
                source="lingxi_dialogue_messages2",
                occurred_at=datetime(2026, 7, 9, 1, 0, 0),
                created_at=datetime(2026, 7, 9, 1, 0, 0),
            )
        ],
    )

    render_daily_chat_report_pdf(
        [thread],
        start_at=datetime(2026, 7, 9, 0, 0, 0),
        end_at=datetime(2026, 7, 10, 0, 0, 0),
        output_path=output,
        translator=NullTranslator(),
    )

    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(output)).pages)
    assert "LingXi 正式群組對話紀錄（繁體中文，三分類）" in text
    assert "本版只保留三類：機器人獨立完成、機器人判定轉真人、客戶手動轉真人。" in text
    assert "LingXi客服參與" not in text
    assert "機器人獨立完成（0 筆）" in text
    assert "機器人判定轉真人（0 筆）" in text
    assert "客戶手動轉真人（1 筆）" in text


def test_pdf_renderer_paginates_very_long_message_content(tmp_path):
    from pypdf import PdfReader

    from app.reporting.daily_chat_report.models import ReportCategory, ReportMessage, ReportThread
    from app.reporting.daily_chat_report.pdf_renderer import render_daily_chat_report_pdf
    from app.reporting.daily_chat_report.translation import NullTranslator

    output = tmp_path / "long-message-report.pdf"
    long_text = "\n".join(f"line {index} 這是一段很長的客服對話內容" for index in range(260))
    thread = ReportThread(
        chat_id="chat-long",
        thread_id="thread-long",
        customer_name="Cliente Largo",
        group_id=28,
        platform="ZAP69",
        start_at=datetime(2026, 7, 10, 1, 0, 0),
        end_at=datetime(2026, 7, 10, 1, 30, 0),
        category=ReportCategory.CUSTOMER_MANUAL_HANDOFF,
        category_reason="真人客服已參與對話。",
        messages=[
            ReportMessage(
                id=1,
                chat_id="chat-long",
                thread_id="thread-long",
                sender_role="agent",
                speaker_name="Prez",
                message_type="text",
                text_content=long_text,
                attachment_refs=[],
                source="livechat_archive",
                occurred_at=datetime(2026, 7, 10, 1, 0, 0),
                created_at=datetime(2026, 7, 10, 1, 0, 0),
            )
        ],
    )

    render_daily_chat_report_pdf(
        [thread],
        start_at=datetime(2026, 7, 10, 0, 0, 0),
        end_at=datetime(2026, 7, 11, 0, 0, 0),
        output_path=output,
        translator=NullTranslator(),
    )

    reader = PdfReader(str(output))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert len(reader.pages) > 1
    assert "Chat ID：chat-long" in text
    assert "真人客服（Prez）" in text
    assert "line 259" in text


def test_audit_repository_prevents_duplicate_send():
    from app.reporting.daily_chat_report.repository import DailyChatReportAuditRepository, _thread_key

    class Cursor:
        rowcount = 0
        lastrowid = None

        async def execute(self, sql, args):
            self.sql = sql
            self.args = args

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Connection:
        def __init__(self):
            self.cursor_obj = Cursor()

        def cursor(self, *args, **kwargs):
            return self.cursor_obj

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Pool:
        def __init__(self):
            self.connection = Connection()

        def acquire(self):
            return self.connection

    result = asyncio.run(
        DailyChatReportAuditRepository(Pool()).start_once(
            report_date="2026-06-19",
            target_chat_id="-1001",
            message_thread_id=None,
            pdf_path="/tmp/report.pdf",
        )
    )

    assert result["started"] is False
    assert result["duplicate"] is True
    assert result["id"] is None
    assert _thread_key(None) == 0
    assert _thread_key(123) == 123


def test_date_windows_query_utc_but_display_report_timezone():
    from datetime import date

    from app.reporting.daily_chat_report.runner import _date_windows

    windows = _date_windows(date(2026, 6, 19), "Asia/Shanghai")

    assert windows["display_start_at"].strftime("%Y-%m-%d %H:%M:%S") == "2026-06-19 00:00:00"
    assert windows["display_end_at"].strftime("%Y-%m-%d %H:%M:%S") == "2026-06-20 00:00:00"
    assert windows["query_start_at"].strftime("%Y-%m-%d %H:%M:%S") == "2026-06-18 16:00:00"
    assert windows["query_end_at"].strftime("%Y-%m-%d %H:%M:%S") == "2026-06-19 16:00:00"


def test_report_filename_uses_lingxi_date_range():
    from datetime import date

    from app.reporting.daily_chat_report.runner import _date_windows, _report_filename

    windows = _date_windows(date(2026, 7, 9), "Asia/Shanghai")

    assert _report_filename(date(2026, 7, 9), windows["display_start_at"], windows["display_end_at"]) == "LingXi_正式群組對話紀錄_20260709-20260710.pdf"


def test_threads_for_display_timezone_converts_message_times_from_utc():
    from app.reporting.daily_chat_report.models import ReportCategory, ReportMessage, ReportThread
    from app.reporting.daily_chat_report.runner import _threads_for_display_timezone

    thread = ReportThread(
        chat_id="chat-1",
        thread_id="thread-1",
        customer_name="Cliente",
        group_id=13,
        platform="PAG99",
        start_at=datetime(2026, 6, 18, 16, 0, 0),
        end_at=datetime(2026, 6, 18, 16, 1, 0),
        category=ReportCategory.BOT_COMPLETED,
        category_reason="未由真人接管。",
        messages=[
            ReportMessage(
                id=1,
                chat_id="chat-1",
                thread_id="thread-1",
                sender_role="customer",
                message_type="text",
                text_content="hello",
                attachment_refs=[],
                source="inbound_event",
                occurred_at=datetime(2026, 6, 18, 16, 0, 0),
                created_at=datetime(2026, 6, 18, 16, 0, 0),
            )
        ],
    )

    converted = _threads_for_display_timezone([thread], "Asia/Shanghai")

    assert converted[0].start_at.strftime("%Y-%m-%d %H:%M:%S") == "2026-06-19 00:00:00"
    assert converted[0].messages[0].occurred_at.strftime("%Y-%m-%d %H:%M:%S") == "2026-06-19 00:00:00"
