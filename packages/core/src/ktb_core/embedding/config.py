import os

EMBEDDING_MODEL = os.environ.get("KTB_EMBEDDING_MODEL", "mlx-community/Qwen3-Embedding-4B-4bit-DWQ")
EMBEDDING_DIMENSIONS = int(os.environ.get("KTB_EMBEDDING_DIMENSIONS", "2000"))
EMBEDDING_MAX_TOKENS = int(os.environ.get("KTB_EMBEDDING_MAX_TOKENS", "16384"))
