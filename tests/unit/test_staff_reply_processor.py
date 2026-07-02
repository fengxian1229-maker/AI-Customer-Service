from app.services.staff_reply_processor import StaffReplyProcessor, staff_reply_passthrough_fallback, validate_staff_reply_facts


def test_staff_reply_fallback_rewrites_processing_wait():
    result = StaffReplyProcessor(enabled=False).process("still processing order 12345678", target_lang="zh")

    assert result.type == "long_wait"
    assert "后台已收到" in result.text
    assert "12345678" in result.text
    assert result.source == "fallback"


def test_staff_reply_fallback_detects_ask_customer():
    text = staff_reply_passthrough_fallback("need deposit receipt for user abc12345", target_lang="zh")

    assert "补充资料" in text
    assert "abc12345" in text


def test_staff_reply_fallback_detects_phone_recheck_as_ask_customer():
    result = StaffReplyProcessor(enabled=False).process("未查到该笔订单，请再检查一遍电话是否提供错误", target_lang="zh")

    assert result.type == "ask_customer"
    assert "手机号可能不一致" in result.text


def test_staff_reply_fallback_detects_phone_mismatch_as_ask_customer():
    result = StaffReplyProcessor(enabled=False).process("我查了这笔订单，貌似手机号不对", target_lang="zh")

    assert result.type == "ask_customer"
    assert "手机号可能不一致" in result.text
    assert "正确的注册手机号" in result.text


def test_staff_reply_fact_validation_rejects_added_success_status():
    result = validate_staff_reply_facts("still checking order 987654321", "已经成功处理 order 987654321")

    assert result["ok"] is False
    assert result["reason"] == "added_status_success"


def test_staff_reply_uses_model_when_fact_safe():
    processor = StaffReplyProcessor(model_rewriter=lambda _text, _lang: {"type": "long_wait", "text": "后台仍在审核订单 12345678。"})

    result = processor.process("checking order 12345678", target_lang="zh")

    assert result.source == "model"
    assert result.type == "long_wait"
    assert result.text == "后台仍在审核订单 12345678。"
