import sys
from pathlib import Path

# The disclosure inference runtime is not a workspace package: its dependencies belong to
# the Modal image and must never enter a service image. Tests import it by path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
