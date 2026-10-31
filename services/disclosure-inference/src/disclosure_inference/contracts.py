import runpy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
        data = runpy.run_path(str(path))["DISCLOSURE_MODELS"]
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
    differing = sorted(
        (field for field, value in loaded.items() if claimed.get(field, _MISSING) != value)
    )
    if differing:
        raise IdentityMismatch(f"identity does not match the loaded model: {', '.join(differing)}")


_MISSING = object()


def document_groups(documents: list[dict[str, Any]]) -> list[DocumentGroup]:
    groups = []
    seen: set[int] = set()
    for document in documents:
        document_id = document["document_id"]
        chunks = document["chunks"]
        if not chunks:
            raise ValueError(f"document {document_id} has no chunks")
        pairs = []
        for chunk in chunks:
            chunk_id = chunk["chunk_id"]
            text = chunk["text"]
            if not text.strip():
                raise ValueError(f"chunk {chunk_id} is empty")
            if chunk_id in seen:
                raise ValueError(f"duplicate chunk_id {chunk_id}")
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
