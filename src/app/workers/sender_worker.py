def classify_send_result(response: dict) -> dict:
    if response.get("success") or response.get("event_id"):
        return {"status": "SENT", "last_error": None}
    return {"status": "FAILED", "last_error": "send failed"}


async def process_pending_message(outbound_repository, sender_client, message: dict) -> dict:
    payload = message["payload_json"]
    response = await sender_client.send_text(
        chat_id=message["chat_id"],
        thread_id=message.get("thread_id"),
        text=payload["text"],
    )
    result = classify_send_result(response)
    if result["status"] == "SENT":
        await outbound_repository.mark_sent(message["id"])
    else:
        await outbound_repository.mark_failed(message["id"], result["last_error"])
    return result
