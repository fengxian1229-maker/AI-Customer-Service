try:
    from app.workflows.backend_dispute_escalation import (
        apply_backend_conclusion,
        backend_conclusion_record,
        clear_backend_dispute_memory,
        evaluate_backend_dispute,
        mark_backend_recheck_pending,
        resolve_backend_recheck,
    )
except ModuleNotFoundError:
    apply_backend_conclusion = None
    backend_conclusion_record = None
    clear_backend_dispute_memory = None
    evaluate_backend_dispute = None
    mark_backend_recheck_pending = None
    resolve_backend_recheck = None


def _result(remaining: str) -> dict:
    return {
        "status": "success",
        "intent": "withdrawal_blocked_or_rollover",
        "reply_intent": "backend_turnover_remaining",
        "reply_facts": {"remaining_turnover": remaining},
    }


def _state(*, text: str, event_id: str, count: int = 0, last_event_id: str | None = None) -> dict:
    memory = apply_backend_conclusion({}, _result("18.88"), recorded_at="2026-07-15T03:19:41Z")
    memory["backend_dispute_count"] = count
    if last_event_id is not None:
        memory["backend_dispute_last_event_id"] = last_event_id
    return {
        "event_id": event_id,
        "raw_user_input": text,
        "slot_memory": memory,
    }


def test_backend_conclusion_fingerprint_is_stable_for_same_business_facts():
    first = backend_conclusion_record(_result("18.88"), recorded_at="2026-07-15T03:19:41Z")
    second = backend_conclusion_record(
        {
            "reply_facts": {"remaining_turnover": "18.88"},
            "reply_intent": "backend_turnover_remaining",
            "intent": "withdrawal_blocked_or_rollover",
            "status": "success",
        },
        recorded_at="2026-07-15T03:20:59Z",
    )

    assert first["fingerprint"] == second["fingerprint"]
    assert first["recorded_at"] != second["recorded_at"]


def test_changed_backend_fact_changes_fingerprint_and_resets_dispute_count():
    memory = apply_backend_conclusion({}, _result("18.88"), recorded_at="2026-07-15T03:19:41Z")
    memory["backend_dispute_count"] = 1
    memory["backend_dispute_last_event_id"] = "event-1"

    updated = apply_backend_conclusion(memory, _result("0.00"), recorded_at="2026-07-15T03:20:59Z")

    assert updated["backend_conclusion"]["fingerprint"] != memory["backend_conclusion"]["fingerprint"]
    assert updated["backend_dispute_count"] == 0
    assert "backend_dispute_last_event_id" not in updated


def test_same_backend_conclusion_preserves_dispute_count():
    memory = apply_backend_conclusion({}, _result("18.88"), recorded_at="2026-07-15T03:19:41Z")
    memory["backend_dispute_count"] = 1

    updated = apply_backend_conclusion(memory, _result("18.88"), recorded_at="2026-07-15T03:20:59Z")

    assert updated["backend_dispute_count"] == 1


def test_player_not_found_is_not_persisted_as_authoritative_conclusion():
    updated = apply_backend_conclusion(
        {"account_or_phone": "test-player"},
        {
            "status": "success",
            "intent": "withdrawal_blocked_or_rollover",
            "reply_intent": "backend_player_not_found",
            "reply_facts": {},
        },
        recorded_at="2026-07-15T03:20:59Z",
    )

    assert updated == {"account_or_phone": "test-player"}


def test_second_dispute_waits_while_backend_recheck_is_pending():
    first = evaluate_backend_dispute(
        _state(
            text="Ya intenté retirar cuatro veces y siempre lo devuelven",
            event_id="event-1",
        )
    )
    pending_memory = mark_backend_recheck_pending(first["state"]["slot_memory"])
    second = evaluate_backend_dispute(
        {
            **first["state"],
            "slot_memory": pending_memory,
            "event_id": "event-2",
            "raw_user_input": "Siempre me dicen que juegue y después aparece retiro fallido",
        }
    )

    assert first["count"] == 1
    assert first["should_handoff"] is False
    assert second["count"] == 1
    assert second["should_handoff"] is False
    assert second["waiting_for_recheck"] is True
    assert second["state"]["slot_memory"]["backend_recheck_queued_dispute"] is True
    assert second["state"]["slot_memory"]["backend_recheck_queued_event_id"] == "event-2"


def test_same_recheck_result_releases_queued_dispute_for_handoff():
    memory = _state(text="Otra vez", event_id="event-1", count=1)["slot_memory"]
    memory = mark_backend_recheck_pending(memory)
    memory["backend_recheck_queued_dispute"] = True
    memory["backend_recheck_queued_event_id"] = "event-2"

    resolved = resolve_backend_recheck(memory, _result("18.88"), recorded_at="2026-07-15T03:20:59Z")

    assert resolved["should_handoff"] is True
    assert resolved["same_conclusion"] is True
    assert resolved["slot_memory"]["backend_dispute_count"] == 2
    assert "backend_recheck_pending" not in resolved["slot_memory"]


def test_changed_recheck_result_clears_queued_dispute_without_handoff():
    memory = _state(text="Otra vez", event_id="event-1", count=1)["slot_memory"]
    memory = mark_backend_recheck_pending(memory)
    memory["backend_recheck_queued_dispute"] = True

    resolved = resolve_backend_recheck(memory, _result("0.00"), recorded_at="2026-07-15T03:20:59Z")

    assert resolved["should_handoff"] is False
    assert resolved["same_conclusion"] is False
    assert resolved["slot_memory"]["backend_dispute_count"] == 0
    assert "backend_recheck_queued_dispute" not in resolved["slot_memory"]


def test_duplicate_event_does_not_increment_backend_dispute():
    first = evaluate_backend_dispute(
        _state(text="Otra vez el mismo problema", event_id="event-1")
    )
    duplicate = evaluate_backend_dispute(
        {
            **first["state"],
            "event_id": "event-1",
            "raw_user_input": "Otra vez el mismo problema",
        }
    )

    assert duplicate["count"] == 1
    assert duplicate["should_handoff"] is False


def test_non_dispute_does_not_change_counter():
    assert evaluate_backend_dispute(
        _state(text="¿Qué significa rollover?", event_id="event-1")
    ) is None


def test_clear_backend_dispute_memory_preserves_conclusion():
    memory = _state(
        text="Otra vez el mismo problema",
        event_id="event-1",
        count=1,
        last_event_id="event-1",
    )["slot_memory"]

    cleared = clear_backend_dispute_memory(memory)

    assert "backend_conclusion" in cleared
    assert "backend_dispute_count" not in cleared
    assert "backend_dispute_last_event_id" not in cleared
