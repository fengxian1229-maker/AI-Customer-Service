import asyncio
import json

from app.workers import seed_knowledge


class FakeKnowledgeRepository:
    def __init__(self) -> None:
        self.inserted = []

    async def insert_idempotent(self, document: dict) -> dict:
        self.inserted.append(document)
        return {"inserted": True, "duplicate": False, "id": len(self.inserted)}


def test_seed_knowledge_parser_accepts_tenant_scope_and_dry_run():
    args = seed_knowledge.build_arg_parser().parse_args(
        ["--tenant-id", "default", "--kb-scope", "default", "--dry-run", "--enabled", "false", "--limit", "2"]
    )

    assert args.tenant_id == "default"
    assert args.kb_scope == "default"
    assert args.dry_run is True
    assert args.enabled == "false"
    assert args.limit == 2


def test_seed_knowledge_dry_run_does_not_write_repository():
    repository = FakeKnowledgeRepository()

    result = asyncio.run(seed_knowledge.seed_repository(repository, tenant_id="default", kb_scope="default", dry_run=True))

    assert result["dry_run"] is True
    assert result["documents"] >= 4
    assert result["inserted"] == 0
    assert result["duplicates"] == 0
    assert result["skipped"] == 0
    assert repository.inserted == []


def test_seed_knowledge_non_dry_run_calls_insert_idempotent():
    repository = FakeKnowledgeRepository()

    result = asyncio.run(seed_knowledge.seed_repository(repository, tenant_id="default", kb_scope="default"))

    assert result["dry_run"] is False
    assert result["inserted"] == result["documents"]
    assert len(repository.inserted) == result["documents"]
    assert {document["tenant_id"] for document in repository.inserted} == {"default"}
    assert {document["kb_scope"] for document in repository.inserted} == {"default"}


def test_seed_knowledge_documents_do_not_contain_backend_fact_answers():
    banned = ("已到账", "审核通过", "余额是", "订单已", "提款成功")

    for document in seed_knowledge.build_seed_documents(tenant_id="default", kb_scope="default"):
        content = document["content"]
        assert not any(marker in content for marker in banned)


def test_seed_knowledge_loads_source_file_and_skips_invalid_documents(tmp_path):
    source_file = tmp_path / "knowledge.json"
    source_file.write_text(
        json.dumps(
            [
                {"title": "充值方式说明", "content": "按页面提示完成充值。", "keywords": ["充值"]},
                {"title": "", "content": "missing title"},
                {"title": "missing content"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    repository = FakeKnowledgeRepository()

    result = asyncio.run(
        seed_knowledge.seed_repository(
            repository,
            tenant_id="default",
            kb_scope="default",
            source_file=str(source_file),
            enabled=False,
        )
    )

    assert result["documents"] == 3
    assert result["inserted"] == 1
    assert result["duplicates"] == 0
    assert result["skipped"] == 2
    assert repository.inserted[0]["enabled"] is False
    assert repository.inserted[0]["answer_blocks"] == [{"type": "text", "text": "按页面提示完成充值。"}]


def test_seed_knowledge_loads_multimodal_seed_file():
    repository = FakeKnowledgeRepository()

    result = asyncio.run(
        seed_knowledge.seed_repository(
            repository,
            tenant_id="default",
            kb_scope="default",
            source_file="data/knowledge/default_multimodal_faq_seed.json",
        )
    )

    assert result["documents"] == 4
    assert result["inserted"] == 4
    deposit = repository.inserted[0]
    assert deposit["question_aliases"]
    assert [block["type"] for block in deposit["answer_blocks"]] == ["image", "text", "buttons"]
    assert deposit["metadata_json"]["intent_id"] == "deposit_howto"


def test_seed_knowledge_counts_invalid_answer_blocks(tmp_path):
    source_file = tmp_path / "knowledge.json"
    source_file.write_text(
        json.dumps(
            [
                {
                    "title": "Bad block",
                    "content": "bad",
                    "answer_blocks": [{"type": "image"}],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    repository = FakeKnowledgeRepository()

    result = asyncio.run(
        seed_knowledge.seed_repository(
            repository,
            tenant_id="default",
            kb_scope="default",
            source_file=str(source_file),
        )
    )

    assert result["documents"] == 1
    assert result["invalid"] == 1
    assert repository.inserted == []
