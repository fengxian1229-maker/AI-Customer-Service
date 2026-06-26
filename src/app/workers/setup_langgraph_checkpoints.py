import asyncio
import json

from app.core.settings import Settings
from app.graph.checkpointing import CHECKPOINT_MODE_MYSQL, build_checkpointer, check_mysql_checkpoint_version, normalize_checkpoint_mode


async def run() -> dict:
    settings = Settings(
        livechat_agent_access_token="unused-for-checkpoint-setup",
        livechat_account_id="unused-for-checkpoint-setup",
    )
    checkpoint_mode = normalize_checkpoint_mode(settings.langgraph_checkpoint_mode)
    result = {
        "worker": "setup_langgraph_checkpoints",
        "checkpoint_mode": checkpoint_mode,
        "setup": False,
        "status": None,
        "error_type": None,
        "error_message": None,
    }
    if checkpoint_mode != CHECKPOINT_MODE_MYSQL:
        result["status"] = "skipped"
        return result

    managed_checkpointer = None
    try:
        version_info = await check_mysql_checkpoint_version(settings)
        managed_checkpointer = build_checkpointer(checkpoint_mode, settings=settings)
        managed_checkpointer.checkpointer.setup()
        result["setup"] = True
        result["status"] = "ok"
        result["server"] = version_info
        return result
    except Exception as exc:
        result["status"] = "error"
        result["error_type"] = type(exc).__name__
        result["error_message"] = str(exc)
        return result
    finally:
        if managed_checkpointer is not None:
            managed_checkpointer.close()


def main() -> int:
    result = asyncio.run(run())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"ok", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
