def conversation_id_for_chat(chat_id: str, thread_id: str | None = None) -> str:
    chat = str(chat_id or "unknown")
    thread = str(thread_id or "").strip()
    if thread:
        return f"livechat:{chat}:{thread}"
    return f"livechat:{chat}"
