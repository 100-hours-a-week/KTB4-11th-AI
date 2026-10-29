import json
from pathlib import Path

import pytest
from contracts import IdentityMismatch, Manifest, document_groups, require_identity

MANIFEST = Path(__file__).resolve().parents[4] / "packages/core/src/ktb_core/disclosure_models.json"


@pytest.fixture
def manifest():
    return Manifest.load(MANIFEST)


def test_the_manifest_pins_every_identity_field(manifest):
    identity = manifest.embedding_identity()

    assert identity["base_repo"] == "perplexity-ai/pplx-embed-v2-context-9b-preview"
    assert identity["base_revision"] == "e2c06a779d45e8db53066735be1b98ffe3412ca3"
    assert identity["adapter_repo"] == "Dongju-00/stockspoon-pplx-embed-v2-context-9b"
    assert identity["adapter_revision"] == "6949b167a2cf9caa92a2e765814a1db008a7bc7e"
    assert identity["adapter_sha256"].startswith("1f5d4367")
    assert identity["dimensions"] == 1024
    assert identity["transform_version"] == "context-int8-1024-v1"
    assert identity["max_tokens"] == 16384


def test_the_reranker_identity_reports_quantisation_not_dimensions(manifest):
    identity = manifest.reranker_identity()

    assert identity["quantization"] == "nf4"
    assert identity["prompt_version"] == "qwen3-reranker-financial-chat-v2"
    assert "dimensions" not in identity


def test_a_matching_identity_is_accepted(manifest):
    require_identity(manifest.embedding_identity(), manifest.embedding_identity())


def test_an_absent_identity_is_rejected(manifest):
    with pytest.raises(IdentityMismatch, match="identity is required"):
        require_identity(None, manifest.embedding_identity())


@pytest.mark.parametrize(
    "field", ["adapter_revision", "adapter_sha256", "dimensions", "transform_version"]
)
def test_any_differing_identity_field_is_rejected(manifest, field):
    claimed = {**manifest.embedding_identity(), field: "changed"}

    with pytest.raises(IdentityMismatch, match=field):
        require_identity(claimed, manifest.embedding_identity())


def test_an_identity_missing_a_field_is_rejected(manifest):
    claimed = dict(manifest.embedding_identity())
    del claimed["adapter_sha256"]

    with pytest.raises(IdentityMismatch, match="adapter_sha256"):
        require_identity(claimed, manifest.embedding_identity())


def test_document_groups_keep_each_document_separate():
    documents = [
        {
            "document_id": "00001.xml",
            "chunks": [{"chunk_id": 1, "text": "가"}, {"chunk_id": 2, "text": "나"}],
        },
        {"document_id": "00002.xml", "chunks": [{"chunk_id": 3, "text": "다"}]},
    ]

    groups = document_groups(documents)

    assert [group.document_id for group in groups] == ["00001.xml", "00002.xml"]
    assert [[chunk_id for chunk_id, _ in group.chunks] for group in groups] == [[1, 2], [3]]


def test_a_duplicate_chunk_id_is_rejected():
    documents = [
        {"document_id": "a", "chunks": [{"chunk_id": 1, "text": "가"}]},
        {"document_id": "b", "chunks": [{"chunk_id": 1, "text": "나"}]},
    ]

    with pytest.raises(ValueError, match="duplicate chunk_id"):
        document_groups(documents)


def test_a_document_without_chunks_is_rejected():
    with pytest.raises(ValueError, match="no chunks"):
        document_groups([{"document_id": "a", "chunks": []}])


def test_blank_chunk_text_is_rejected():
    with pytest.raises(ValueError, match="empty"):
        document_groups([{"document_id": "a", "chunks": [{"chunk_id": 1, "text": "   "}]}])


def test_the_manifest_on_disk_is_valid_json():
    assert set(json.loads(MANIFEST.read_text())) == {
        "transform_version",
        "max_tokens",
        "embedding",
        "reranker",
    }


def test_reranker_uses_the_evaluated_chat_template():
    from contracts import reranker_prompt

    prompt = reranker_prompt("소송 위험", "회사: 삼성전자\n본문:\n소송이 진행 중입니다.")
    assert prompt.startswith(
        "<|im_start|>system\nJudge whether the Document meets the requirements"
    )
    assert (
        "<Instruct>: Retrieve disclosure passages that answer the financial question.\n"
        "<Query>: 소송 위험\n<Document>: 회사: 삼성전자" in prompt
    )
    assert prompt.endswith("<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n")
