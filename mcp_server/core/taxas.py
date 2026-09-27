from functools import cache
from pathlib import Path

import yaml

TAXAS = Path(__file__).resolve().parents[1] / "taxas.yaml"


@cache
def carregar() -> dict:
    return yaml.safe_load(TAXAS.read_text())
