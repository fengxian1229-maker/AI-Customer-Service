import argparse
import asyncio
import json

from app.core.settings import Settings
from app.db.mysql import create_pool
from app.db.repositories import KnowledgeDocumentRepository
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


def build_seed_documents(tenant_id: str = "default", kb_scope: str = "default") -> list[dict]:
    documents = []
    for document in [*DEFAULT_KNOWLEDGE_DOCUMENTS, *EXTRA_SEED_DOCUMENTS]:
        copied = dict(document)
        copied["tenant_id"] = tenant_id
        copied["kb_scope"] = kb_scope
        copied["enabled"] = True
        documents.append(copied)
    return documents


async def seed_repository(repository, tenant_id: str = "default", kb_scope: str = "default", dry_run: bool = False) -> dict:
    documents = build_seed_documents(tenant_id=tenant_id, kb_scope=kb_scope)
    if dry_run:
        return {
            "dry_run": True,
            "tenant_id": tenant_id,
            "kb_scope": kb_scope,
            "documents": len(documents),
            "upserted": 0,
        }

    results = []
    for document in documents:
        results.append(await repository.insert_idempotent(document))

    return {
        "dry_run": False,
        "tenant_id": tenant_id,
        "kb_scope": kb_scope,
        "documents": len(documents),
        "upserted": len(results),
        "inserted": sum(1 for result in results if result.get("inserted")),
        "duplicates": sum(1 for result in results if result.get("duplicate")),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed default deterministic knowledge documents.")
    parser.add_argument("--tenant-id", default="default", help="Tenant id to seed.")
    parser.add_argument("--kb-scope", default="default", help="Knowledge base scope to seed.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without writing documents.")
    return parser


async def run(argv: list[str] | None = None) -> dict:
    args = build_arg_parser().parse_args(argv)
    if args.dry_run:
        return await seed_repository(None, tenant_id=args.tenant_id, kb_scope=args.kb_scope, dry_run=True)

    settings = Settings(
        livechat_agent_access_token="unused-for-seed",
        livechat_account_id="unused-for-seed",
    )
    pool = await create_pool(settings)
    try:
        repository = KnowledgeDocumentRepository(pool)
        return await seed_repository(repository, tenant_id=args.tenant_id, kb_scope=args.kb_scope)
    finally:
        pool.close()
        await pool.wait_closed()


def main(argv: list[str] | None = None) -> int:
    result = asyncio.run(run(argv))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
