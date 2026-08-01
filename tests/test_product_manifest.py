import json
from pathlib import Path

from shared.product_manifest import build_product_manifest, write_product_manifest


def _write(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_product_manifest_hash_links_sparse_training_and_outputs(tmp_path: Path) -> None:
    sparse = tmp_path / "sparse"
    for name in ("cameras.bin", "images.bin", "points3D.bin"):
        _write(sparse / name, name.encode())
    ortho = _write(tmp_path / "orthomosaic.tif", b"ortho")
    dsm = _write(tmp_path / "orthomosaic.height.tif", b"height")
    ply = _write(tmp_path / "final.ply", b"ply")
    trainer = _write(tmp_path / "trainer_run.json", b"{}")
    canary = _write(tmp_path / "canary_result.json", b'{"status":"passed"}')

    manifest = build_product_manifest(
        mission_id="mission-1",
        projected_crs="EPSG:32633",
        parameters={"gs_profile": "v1"},
        products={"orthomosaic": ortho, "dsm": dsm, "gaussian_model": ply},
        sparse_model_path=sparse,
        reports={},
        trainer_manifests=[trainer],
        qualification_manifests=[canary],
        git_revision="deadbeef",
        software_components={"renderer": ortho},
    )
    output = write_product_manifest(tmp_path / "product_manifest.json", manifest)
    loaded = json.loads(output.read_text())

    assert loaded["products"]["orthomosaic"]["sha256"]
    assert loaded["source_sparse_model"]["images.bin"]["sha256"]
    assert loaded["training_manifests"][0]["sha256"]
    assert loaded["qualification_manifests"][0]["sha256"]
    assert loaded["software"]["git_revision"] == "deadbeef"
    assert loaded["software"]["components"]["renderer"]["sha256"]
