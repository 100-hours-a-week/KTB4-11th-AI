import json
import os
import runpy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class InvalidRequest(ValueError):
    pass


class IdentityMismatch(Exception):
    pass


@dataclass(frozen=True)
class DocumentGroup:
    document_id: str
    chunks: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class Manifest:
    transform_version: str
    max_tokens: int
    embedding: dict[str, Any]
    reranker: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        frozen = os.environ.get("DISCLOSURE_MODEL_MANIFEST")
        data = json.loads(frozen) if frozen else runpy.run_path(str(path))["DISCLOSURE_MODELS"]
        return cls(
            transform_version=data["transform_version"],
            max_tokens=data["max_tokens"],
            embedding=data["embedding"],
            reranker=data["reranker"],
        )

    def embedding_identity(self) -> dict[str, Any]:
        return {
            **self.embedding,
            "transform_version": self.transform_version,
            "max_tokens": self.max_tokens,
        }

    def reranker_identity(self) -> dict[str, Any]:
        return {**self.reranker, "max_tokens": self.max_tokens}


def require_identity(claimed: dict[str, Any] | None, loaded: dict[str, Any]) -> None:
    if claimed is None:
        raise IdentityMismatch("identity is required on every inference request")
    if not isinstance(claimed, dict):
        raise InvalidRequest("identity must be an object")
    differing = sorted(
        (field for field, value in loaded.items() if claimed.get(field, _MISSING) != value)
    )
    if differing:
        raise IdentityMismatch(f"identity does not match the loaded model: {', '.join(differing)}")


_MISSING = object()


def document_groups(documents: list[dict[str, Any]]) -> list[DocumentGroup]:
    if not isinstance(documents, list) or not documents:
        raise InvalidRequest("documents must be a nonempty list")
    groups = []
    seen: set[int] = set()
    for document in documents:
        if not isinstance(document, dict):
            raise InvalidRequest("document must be an object")
        document_id = document.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            raise InvalidRequest("document_id must be a nonempty string")
        chunks = document.get("chunks")
        if not isinstance(chunks, list):
            raise InvalidRequest("chunks must be a list")
        if not chunks:
            raise InvalidRequest(f"document {document_id} has no chunks")
        pairs = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                raise InvalidRequest("chunk must be an object")
            chunk_id = chunk.get("chunk_id")
            if type(chunk_id) is not int or chunk_id < 0:
                raise InvalidRequest("chunk_id must be a nonnegative integer")
            text = chunk.get("text")
            if not isinstance(text, str) or not text.strip():
                raise InvalidRequest(f"chunk {chunk_id} is empty")
            if chunk_id in seen:
                raise InvalidRequest(f"duplicate chunk_id {chunk_id}")
            seen.add(chunk_id)
            pairs.append((chunk_id, text))
        groups.append(DocumentGroup(document_id=document_id, chunks=tuple(pairs)))
    return groups


def reranker_prompt(query: str, text: str) -> str:
    return (
        "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query "
        'and the Instruct provided. Note that the answer can only be "yes" or "no".'
        "<|im_end|>\n<|im_start|>user\n"
        "<Instruct>: Retrieve disclosure passages that answer the financial question.\n"
        f"<Query>: {query}\n<Document>: {text}"
        "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )


def request_query(payload: dict) -> str:
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        raise InvalidRequest("query is empty or not a string")
    return query


def request_candidates(payload: dict) -> list[dict]:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise InvalidRequest("candidates must not be empty")
    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise InvalidRequest("candidate must be an object")
        chunk_id = candidate.get("chunk_id")
        if type(chunk_id) is not int or chunk_id < 0 or chunk_id in seen:
            raise InvalidRequest("candidate IDs must be unique nonnegative integers")
        seen.add(chunk_id)
        text = candidate.get("text")
        if not isinstance(text, str) or not text.strip():
            raise InvalidRequest("candidate text must be a nonempty string")
    return candidates
