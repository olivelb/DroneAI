from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import promotion_manifest


def _record(tmp_path: Path, name: str, commit: str) -> dict[str, object]:
    sbom = tmp_path / f"{name}.cdx.json"
    report = tmp_path / f"{name}.trivy.json"
    sbom.write_text('{"bomFormat":"CycloneDX"}', encoding="utf-8")
    report.write_text('{"Results":[]}', encoding="utf-8")
    return promotion_manifest.image_record(
        name=name,
        image=f"ghcr.io/olivelb/droneai/{name}",
        digest="sha256:" + "a" * 64,
        source_commit=commit,
        sbom=sbom,
        vulnerability_report=report,
    )


def _evidence(tmp_path: Path, commit: str) -> tuple[Path, Path]:
    records = tmp_path / "records"
    records.mkdir()
    for name in promotion_manifest.REQUIRED_IMAGES:
        (records / f"{name}.image.json").write_text(
            json.dumps(_record(tmp_path, name, commit)),
            encoding="utf-8",
        )
    runs = [
        {
            "workflow_file": workflow,
            "headSha": commit,
            "conclusion": "success",
            "databaseId": index,
            "url": f"https://github.test/runs/{index}",
        }
        for index, workflow in enumerate(
            sorted(promotion_manifest.REQUIRED_WORKFLOWS), start=1
        )
    ]
    run_path = tmp_path / "qualification-runs.json"
    run_path.write_text(json.dumps(runs), encoding="utf-8")
    return records, run_path


def test_image_record_binds_digest_commit_and_evidence_hashes(tmp_path: Path) -> None:
    record = _record(tmp_path, "drone-dashboard-api", "1" * 40)

    assert record["reference"] == (
        "ghcr.io/olivelb/droneai/drone-dashboard-api@sha256:" + "a" * 64
    )
    assert record["source_commit"] == "1" * 40
    assert len(record["sbom"]["sha256"]) == 64  # type: ignore[index]
    assert record["vulnerability_report"]["fixable_high_critical_gate"] == "passed"  # type: ignore[index]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("image", "ghcr.io/owner/image:mutable", "tag-free"),
        ("digest", "sha256:short", "OCI SHA-256"),
        ("source_commit", "abc1234", "full lower-case"),
    ),
)
def test_image_record_rejects_mutable_or_abbreviated_identity(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    kwargs = {
        "name": "drone-ia",
        "image": "ghcr.io/olivelb/droneai/drone-ia",
        "digest": "sha256:" + "b" * 64,
        "source_commit": "2" * 40,
        "sbom": tmp_path / "sbom.json",
        "vulnerability_report": tmp_path / "trivy.json",
    }
    kwargs["sbom"].write_text("{}", encoding="utf-8")  # type: ignore[union-attr]
    kwargs["vulnerability_report"].write_text("{}", encoding="utf-8")  # type: ignore[union-attr]
    kwargs[field] = value

    with pytest.raises(ValueError, match=message):
        promotion_manifest.image_record(**kwargs)  # type: ignore[arg-type]


def test_manifest_requires_exact_images_and_successful_commit_scoped_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "3" * 40
    records, runs = _evidence(tmp_path, commit)
    monkeypatch.setattr(promotion_manifest, "ROOT", tmp_path)
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")

    manifest = promotion_manifest.assemble_manifest(
        records_directory=records,
        qualification_runs_path=runs,
        release_tag="v1.2.3",
        source_commit=commit,
        generated_at="2026-08-29T12:00:00+00:00",
    )

    assert manifest["release_tag"] == "v1.2.3"
    assert {image["name"] for image in manifest["images"]} == (  # type: ignore[union-attr]
        promotion_manifest.REQUIRED_IMAGES
    )
    assert len(manifest["qualification_runs"]) == 4  # type: ignore[arg-type]

    (records / "drone-ia.image.json").unlink()
    with pytest.raises(ValueError, match="exactly"):
        promotion_manifest.assemble_manifest(
            records_directory=records,
            qualification_runs_path=runs,
            release_tag="v1.2.3",
            source_commit=commit,
        )


def test_manifest_rejects_qualification_from_another_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "4" * 40
    records, runs = _evidence(tmp_path, commit)
    data = json.loads(runs.read_text(encoding="utf-8"))
    data[0]["headSha"] = "5" * 40
    runs.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(promotion_manifest, "ROOT", tmp_path)
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not successful for release"):
        promotion_manifest.assemble_manifest(
            records_directory=records,
            qualification_runs_path=runs,
            release_tag="v1.2.3",
            source_commit=commit,
        )
