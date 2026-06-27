import argparse
import asyncio
import json
import uuid
from pathlib import Path

import aiomysql

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import (
    ExternalCommandRepository,
    InboundEventRepository,
    KnowledgeDocumentRepository,
    OutboundMessageRepository,
)
from app.schemas.events import InboundEvent
from app.workers import gateway_consumer
from app.workers.seed_knowledge import seed_repository


DEFAULT_SKIP_ERROR = "manual guarded smoke dry-run; not sent"
DEFAULT_EXTERNAL_COMMAND_SKIP_ERROR = "manual guarded smoke dry-run; external command not executed"

DEFAULT_CASES = [
    {
        "case_id": "faq_deposit_howto_zh",
        "input_text": "怎么存款？",
        "event_type": "message",
        "standard_event_type": "MESSAGE_CREATED",
        "expected": {"allow_routes": {"faq"}, "external_commands": 0},
    },
    {
        "case_id": "faq_withdrawal_howto_en",
        "input_text": "how to withdraw?",
        "event_type": "message",
        "standard_event_type": "MESSAGE_CREATED",
        "expected": {"allow_routes": {"faq"}, "external_commands": 0},
    },
    {
        "case_id": "sop_deposit_missing_es",
        "input_text": "mi deposito no llegó",
        "event_type": "message",
        "standard_event_type": "MESSAGE_CREATED",
        "expected": {"allow_routes": {"sop", "human_handoff"}, "forbid_routes": {"faq"}, "external_commands": 0},
    },
    {
        "case_id": "explicit_human_en",
        "input_text": "I want a human agent",
        "event_type": "message",
        "standard_event_type": "MESSAGE_CREATED",
        "expected": {
            "allow_routes": {"human_handoff"},
            "forbid_routes": {"faq"},
            "allow_external_commands": True,
            "allow_external_command_types": {"human_handoff.requested"},
        },
    },
    {
        "case_id": "backend_fact_balance_en",
        "input_text": "what is my balance?",
        "event_type": "message",
        "standard_event_type": "MESSAGE_CREATED",
        "expected": {
            "forbid_routes": {"faq"},
            "allow_external_commands": True,
            "allow_external_command_types": {"human_handoff.requested", "backend.query"},
        },
    },
    {
        "case_id": "backend_fact_order_status_zh",
        "input_text": "我的订单现在是什么状态？",
        "event_type": "message",
        "standard_event_type": "MESSAGE_CREATED",
        "expected": {
            "forbid_routes": {"faq"},
            "allow_external_commands": True,
            "allow_external_command_types": {"human_handoff.requested", "backend.query"},
        },
    },
    {
        "case_id": "file_without_text",
        "input_text": "",
        "event_type": "file",
        "standard_event_type": "FILE_RECEIVED",
        "expected": {"forbid_routes": {"faq"}, "router_must_not_accept": True, "external_commands": 0},
    },
]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a real Gemini guarded-authoritative dry-run smoke review.")
    parser.add_argument("--case-set", default="default")
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--kb-scope", default="default")
    parser.add_argument("--seed-default-faq", action="store_true")
    parser.add_argument("--min-confidence", type=float)
    parser.add_argument("--json", action="store_true", default=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--case")
    return parser


async def run(argv: list[str] | None = None) -> dict:
    args = build_arg_parser().parse_args(argv)
    base_summary = {
        "worker": "real_gemini_guarded_smoke",
        "case_set": args.case_set,
        "total": 0,
        "passed": 0,
        "failed": 0,
        "cases": [],
        "smoke_success": False,
    }
    cases, selection_error = _select_cases(args)
    if selection_error:
        return {
            **base_summary,
            "error": selection_error,
        }

    try:
        settings = Settings(
            livechat_agent_access_token="unused-for-real-gemini-guarded-smoke",
            livechat_account_id="unused-for-real-gemini-guarded-smoke",
        )
    except TypeError:
        settings = Settings()
    except Exception as exc:
        return {
            **base_summary,
            "error": {"code": "invalid_settings", "message": str(exc), "error_type": type(exc).__name__},
        }

    if args.min_confidence is not None:
        setattr(settings, "llm_router_min_confidence", args.min_confidence)

    llm_provider = str(getattr(settings, "llm_provider", "") or "").strip().lower()
    llm_router_mode = str(getattr(settings, "llm_router_mode", "") or "").strip().lower()
    if llm_provider != "gemini" or llm_router_mode != "guarded_authoritative":
        return {
            **base_summary,
            "error": {
                "code": "invalid_settings",
                "message": "real_gemini_guarded_smoke requires llm_provider=gemini and llm_router_mode=guarded_authoritative",
            },
        }

    pool = await create_pool(settings)
    try:
        seed_result = None
        if args.seed_default_faq:
            seed_result = await seed_repository(
                KnowledgeDocumentRepository(pool),
                tenant_id=args.tenant_id,
                kb_scope=args.kb_scope,
                source_file=str(Path("data/knowledge/default_multimodal_faq_seed.json")),
            )
        results = []
        for case in cases:
            results.append(await _run_case(pool, settings, args, case))
        failed = sum(1 for result in results if not result["pass"])
        return {
            **base_summary,
            "seed_result": seed_result,
            "total": len(results),
            "passed": len(results) - failed,
            "failed": failed,
            "cases": results,
            "smoke_success": failed == 0,
        }
    finally:
        pool.close()
        await pool.wait_closed()


def _select_cases(args) -> tuple[list[dict], dict | None]:
    if args.case_set != "default":
        return [], {"code": "unsupported_case_set", "message": f"unsupported case-set: {args.case_set}"}
    if args.limit is not None and args.limit <= 0:
        return [], {"code": "empty_case_selection", "message": "--limit must be greater than 0"}
    cases = [case for case in DEFAULT_CASES if not args.case or case["case_id"] == args.case]
    if args.case and not cases:
        return [], {"code": "unknown_case", "message": f"unknown case: {args.case}"}
    if args.limit is not None:
        cases = cases[: args.limit]
    if not cases:
        return [], {"code": "empty_case_selection", "message": "case selection is empty"}
    return cases, None


async def _run_case(pool, settings, args, case: dict) -> dict:
    summary = _case_summary(case, args)
    inbound_event_id = await _insert_smoke_event(pool, case, summary)
    gateway_result = await gateway_consumer.process_inbound_event_id(
        pool,
        inbound_event_id=inbound_event_id,
        checkpoint_mode=settings.langgraph_checkpoint_mode,
        settings=settings,
    )
    conversation_id = summary["conversation_id"]
    llm_router = await _fetch_latest_router_metadata(pool, conversation_id, inbound_event_id)
    outbound_messages = await _fetch_outbound_messages(pool, conversation_id, inbound_event_id)
    external_commands = await _fetch_external_commands(pool, conversation_id, inbound_event_id)
    graph_run_errors = await _fetch_graph_run_errors(pool, conversation_id, inbound_event_id)
    skipped_unsent_count = await OutboundMessageRepository(pool).mark_pending_by_inbound_event_skipped(
        inbound_event_id,
        error=DEFAULT_SKIP_ERROR,
    )
    skipped_external_command_count = await ExternalCommandRepository(pool).mark_pending_by_inbound_event_skipped(
        inbound_event_id,
        error=DEFAULT_EXTERNAL_COMMAND_SKIP_ERROR,
    )
    final_route = _actual_final_route(gateway_result, llm_router)
    final_intent = _actual_final_intent(gateway_result, llm_router)
    evaluation = evaluate_case_result(case, final_route, _route_source(gateway_result, llm_router), llm_router, external_commands)
    if graph_run_errors:
        evaluation = {"pass": False, "failure_reason": "graph_run_errors_present"}
    return {
        "case_id": case["case_id"],
        "input_text": case.get("input_text", ""),
        "inbound_event_id": inbound_event_id,
        "conversation_id": conversation_id,
        "expected": _jsonable_expected(case.get("expected") or {}),
        "actual_final_route": final_route,
        "actual_final_intent": final_intent,
        "route_source": _route_source(gateway_result, llm_router),
        "rewrite_source": _rewrite_source(gateway_result, llm_router),
        "llm_router": llm_router,
        "outbound_count": len(outbound_messages),
        "external_command_count": len(external_commands),
        "skipped_external_command_count": skipped_external_command_count,
        "external_commands": external_commands,
        "graph_run_errors": graph_run_errors,
        "skipped_unsent_count": skipped_unsent_count,
        "pass": evaluation["pass"],
        "failure_reason": evaluation.get("failure_reason"),
    }


def evaluate_case_result(
    case: dict,
    actual_final_route: str | None,
    route_source: str | None,
    llm_router: dict | None,
    external_commands: list[dict],
) -> dict:
    expected = case.get("expected") or {}
    external_command_count = len(external_commands)
    if expected.get("external_commands") is not None:
        if external_command_count != expected["external_commands"]:
            return {"pass": False, "failure_reason": "external_command_count_mismatch"}
    elif expected.get("allow_external_commands"):
        allowed_types = expected.get("allow_external_command_types") or set()
        for command in external_commands:
            command_type = command.get("command_type")
            if command_type not in allowed_types:
                return {"pass": False, "failure_reason": f"external_command_type_not_allowed:{command_type}"}
    elif external_command_count:
        return {"pass": False, "failure_reason": "external_command_count_mismatch"}
    if actual_final_route in (expected.get("forbid_routes") or set()):
        return {"pass": False, "failure_reason": f"forbidden_route:{actual_final_route}"}
    allow_routes = expected.get("allow_routes")
    if allow_routes and actual_final_route not in allow_routes:
        return {"pass": False, "failure_reason": f"unexpected_route:{actual_final_route}"}
    if expected.get("router_must_not_accept") and (llm_router or {}).get("status") == "accepted":
        return {"pass": False, "failure_reason": "router_accepted_guarded_case"}
    return {"pass": True, "failure_reason": None}


def _case_summary(case: dict, args) -> dict:
    smoke_id = uuid.uuid4().hex[:12]
    chat_id = f"manual-gemini-guarded-{case['case_id']}-{smoke_id}:chat"
    thread_id = f"manual-gemini-guarded-{case['case_id']}-{smoke_id}:thread"
    return {
        "tenant_id": args.tenant_id,
        "kb_scope": args.kb_scope,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "conversation_id": f"livechat:{chat_id}",
    }


async def _insert_smoke_event(pool, case: dict, summary: dict) -> int:
    event_id = f"manual-gemini-guarded:{case['case_id']}:{uuid.uuid4().hex}"
    text = case.get("input_text", "")
    event = InboundEvent(
        source="manual_smoke",
        raw_action="manual.real_gemini_guarded_smoke.message",
        chat_id=summary["chat_id"],
        thread_id=summary["thread_id"],
        event_id=event_id,
        event_type=case.get("event_type", "message"),
        standard_event_type=case.get("standard_event_type", "MESSAGE_CREATED"),
        author_id="manual-smoke",
        sender_role="external",
        occurred_at="2026-06-27 00:00:00.000000",
        dedup_key=event_id,
        payload_json={"event": {"type": case.get("event_type", "message"), "text": text}, "text": text},
        ignored=False,
    )
    result = await InboundEventRepository(pool).insert(event)
    if result.get("id"):
        return result["id"]
    row = await _fetch_one(pool, "SELECT id FROM inbound_events WHERE dedup_key = %s", (event.dedup_key,))
    return row["id"]


async def _fetch_latest_router_metadata(pool, conversation_id: str, inbound_event_id: int) -> dict | None:
    row = await _fetch_one(
        pool,
        """
        SELECT metadata_json
        FROM graph_checkpoint_runs
        WHERE conversation_id = %s
          AND inbound_event_id = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (conversation_id, inbound_event_id),
        required=False,
    )
    if not row:
        return None
    return (row.get("metadata_json") or {}).get("llm_router")


async def _fetch_outbound_messages(pool, conversation_id: str, inbound_event_id: int) -> list[dict]:
    return await _fetch_all(
        pool,
        """
        SELECT id, inbound_event_id, conversation_id, action_type, command_type,
               message_type, message_kind, block_index, status, last_error, payload_json
        FROM outbound_messages
        WHERE conversation_id = %s AND inbound_event_id = %s
        ORDER BY COALESCE(block_index, 0), id
        """,
        (conversation_id, inbound_event_id),
    )


async def _fetch_external_commands(pool, conversation_id: str, inbound_event_id: int) -> list[dict]:
    return await _fetch_all(
        pool,
        """
        SELECT id, inbound_event_id, conversation_id, command_type, status, payload_json
        FROM external_commands
        WHERE conversation_id = %s
          AND inbound_event_id = %s
        ORDER BY id
        """,
        (conversation_id, inbound_event_id),
    )


async def _fetch_graph_run_errors(pool, conversation_id: str, inbound_event_id: int) -> list[dict]:
    return await _fetch_all(
        pool,
        """
        SELECT id, inbound_event_id, error_type, error_message
        FROM graph_run_errors
        WHERE conversation_id = %s
          AND inbound_event_id = %s
        ORDER BY id
        """,
        (conversation_id, inbound_event_id),
    )


async def _fetch_one(pool, sql: str, args: tuple, required: bool = True) -> dict | None:
    rows = await _fetch_all(pool, sql, args)
    if not rows:
        if required:
            raise RuntimeError("expected one row")
        return None
    return rows[0]


async def _fetch_all(pool, sql: str, args: tuple) -> list[dict]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            rows = list(await cur.fetchall())
    for row in rows:
        for key in ("payload_json", "metadata_json"):
            if key in row and isinstance(row[key], str):
                row[key] = json.loads(row[key])
    return rows


def _actual_final_route(gateway_result: dict, llm_router: dict | None) -> str | None:
    graph_state = _first_graph_state(gateway_result)
    return graph_state.get("route") or (llm_router or {}).get("final_route")


def _actual_final_intent(gateway_result: dict, llm_router: dict | None) -> str | None:
    graph_state = _first_graph_state(gateway_result)
    return (graph_state.get("intent_result") or {}).get("intent") or (llm_router or {}).get("final_intent")


def _route_source(gateway_result: dict, llm_router: dict | None) -> str | None:
    graph_state = _first_graph_state(gateway_result)
    return graph_state.get("route_source") or (llm_router or {}).get("route_source")


def _rewrite_source(gateway_result: dict, llm_router: dict | None) -> str | None:
    graph_state = _first_graph_state(gateway_result)
    return graph_state.get("rewrite_source") or (llm_router or {}).get("rewrite_source")


def _first_graph_state(gateway_result: dict) -> dict:
    for result in gateway_result.get("results") or []:
        if result.get("graph_state"):
            return result["graph_state"]
    return {}


def _jsonable_expected(expected: dict) -> dict:
    return {key: sorted(value) if isinstance(value, set) else value for key, value in expected.items()}


def main(argv: list[str] | None = None) -> int:
    result = asyncio.run(run(argv))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
