from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "drone-ai"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_production_overlay_requires_immutable_application_images() -> None:
    values = _read(CHART / "values-production.example.yaml")
    defaults = _read(CHART / "values.yaml")
    helpers = _read(CHART / "templates" / "_helpers.tpl")
    ci = _read(ROOT / ".github" / "workflows" / "ci.yml")

    assert "requireImmutableImages: false" in defaults
    assert "requireImmutableImages: true" in values
    assert values.count('tag: "REPLACE_GIT_SHA"') == 5
    assert 'eq $tag "latest"' in helpers
    assert "@sha256:" in helpers
    assert "Mutable application image tag found in the production render" in ci
    assert "REPLACE_GIT_SHA" in ci


def test_browser_upload_cors_exposes_multipart_etag() -> None:
    defaults = _read(CHART / "values.yaml")
    minio = _read(CHART / "templates" / "minio.yaml")
    external_script = _read(
        ROOT / "scripts" / "deploy" / "configure-s3-upload-cors.sh"
    )

    assert "browserUploadCors:" in defaults
    for source in (minio, external_script):
        assert "PUT" in source
        assert "ETag" in source
        assert "AllowedOrigin" in source
