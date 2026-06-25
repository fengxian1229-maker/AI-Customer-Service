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
        r"\b(?:usuario|user|username|cuenta|mi usuario es|mi user es)\s*[:：-]?\s*([a-zA-Z][a-zA-Z0-9_.-]{3,30})\b",
        raw,
        re.I,
    )
    if username and username.group(1).lower() not in COMMON_WORDS:
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
    match = re.search(r"\b(?:\d{4,}|\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?|\d{1,3}(?:[.,]\d{2}))\b", raw)
    return match.group(0) if match else None


def is_explicit_human_request(text: str | None) -> bool:
    raw = normalize_text(text)
    return bool(
        re.search(
            r"\b(humano|humana|persona real|agente|asesor|representante|atenci[oó]n humana|live support|human|agent|真人|人工|客服人员|客服人員)\b",
            raw,
            re.I,
        )
    )


def attachment_urls(attachments: list[dict[str, Any]]) -> list[str]:
    return [str(item["url"]) for item in attachments if item.get("url")]
