import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class StaffReplyResult:
    type: str
    text: str
    source: str


class StaffReplyProcessor:
    def __init__(self, model_rewriter: Callable[[str, str], dict | str] | None = None, enabled: bool = True) -> None:
        self.model_rewriter = model_rewriter
        self.enabled = enabled

    def process(self, text: str, target_lang: str = "zh") -> StaffReplyResult:
        raw = _compact(text)
        if not raw:
            return StaffReplyResult(type="long_wait", text=staff_reply_passthrough_fallback(raw, target_lang), source="fallback_empty")
        if self.enabled and self.model_rewriter is not None:
            try:
                candidate = self.model_rewriter(raw, target_lang)
                parsed = _parse_candidate(candidate)
                polished = _compact(parsed.get("text"))
                reply_type = parsed.get("type") or classify_staff_reply(raw)
                if polished and validate_staff_reply_facts(raw, polished)["ok"] and not has_untranslated_internal_english(polished, target_lang):
                    return StaffReplyResult(type=reply_type, text=polished, source="model")
            except Exception:
                pass
        return StaffReplyResult(type=classify_staff_reply(raw), text=staff_reply_passthrough_fallback(raw, target_lang), source="fallback")


def staff_reply_passthrough_fallback(text: str, target_lang: str = "zh") -> str:
    raw = _compact(text)
    if not raw:
        if target_lang == "en":
            return "The team is still checking your case. We will update you in this chat once there is progress."
        return "后台仍在确认中，有更新会马上在这里通知你。"
    lower = raw.lower()
    reply_type = classify_staff_reply(raw)
    if target_lang == "en":
        if reply_type == "ask_customer":
            if _PHONE_MISMATCH_PATTERN.search(lower):
                return "The team found that the phone number may not match. Please confirm and send the correct registered phone number here so we can continue checking it."
            return with_critical_facts("The team needs additional information from you. Please send the requested details here so we can continue checking it.", raw, target_lang)
        if reply_type == "resolution":
            return with_critical_facts("The team has sent an update. We will continue helping you based on that reply.", raw, target_lang)
        return with_critical_facts("The team has received your case and is checking it now. We will keep following up in this chat. Your funds are safe within our process.", raw, target_lang)
    if reply_type == "ask_customer":
        if _PHONE_MISMATCH_PATTERN.search(lower):
            return "后台核实时发现手机号可能不一致，请你再次确认并发送正确的注册手机号，我们收到后会继续协助确认。"
        return with_critical_facts("后台需要你补充资料，请按照要求提供，我们收到后会继续协助确认。", raw, target_lang)
    if reply_type == "resolution":
        if re.search(r"(withdraw|retiro|出款|提款|取款).*(success|completed|done|成功|完成|processed|aprobado|procesado)", lower):
            return with_critical_facts("后台回复你的提款已处理完成，请你确认账户入账情况。", raw, target_lang)
        if re.search(r"(deposit|recarga|存款|充值).*(success|completed|done|成功|完成|credited|acredit|aprobado)", lower):
            return with_critical_facts("后台回复你的存款已处理完成，请你确认账户余额。", raw, target_lang)
        if _REJECTED_PATTERN.search(lower):
            return with_critical_facts("后台回复此笔目前没有成功通过，我们会按照后台结果继续协助你确认下一步。", raw, target_lang)
        return with_critical_facts("后台已回复，我们会按照这个更新继续协助你处理。", raw, target_lang)
    return with_critical_facts("后台已收到并正在确认，我们会在这个对话内持续跟进。请放心，您的资金在我们的流程下是安全的。", raw, target_lang)


_WAIT_PATTERN = re.compile(r"(wait|checking|review|investig|pending|process(?:ing)?|on process|in process|under review|for review|稍等|审核|審核|查询|查詢|处理中|處理中)")
_REJECTED_PATTERN = re.compile(r"(reject|rejected|rechazad|cancel|cancelad|returned|devuelt|failed|fall[oó]|no exitos|拒绝|拒絕|退回|取消|失败|失敗)")
_ASK_INFO_PATTERN = re.compile(r"(send|ask|need|request|check|verify|correct|env[ií]e|enviar|mandar|solicitar|necesita|pedir|提供|补充|補充|发送|發送|检查|檢查|确认|確認|核对|核對).*(receipt|comprobante|recibo|screenshot|captura|usuario|user|phone|tel[eé]fono|numero|n[uú]mero|资料|資料|截图|截圖|凭证|憑證|电话|電話|用户|用戶|手机号|手機號)")
_RECHECK_CONTACT_PATTERN = re.compile(r"(未查到|查不到|not found|no record|no se encontr).*(电话|電話|手机号|手機號|phone|tel[eé]fono|number|n[uú]mero|用户|用戶|usuario|user)")
_PHONE_MISMATCH_PATTERN = re.compile(r"(电话|電話|手机号|手機號|phone|tel[eé]fono|number|n[uú]mero).{0,12}(不对|不對|错误|錯誤|有误|有誤|不一致|不符|不匹配|wrong|incorrect|mismatch|not match)")
_RECEIPT_PATTERN = re.compile(r"(deposit receipt|successful receipt|payment receipt|transaction receipt|proof of payment|comprobante|recibo|凭证|憑證|水单|水單)")
_SUCCESS_PATTERN = re.compile(r"(processed|approved|credited|completed|done|success|successful|aprobado|procesado|成功|完成|已处理|已處理|已到账|已到帳)")


def classify_staff_reply(text: str) -> str:
    lower = _compact(text).lower()
    if not lower:
        return "long_wait"
    if _ASK_INFO_PATTERN.search(lower) or _RECHECK_CONTACT_PATTERN.search(lower) or _PHONE_MISMATCH_PATTERN.search(lower) or _RECEIPT_PATTERN.search(lower):
        return "ask_customer"
    if _SUCCESS_PATTERN.search(lower) or _REJECTED_PATTERN.search(lower):
        return "resolution"
    if _WAIT_PATTERN.search(lower):
        return "long_wait"
    return "resolution"


def validate_staff_reply_facts(source: str, candidate: str) -> dict:
    original = normalize_fact_text(source)
    output = normalize_fact_text(candidate)
    if not output:
        return {"ok": False, "reason": "empty_output"}
    original_facts = critical_facts(original)
    output_facts = critical_facts(output)
    missing = [fact for fact in original_facts if fact not in output]
    if missing:
        return {"ok": False, "reason": "missing_critical_fact", "facts": missing}
    added = [fact for fact in output_facts if fact not in original]
    if added:
        return {"ok": False, "reason": "added_critical_fact", "facts": added}
    original_status = status_facts(original)
    output_status = status_facts(output)
    for status in output_status:
        if status not in original_status:
            return {"ok": False, "reason": f"added_status_{status}"}
    return {"ok": True}


def has_untranslated_internal_english(text: str, target_lang: str = "zh") -> bool:
    if target_lang not in {"zh", "es"}:
        return False
    return bool(re.search(r"\b(still processing|already on process|on process|in process|checking|wait please|under checking|for review)\b", text.lower()))


def with_critical_facts(text: str, raw: str, target_lang: str = "zh") -> str:
    normalized_text = normalize_fact_text(text)
    facts = [fact for fact in critical_facts(normalize_fact_text(raw)) if fact not in normalized_text]
    if not facts:
        return text
    if target_lang == "en":
        return f"{text} Case details: {', '.join(facts)}."
    return f"{text} 案件资料：{'、'.join(facts)}。"


def critical_facts(text: str) -> list[str]:
    tokens: set[str] = set()
    patterns = [
        r"https?://[^\s)]+",
        r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b",
        r"[$€£]\s*\d+(?:[.,]\d+)?",
        r"\b\d+(?:\s*(?:-|a|to)\s*\d+)?\s*(?:minutos?|minutes?|horas?|hours?|d[ií]as?|days?|semanas?|weeks?)\b",
        r"\b\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?\b",
        r"\b\d+(?:[.,]\d+)?\s*(?:cop|usd|mxn|pesos?|mil)\b",
        r"\b\d{7,}\b",
        r"\b[a-z0-9._-]{5,}\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            token = normalize_fact_token(match.group(0))
            if token and (any(ch.isdigit() for ch in token) or token.startswith("http") or "@" in token):
                tokens.add(token)
    return sorted(tokens)


def status_facts(text: str) -> set[str]:
    statuses = set()
    western_success = r"\b(procesad\w*|processed|aprobad\w*|approved|acreditad\w*|credited|completad\w*|completed|finalizad\w*|done|success|successful)\b"
    western_rejected = r"\b(rechazad\w*|rejected|cancelad\w*|cancelled|canceled|devuelt\w*|returned|refund|reembolso)\b"
    if re.search(western_success, text) or re.search(r"(成功|完成|已处理|已處理|已到账|已到帳)", text):
        statuses.add("success")
    if re.search(western_rejected, text) or re.search(r"(退款|退回|取消|拒绝|拒絕)", text):
        statuses.add("rejected_or_returned")
    return statuses


def normalize_fact_text(text: str) -> str:
    return _compact(text).lower()


def normalize_fact_token(token: str) -> str:
    return normalize_fact_text(token).rstrip(".,;:!?")


def _compact(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _parse_candidate(candidate: dict | str) -> dict:
    if isinstance(candidate, dict):
        return candidate
    if isinstance(candidate, str):
        return {"type": "resolution", "text": candidate}
    return {}
