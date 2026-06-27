import argparse
import asyncio
import json
from pathlib import Path

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import KnowledgeDocumentRepository
from app.services.knowledge_blocks import (
    default_text_answer_blocks,
    normalize_metadata_json,
    normalize_question_aliases,
    validate_answer_blocks,
)
from app.services.rag import DEFAULT_KNOWLEDGE_DOCUMENTS


EXTRA_SEED_DOCUMENTS = [
    {
        "id": 3,
        "title": "提款方式说明",
        "content": "你可以在提款页面提交提款申请，并按页面要求完成账户与流水条件确认。具体审核进度需要后台或人工查询。",
        "keywords": ["how to withdraw", "withdraw", "retirar", "cómo retirar", "como retirar", "如何提款", "提款"],
        "language": "multi",
        "priority": 30,
    },
    {
        "id": 4,
        "title": "账户安全说明",
        "content": "如需处理密码或账户安全问题，请优先使用平台提供的安全验证流程，避免向任何人泄露验证码或密码。",
        "keywords": ["password", "contraseña", "account security", "账户安全", "密码"],
        "language": "multi",
        "priority": 40,
    },
]


def build_seed_documents(
    tenant_id: str = "default",
    kb_scope: str = "default",
    source_documents: list[dict] | None = None,
    enabled: bool = True,
    limit: int | None = None,
) -> list[dict]:
    documents = []
    base_documents = source_documents if source_documents is not None else [*DEFAULT_KNOWLEDGE_DOCUMENTS, *EXTRA_SEED_DOCUMENTS]
    for document in base_documents[:limit]:
        copied = dict(document)
        copied["tenant_id"] = tenant_id
        copied["kb_scope"] = kb_scope
        copied["enabled"] = bool(copied.get("enabled", enabled))
        documents.append(copied)
    return documents


def load_source_documents(source_file: str | None) -> list[dict] | None:
    if not source_file:
        return None
    payload = json.loads(Path(source_file).read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else []


async def seed_repository(
    repository,
    tenant_id: str = "default",
    kb_scope: str = "default",
    dry_run: bool = False,
    source_file: str | None = None,
    enabled: bool = True,
    limit: int | None = None,
) -> dict:
    source_documents = load_source_documents(source_file)
    documents = build_seed_documents(
        tenant_id=tenant_id,
        kb_scope=kb_scope,
        source_documents=source_documents,
        enabled=enabled,
        limit=limit,
    )
    result = {
        "dry_run": dry_run,
        "tenant_id": tenant_id,
        "kb_scope": kb_scope,
        "documents": len(documents),
        "inserted": 0,
        "duplicates": 0,
        "skipped": 0,
        "invalid": 0,
    }

    for document in documents:
        if not document.get("title") or not document.get("content"):
            result["skipped"] += 1
            continue
        try:
            prepared = prepare_seed_document(document)
        except ValueError:
            result["invalid"] += 1
            continue
        if dry_run:
            continue
        upsert = await repository.insert_idempotent(prepared)
        if upsert.get("inserted"):
            result["inserted"] += 1
        else:
            result["duplicates"] += 1
    return result


def prepare_seed_document(document: dict) -> dict:
    prepared = dict(document)
    prepared["question_aliases"] = normalize_question_aliases(prepared.get("question_aliases"))
    prepared["metadata_json"] = normalize_metadata_json(prepared.get("metadata_json"))
    if prepared.get("answer_blocks") is None:
        prepared["answer_blocks"] = default_text_answer_blocks(prepared.get("content") or "")
    else:
        prepared["answer_blocks"] = validate_answer_blocks(prepared.get("answer_blocks"))
    return prepared


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed deterministic knowledge documents.")
    parser.add_argument("--tenant-id", default="default", help="Tenant id to seed.")
    parser.add_argument("--kb-scope", default="default", help="Knowledge base scope to seed.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without writing documents.")
    parser.add_argument("--source-file", help="Optional JSON file of knowledge documents.")
    parser.add_argument("--enabled", choices=("true", "false"), default="true", help="Seed documents as enabled or disabled.")
    parser.add_argument("--limit", type=int, help="Limit how many source documents to seed.")
    return parser


async def run(argv: list[str] | None = None) -> dict:
    args = build_arg_parser().parse_args(argv)
    if args.dry_run:
        return await seed_repository(
            None,
            tenant_id=args.tenant_id,
            kb_scope=args.kb_scope,
            dry_run=True,
            source_file=args.source_file,
            enabled=args.enabled == "true",
            limit=args.limit,
        )

    settings = Settings(
        livechat_agent_access_token="unused-for-seed",
        livechat_account_id="unused-for-seed",
    )
    pool = await create_pool(settings)
    try:
        repository = KnowledgeDocumentRepository(pool)
        return await seed_repository(
            repository,
            tenant_id=args.tenant_id,
            kb_scope=args.kb_scope,
            source_file=args.source_file,
            enabled=args.enabled == "true",
            limit=args.limit,
        )
    finally:
        pool.close()
        await pool.wait_closed()


def main(argv: list[str] | None = None) -> int:
    result = asyncio.run(run(argv))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
