"""
A module that loads pre-generated test vectors.

:ivar CHK_PATH: The path of the file containing CHK test vectors.

:ivar chk: The CHK test vectors.
"""

from yaml import safe_load
from pathlib import Path

DATA_PATH: Path = Path(__file__).parent / "test_vectors.yaml"

try:
    with DATA_PATH.open() as f:
        capabilities: dict[str, str] = safe_load(f)
except FileNotFoundError:
    capabilities = {}
