import pytest

from app.db.repositories import GraphCheckpointRunRepository, GraphRunErrorRepository
from app.workers import checkpoint_admin

from conftest import (
    assert_mysql_test_database,
    create_bootstrapped_mysql_pool,
    drop_mysql_test_database,
    mysql_test_config,
    provision_mysql_test_settings,
    run,
)


pytestmark = [pytest.mark.integration, pytest.mark.mysql]


def test_checkpoint_admin_mysql_smoke():
    mysql_test_config()
    run(_test_checkpoint_admin_mysql_smoke())


async def _test_checkpoint_admin_mysql_smoke() -> None:
    settings = await provision_mysql_test_settings()
    pool = await create_bootstrapped_mysql_pool(settings=settings)
    try:
        await assert_mysql_test_database(pool)
        checkpoint_runs = GraphCheckpointRunRepository(pool)
        graph_errors = GraphRunErrorRepository(pool)

        run_id = await checkpoint_runs.insert_run(
            {
                "conversation_id": "livechat:checkpoint-admin-chat",
                "graph_thread_id": "livechat:checkpoint-admin-chat",
                "checkpoint_mode": "mysql",
                "status": "CREATED",
                "inbound_event_id": 11,
                "latest_checkpoint_id": None,
                "metadata_json": {"checkpoint_mode": "mysql", "config_summary": {"thread_id": "livechat:checkpoint-admin-chat"}},
            }
        )
        await checkpoint_runs.mark_failed(run_id, RuntimeError("checkpoint admin smoke failed"))
        await graph_errors.insert(
            {
                "conversation_id": "livechat:checkpoint-admin-chat",
                "inbound_event_id": 11,
                "graph_thread_id": "livechat:checkpoint-admin-chat",
                "node_name": "intent_router_node",
                "error_type": "RuntimeError",
                "error_message": "checkpoint admin smoke failed",
                "retryable": 0,
                "state_snapshot": {"conversation_id": "livechat:checkpoint-admin-chat", "route": "sop"},
            }
        )

        list_result = await checkpoint_admin.run_command(
            checkpoint_admin.build_arg_parser().parse_args(["list-runs", "--conversation-id", "livechat:checkpoint-admin-chat"]),
            checkpoint_runs,
            graph_errors,
        )
        show_result = await checkpoint_admin.run_command(
            checkpoint_admin.build_arg_parser().parse_args(["show-run", "--run-id", str(run_id)]),
            checkpoint_runs,
            graph_errors,
        )
        latest_result = await checkpoint_admin.run_command(
            checkpoint_admin.build_arg_parser().parse_args(["latest", "--conversation-id", "livechat:checkpoint-admin-chat"]),
            checkpoint_runs,
            graph_errors,
        )
        error_result = await checkpoint_admin.run_command(
            checkpoint_admin.build_arg_parser().parse_args(["errors", "--conversation-id", "livechat:checkpoint-admin-chat"]),
            checkpoint_runs,
            graph_errors,
        )

        assert list_result["runs"]
        assert show_result["run"]["id"] == run_id
        assert show_result["run"]["status"] == "FAILED"
        assert show_result["run"]["error_message"] == "checkpoint admin smoke failed"
        assert latest_result["run"]["id"] == run_id
        assert error_result["errors"]
        assert error_result["errors"][0]["graph_thread_id"] == "livechat:checkpoint-admin-chat"
        assert error_result["errors"][0]["error_type"] == "RuntimeError"
    finally:
        pool.close()
        await pool.wait_closed()
        await drop_mysql_test_database(settings)
