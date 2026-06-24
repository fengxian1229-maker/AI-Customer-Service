from app.workers.sender_worker import classify_send_result


def test_classify_send_result_marks_success():
    result = classify_send_result({"success": True})

    assert result["status"] == "SENT"
    assert result["last_error"] is None


def test_classify_send_result_marks_livechat_event_id_success():
    result = classify_send_result({"event_id": "event-1"})

    assert result["status"] == "SENT"
    assert result["last_error"] is None


def test_livechat_auth_header_uses_account_and_token():
    from app.channels.livechat.sender_client import LiveChatSenderClient

    client = LiveChatSenderClient(
        base_url="https://api.livechatinc.com/v3.6",
        account_id="account-1",
        access_token="token-1",
    )

    assert client.auth_header() == "Basic YWNjb3VudC0xOnRva2VuLTE="


def test_livechat_auth_header_accepts_preencoded_basic_token():
    from app.channels.livechat.sender_client import LiveChatSenderClient

    client = LiveChatSenderClient(
        base_url="https://api.livechatinc.com/v3.6",
        account_id="account-1",
        access_token="YWNjb3VudC0xOnRva2VuLTE=",
    )

    assert client.auth_header() == "Basic YWNjb3VudC0xOnRva2VuLTE="


def test_livechat_auth_header_accepts_preencoded_basic_token_with_different_env_account():
    from app.channels.livechat.sender_client import LiveChatSenderClient

    client = LiveChatSenderClient(
        base_url="https://api.livechatinc.com/v3.6",
        account_id="different-account",
        access_token="YWNjb3VudC0xOnRva2VuLTE=",
    )

    assert client.auth_header() == "Basic YWNjb3VudC0xOnRva2VuLTE="
