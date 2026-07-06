from __future__ import annotations

import re
from typing import Any


LIVECHAT_BUTTON_SOURCE = "livechat_button"

_MENU_DIRECTIVE_PATTERNS = (
    re.compile(r"請回到選單並選擇[「\"]存款未到[帳账][」\"]。?"),
    re.compile(r"请回到选单并选择[「\"]存款未到[帳账]?[」\"]。?"),
    re.compile(r"請回到選單並選擇[「\"]提款未到[帳账][」\"]。?"),
    re.compile(r"请回到选单并选择[「\"]提款未到[帳账]?[」\"]。?"),
    re.compile(r"如果沒有出現金額欄位或頁面不讓你繼續，請選擇[「\"]無法提款[」\"]。?"),
    re.compile(r"如果没有出现金额栏位或页面不让你继续，请选择[「\"]无法提款[」\"]。?"),
)


def faq_trigger_source(state: dict[str, Any] | None) -> str | None:
    state = state or {}
    intent_result = state.get("intent_result") or {}
    return intent_result.get("faq_trigger_source") or intent_result.get("trigger_source") or state.get("route_source")


def is_livechat_button_faq(state: dict[str, Any] | None) -> bool:
    return faq_trigger_source(state) == LIVECHAT_BUTTON_SOURCE


def prepare_faq_context_for_delivery(rag_context: dict[str, Any] | None, state: dict[str, Any] | None) -> dict[str, Any] | None:
    if rag_context is None or is_livechat_button_faq(state):
        return rag_context

    prepared = dict(rag_context)
    if "answer" in prepared:
        prepared["answer"] = strip_menu_directives(prepared.get("answer"))
    if prepared.get("answer_blocks") is not None:
        prepared["answer_blocks"] = [_prepare_block(block) for block in prepared.get("answer_blocks") or []]
    if prepared.get("documents") is not None:
        prepared["documents"] = [_prepare_document(document) for document in prepared.get("documents") or []]
    return prepared


def strip_menu_directives(text: Any) -> str:
    cleaned = str(text or "")
    for pattern in _MENU_DIRECTIVE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _prepare_block(block: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(block)
    if prepared.get("type") == "text":
        prepared["text"] = strip_menu_directives(prepared.get("text"))
    return prepared


def _prepare_document(document: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(document)
    if "content" in prepared:
        prepared["content"] = strip_menu_directives(prepared.get("content"))
    return prepared
