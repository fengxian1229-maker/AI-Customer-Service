import re
from typing import Any


COMMON_WORDS = {
    "hola",
    "buenas",
    "gracias",
    "retiro",
    "deposito",
    "depósito",
    "recarga",
    "usuario",
    "telefono",
    "teléfono",
    "correo",
    "email",
    "hello",
    "thanks",
}

PAYMENT_CHANNELS = {
    "gcash",
    "maya",
    "pix",
    "spei",
    "oxxo",
    "pse",
    "nequi",
    "daviplata",
}


def normalize_text(text: str | None) -> str:
    return str(text or "").strip()


def extract_identity(text: str | None) -> dict[str, str] | None:
    raw = normalize_text(text)
    if not raw:
        return None
    email = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", raw, re.I)
    if email:
        return {"type": "email", "value": email.group(0)}
    phone = re.search(r"\b(?:\+?\d[\d\s-]{6,18}\d)\b", raw)
    if phone:
        return {"type": "phone", "value": re.sub(r"\s+", "", phone.group(0))}
    username = re.search(
        r"(?:\b(?:usuario|user|username|cuenta|mi usuario es|mi user es)\b|用户名|用戶名|账号|帳號|账户|賬戶)\s*(?:是|为|為)?\s*[:：-]?\s*([a-zA-Z][a-zA-Z0-9_.-]{3,30})\b",
        raw,
        re.I,
    )
    if username and username.group(1).lower() not in COMMON_WORDS | PAYMENT_CHANNELS:
        return {"type": "username", "value": username.group(1)}
    return None


def extract_transaction_signal(text: str | None) -> dict[str, str] | None:
    raw = normalize_text(text)
    if not raw:
        return None
    if re.search(r"\b(ref|referencia|n[uú]mero|orden|pedido|transacci[oó]n|id)\b", raw, re.I) and re.search(r"\d{4,}", raw):
        return {"type": "reference", "value": raw}
    money_like = re.search(r"\b(?:\d{4,}|\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?|\d{1,3}(?:[.,]\d{2}))\b", raw)
    if money_like and re.search(r"\b(monto|valor|cop|pesos?|deposit[eé]|dep[oó]sito|retir[eé]|retiro|pagu[eé]|pago)\b", raw, re.I):
        return {"type": "amount_or_transaction", "value": raw}
    if re.search(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", raw):
        return {"type": "date", "value": raw}
    return None


def extract_amount(text: str | None) -> str | None:
    raw = normalize_text(text)
    labelled = re.search(
        r"(?:金额|金額|monto|valor|amount)\s*[:：-]?\s*([A-Z]{0,3}\s*\d{4,}|[A-Z]{0,3}\s*\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?|[A-Z]{0,3}\s*\d{1,3}(?:[.,]\d{2})?)",
        raw,
        re.I,
    )
    if labelled:
        return labelled.group(1).strip()
    match = re.search(r"\b(?:\d{4,}|\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?|\d{1,3}(?:[.,]\d{2}))\b", raw)
    return match.group(0) if match else None


def extract_order_id(text: str | None) -> str | None:
    raw = normalize_text(text)
    labelled = re.search(
        r"(?:订单|訂單|order|orden|pedido|transacci[oó]n|transaction|ref(?:erencia)?)\s*(?:号|號|id|number|n[uú]mero)?\s*[:：#-]?\s*([A-Z]{1,6}\d{4,})",
        raw,
        re.I,
    )
    if labelled:
        return labelled.group(1).upper()
    generic = re.search(r"\b([A-Z]{1,6}\d{4,})\b", raw, re.I)
    return generic.group(1).upper() if generic else None


def extract_channel(text: str | None) -> str | None:
    raw = normalize_text(text)
    labelled = re.search(
        r"(?:渠道|通道|channel|canal|via|v[ií]a)\s*[:：-]?\s*([A-Za-z0-9_\-\u4e00-\u9fff]{2,30})",
        raw,
        re.I,
    )
    if labelled:
        return labelled.group(1).strip()
    known = re.search(r"\b(GCASH|MAYA|PIX|SPEI|OXXO|PSE|NEQUI|DAVIPLATA)\b", raw, re.I)
    return known.group(1).upper() if known else None


def is_wallet_or_receiving_account_change(text: str | None) -> bool:
    raw = normalize_text(text).lower()
    if not raw:
        return False
    if not extract_channel(raw):
        return False
    return bool(
        re.search(
            r"(agreg(?:ar|ué|ue|ado)|añadir|add(?:ed)?|change|cambiar|otra\s+cuenta|another\s+account|"
            r"billetera|wallet|receiving\s+account|收款账户|收款帳戶|钱包|錢包)",
            raw,
            re.I,
        )
    )


def is_explicit_human_request(text: str | None) -> bool:
    raw = normalize_text(text)
    if any(token in raw for token in ("真人客服", "人工客服")):
        return True
    return bool(
        re.search(
            r"\b(humano|humana|persona real|agente|asesor|representante|atenci[oó]n humana|live support|human|agent|真人|人工|客服人员|客服人員)\b",
            raw,
            re.I,
        )
    )


def attachment_urls(attachments: list[dict[str, Any]]) -> list[str]:
    return [str(item["url"]) for item in attachments if item.get("url")]
