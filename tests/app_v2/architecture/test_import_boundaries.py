from app_v2.architecture.import_boundaries import scan_forbidden_imports
from app_v2.graph.nodes import NODE_REGISTRY
from app_v2.graph.state import GraphState


def test_scan_forbidden_imports_reports_old_business_orchestration(tmp_path):
    source_root = tmp_path / "app_v2"
    source_root.mkdir()
    (source_root / "bad_adapter.py").write_text(
        "from app.workflows.models import WorkflowState\n",
        encoding="utf-8",
    )

    violations = scan_forbidden_imports(source_root)

    assert [(item.module, item.line) for item in violations] == [
        ("app.workflows.models", 1),
    ]


def test_scan_forbidden_imports_allows_neutral_low_level_components(tmp_path):
    source_root = tmp_path / "app_v2"
    source_root.mkdir()
    (source_root / "adapters.py").write_text(
        "from app.db.mysql import mysql_pool_kwargs\n"
        "from app.llm.gemini_model import build_gemini_chat_model\n"
        "from app.backends.tac_client import TacBackendClient\n",
        encoding="utf-8",
    )

    assert scan_forbidden_imports(source_root) == []


def test_current_v2_package_respects_import_boundary():
    assert scan_forbidden_imports("src/app_v2") == []


def test_graph_state_has_only_the_approved_top_level_groups():
    assert set(GraphState.__required_keys__) == {
        "event",
        "session",
        "runtime",
        "understanding",
        "execution",
        "reply",
        "trace_context",
    }


def test_complete_stage_one_node_catalog_is_publicly_registered():
    assert set(NODE_REGISTRY) == {
        "load_event_context",
        "analyze_multimodal_content",
        "normalize_turn",
        "classify_intent",
        "interpret_workflow",
        "knowledge",
        "workflow_engine",
        "prepare_handoff",
        "prepare_direct_reply",
        "prepare_clarification",
        "compose_reply",
        "fact_guard",
        "persist_result",
        "apply_capability_result",
        "prepare_result_reply",
        "verify_job_still_pending",
        "prepare_query_pending_reply",
        "apply_control_result",
        "prepare_control_reply",
        "apply_conversation_status_update",
        "load_deferred_messages",
        "build_deferred_message_batch",
    }
    assert all(callable(node) for node in NODE_REGISTRY.values())
