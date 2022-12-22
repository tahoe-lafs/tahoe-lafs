from yaml import safe_load
from pathlib import Path

CHK_PATH = Path(__file__).parent / "_vectors_chk.yaml"

with CHK_PATH.open() as f:
    chk = safe_load(f)
