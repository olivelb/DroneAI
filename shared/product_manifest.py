"""Hash-linked provenance manifest for published mapping products."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from shared.checksums import sha256_file


def describe_file(path: str | Path, *, role: str) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file() or source.stat().st_size <= 0:
        raise FileNotFoundError(f"required {role} artifact is missing: {source}")
    return {
        "role": role,
        "name": source.name,
        "size_bytes": source.stat().st_size,
        "sha256": sha256_file(source),
    }


def build_product_manifest(
    *,
    mission_id: str,
    projected_crs: str,
    parameters: dict[str, Any],
    products: dict[str, str | Path],
    sparse_model_path: str | Path,
    reports: dict[str, str | Path | None],
    trainer_manifests: Iterable[str | Path] = (),
    qualification_manifests: Iterable[str | Path] = (),
    git_revision: str | None = None,
    software_components: dict[str, str | Path] | None = None,
) -> dict[str, Any]:
    """Build a deterministic file-integrity graph for one published product."""

    sparse_root = Path(sparse_model_path)
    sparse_files = {
        name: describe_file(sparse_root / name, role=f"sparse-{name}")
        for name in ("cameras.bin", "images.bin", "points3D.bin")
    }
    product_files = {
        name: describe_file(path, role=name) for name, path in products.items()
    }
    report_files = {
        name: describe_file(path, role=name)
        for name, path in reports.items()
        if path is not None and Path(path).is_file()
    }
    training = [
        describe_file(path, role="dronegs-training-manifest")
        for path in sorted(Path(path) for path in trainer_manifests)
    ]
    qualification = [
        describe_file(path, role="dronegs-qualification-manifest")
        for path in sorted(Path(path) for path in qualification_manifests)
    ]
    components = {
        name: describe_file(path, role=f"software-{name}")
        for name, path in (software_components or {}).items()
    }
    return {
        "schema_version": 1,
        "mission_id": str(mission_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "software": {
            "git_revision": git_revision,
            "manifest_contract": "droneai-product-manifest-v1",
            "components": components,
        },
        "coordinate_reference_system": str(projected_crs),
        "processing_parameters": parameters,
        "source_sparse_model": sparse_files,
        "reports": report_files,
        "training_manifests": training,
        "qualification_manifests": qualification,
        "products": product_files,
    }


def write_product_manifest(
    path: str | Path,
    manifest: dict[str, Any],
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output)
    return output
