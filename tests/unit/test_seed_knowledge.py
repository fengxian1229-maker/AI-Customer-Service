import asyncio

from app.workers import seed_knowledge


class FakeKnowledgeRepository:
    def __init__(self) -> None:
        self.inserted = []

    async def insert_idempotent(self, document: dict) -> dict:
        self.inserted.append(document)
        return {"inserted": True, "duplicate": False, "id": len(self.inserted)}


def test_seed_knowledge_parser_accepts_tenant_scope_and_dry_run():
    args = seed_knowledge.build_arg_parser().parse_args(
        ["--tenant-id", "default", "--kb-scope", "default", "--dry-run"]
    )

    assert args.tenant_id == "default"
    assert args.kb_scope == "default"
    assert args.dry_run is True


def test_seed_knowledge_dry_run_does_not_write_repository():
    repository = FakeKnowledgeRepository()

    result = asyncio.run(seed_knowledge.seed_repository(repository, tenant_id="default", kb_scope="default", dry_run=True))

    assert result["dry_run"] is True
    assert result["documents"] >= 4
    assert repository.inserted == []


def test_seed_knowledge_non_dry_run_calls_insert_idempotent():
    repository = FakeKnowledgeRepository()

    result = asyncio.run(seed_knowledge.seed_repository(repository, tenant_id="default", kb_scope="default"))

    assert result["dry_run"] is False
    assert result["upserted"] == result["documents"]
    assert len(repository.inserted) == result["documents"]
    assert {document["tenant_id"] for document in repository.inserted} == {"default"}
    assert {document["kb_scope"] for document in repository.inserted} == {"default"}


def test_seed_knowledge_documents_do_not_contain_backend_fact_answers():
    banned = ("已到账", "审核通过", "余额是", "订单已", "提款成功")

    for document in seed_knowledge.build_seed_documents(tenant_id="default", kb_scope="default"):
        content = document["content"]
        assert not any(marker in content for marker in banned)
