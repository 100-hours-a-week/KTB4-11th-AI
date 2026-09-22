import sys
from pathlib import Path

# Add workspace src directories to sys.path
# Workaround for macOS UF_HIDDEN flag issue with uv's .pth files
workspace_root = Path(__file__).parent.parent.parent.parent
for src_dir in [
    workspace_root / "services" / "market-collector" / "src",
    workspace_root / "packages" / "core" / "src",
    workspace_root / "packages" / "market-analyzer" / "src",
]:
    path_str = str(src_dir)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
