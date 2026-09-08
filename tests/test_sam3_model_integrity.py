from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

APP2_ROOT = Path(__file__).parents[1] / "app2-ia"
if str(APP2_ROOT) not in sys.path:
    sys.path.insert(0, str(APP2_ROOT))

from sam3_backend import Sam3Backend  # noqa: E402


def test_protected_environment_requires_an_independent_artifact_hash(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "production")
    monkeypatch.delenv("SAM3_MODEL_SHA256", raising=False)

    with pytest.raises(RuntimeError, match="required in protected"):
        Sam3Backend()


def test_expected_artifact_hash_must_be_canonical():
    with pytest.raises(ValueError, match="64-character"):
        Sam3Backend(model_sha256="not-a-sha")


def test_downloaded_artifact_is_verified_before_deserialization(monkeypatch, tmp_path):
    artifact = tmp_path / "model.safetensors"
    artifact.write_bytes(b"trusted-sam3-weights")
    expected = hashlib.sha256(artifact.read_bytes()).hexdigest()
    model_calls: list[bool] = []

    class FakeModel:
        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            model_calls.append(True)
            return cls()

        def to(self, _device):
            return self

    class FakeProcessor:
        image_processor = SimpleNamespace(size={"height": 1008, "width": 1008})

        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            return cls()

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(hf_hub_download=lambda **_kwargs: str(artifact)),
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(Sam3Model=FakeModel, Sam3Processor=FakeProcessor),
    )
    backend = Sam3Backend(model_sha256="0" * 64)
    backend.device_type = "cpu"

    with pytest.raises(RuntimeError, match="does not match"):
        backend.load_model()
    assert model_calls == []

    verified = Sam3Backend(model_sha256=expected)
    verified.device_type = "cpu"
    model, processor = verified.load_model()
    assert isinstance(model, FakeModel)
    assert isinstance(processor, FakeProcessor)


@pytest.mark.parametrize("directory,sha", [("relative", "a" * 64), ("/opt/models/sam3", "")])
def test_local_snapshot_requires_absolute_path_and_independent_hash(directory, sha):
    with pytest.raises(ValueError, match="Local SAM3 requires"):
        Sam3Backend(model_directory=directory, model_sha256=sha)


def test_local_snapshot_never_downloads_and_verifies_before_loading(monkeypatch, tmp_path):
    artifact = tmp_path / "model.safetensors"
    artifact.write_bytes(b"synthetic trusted offline weights")
    expected = hashlib.sha256(artifact.read_bytes()).hexdigest()
    calls = []
    class FakeModel:
        @classmethod
        def from_pretrained(cls, source, **kwargs):
            calls.append(("model", source, kwargs))
            return cls()
        def to(self, _device):
            return self
    class FakeProcessor:
        image_processor = SimpleNamespace(size={"height": 1008, "width": 1008})
        @classmethod
        def from_pretrained(cls, source, **kwargs):
            calls.append(("processor", source, kwargs))
            return cls()
    def no_download(**kwargs):
        pytest.fail("offline loader attempted a Hugging Face download")
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(hf_hub_download=no_download))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(Sam3Model=FakeModel, Sam3Processor=FakeProcessor))
    backend = Sam3Backend(model_directory=str(tmp_path), model_sha256="0" * 64)
    backend.device_type = "cpu"
    with pytest.raises(RuntimeError, match="does not match"):
        backend.load_model()
    assert calls == []
    backend = Sam3Backend(model_directory=str(tmp_path), model_sha256=expected)
    backend.device_type = "cpu"
    backend.load_model()
    assert calls == [("model", str(tmp_path), {"local_files_only": True}),
                     ("processor", str(tmp_path), {"local_files_only": True})]
    artifact.unlink()
    missing = Sam3Backend(model_directory=str(tmp_path), model_sha256=expected)
    missing.device_type = "cpu"
    with pytest.raises(FileNotFoundError):
        missing.load_model()
    assert len(calls) == 2
