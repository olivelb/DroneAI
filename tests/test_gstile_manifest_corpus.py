"""Cross-language admission fixtures, including allocation-free size boundaries."""
import json
from pathlib import Path

import pytest

from shared.gstile_manifest import (
    GSTILE_MAX_MANIFEST_BYTES,
    canonical_gstile_manifest_bytes,
    validate_gstile_manifest,
)

CORPUS = Path(__file__).parent / "fixtures/gstile/manifest-contracts"


@pytest.mark.parametrize("case", sorted(CORPUS.glob("*.json")), ids=lambda case: case.stem)
def test_common_manifest_contract(case: Path) -> None:
    payload = json.loads(case.read_text())
    if case.stem.endswith("-invalid"):
        with pytest.raises(ValueError):
            validate_gstile_manifest(payload)
    else:
        validate_gstile_manifest(payload)


def test_producer_rejects_oversized_manifest_before_publication() -> None:
    payload = json.loads((CORPUS / "leaf-valid.json").read_text())
    payload["padding"] = "x" * GSTILE_MAX_MANIFEST_BYTES
    with pytest.raises(ValueError, match="exceeds 8 MiB"):
        canonical_gstile_manifest_bytes(payload)
