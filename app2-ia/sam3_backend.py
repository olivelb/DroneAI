"""SAM3 model lifecycle and tile segmentation.

The Kafka entrypoint owns wiring only; this module keeps the heavyweight model
state and image-to-detection conversion behind one reusable boundary.
"""

from __future__ import annotations

import logging
import os
from typing import Any, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from detection_core import DetectionRecord, polygon_center
from shared.model_provenance import (
    build_model_manifest,
    immutable_revision,
    installed_versions,
    sha256_file,
)
from shared.sam3_capabilities import (
    SAM3_DEFAULT_MODEL_ID,
    SAM3_DEFAULT_MODEL_REVISION,
    SAM3_INFERENCE_BATCH_SIZE,
    SAM3_PROCESSOR_TARGET_SIZE,
)


JsonObject = dict[str, Any]


class Sam3Backend:
    """Load one immutable SAM3 revision lazily and run prompted inference."""

    def __init__(
        self,
        *,
        model_id: str | None = None,
        model_revision: str | None = None,
        default_prompt: str | None = None,
        mask_threshold: float | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.model_id = model_id or os.getenv(
            "SAM3_MODEL_ID",
            SAM3_DEFAULT_MODEL_ID,
        )
        configured_revision = model_revision or os.getenv(
            "SAM3_MODEL_REVISION",
            SAM3_DEFAULT_MODEL_REVISION,
        )
        self.model_revision = immutable_revision(configured_revision)
        self.default_prompt: str = (
            default_prompt
            or os.getenv(
                "SAM3_DEFAULT_PROMPT",
                "car",
            )
            or "car"
        )
        self.mask_threshold = (
            mask_threshold if mask_threshold is not None else float(os.getenv("SAM3_MASK_THRESHOLD", "0.5"))
        )
        self.device_type: str | None = None
        self.autocast_dtype: Any | None = None
        self.logger = logger or logging.getLogger("app2-ia.sam3")
        self._model: Any | None = None
        self._processor: Any | None = None
        self._artifact_sha256: str | None = None

    def _configure_runtime(self) -> None:
        if self.device_type is not None:
            return
        import torch

        self.device_type = "cuda" if torch.cuda.is_available() else "cpu"
        self.autocast_dtype = torch.bfloat16 if self.device_type == "cuda" else torch.float32

    def load_model(self) -> tuple[Any, Any]:
        from huggingface_hub import hf_hub_download
        from transformers import Sam3Model, Sam3Processor

        if self._model is not None and self._processor is not None:
            return self._model, self._processor

        self._configure_runtime()
        if self.device_type is None:
            raise RuntimeError("SAM3 runtime device is unavailable")
        self.logger.info(
            "Loading SAM3 model=%s revision=%s device=%s",
            self.model_id,
            self.model_revision,
            self.device_type,
        )
        artifact_path = hf_hub_download(
            repo_id=self.model_id,
            filename="model.safetensors",
            revision=self.model_revision,
        )
        self._artifact_sha256 = sha256_file(artifact_path)
        self._model = Sam3Model.from_pretrained(
            self.model_id,
            revision=self.model_revision,
        ).to(self.device_type)
        self._processor = Sam3Processor.from_pretrained(
            self.model_id,
            revision=self.model_revision,
        )
        image_processor = getattr(self._processor, "image_processor", None)
        processor_size = cast(
            JsonObject,
            getattr(image_processor, "size", {}),
        )
        if processor_size != {
            "height": SAM3_PROCESSOR_TARGET_SIZE,
            "width": SAM3_PROCESSOR_TARGET_SIZE,
        }:
            raise RuntimeError(
                "Pinned SAM3 processor target changed: expected "
                f"{SAM3_PROCESSOR_TARGET_SIZE}x{SAM3_PROCESSOR_TARGET_SIZE}, "
                f"got {processor_size!r}"
            )
        return self._model, self._processor

    def resolve_prompt(self, tile_info: JsonObject) -> str:
        explicit_prompt = str(tile_info.get("sam_prompt") or "").strip()
        if explicit_prompt:
            return explicit_prompt

        requested_classes = cast(list[str], tile_info.get("classes") or [])
        if requested_classes:
            return str(requested_classes[0]).strip()
        return self.default_prompt

    @staticmethod
    def contour_to_polygon(
        mask: NDArray[Any],
        fallback_box: list[list[float]],
    ) -> tuple[list[list[float]], float, float]:
        binary_mask = (mask > 0).astype(np.uint8)
        contours, _ = cv2.findContours(
            binary_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        if not contours:
            center_x, center_y = polygon_center(fallback_box)
            return fallback_box, center_x, center_y

        contour = max(contours, key=cv2.contourArea)
        perimeter = cv2.arcLength(contour, True)
        epsilon = max(1.0, 0.01 * perimeter)
        simplified = cv2.approxPolyDP(contour, epsilon, True)
        polygon = [[float(point[0][0]), float(point[0][1])] for point in simplified]
        if len(polygon) < 3:
            polygon = fallback_box

        moments = cv2.moments(contour)
        if moments["m00"]:
            center_x = float(moments["m10"] / moments["m00"])
            center_y = float(moments["m01"] / moments["m00"])
        else:
            center_x, center_y = polygon_center(polygon)
        return polygon, center_x, center_y

    def run(
        self,
        tile_path: str,
        prompt: str,
        requested_confidence: float,
    ) -> tuple[list[DetectionRecord], JsonObject]:
        import torch
        from PIL import Image

        model, processor = self.load_model()
        if self._artifact_sha256 is None or self.device_type is None:
            raise RuntimeError("SAM3 runtime metadata is unavailable after model loading")
        image = Image.open(tile_path).convert("RGB")
        inputs = processor(
            images=image,
            text=prompt,
            return_tensors="pt",
        ).to(self.device_type)

        with (
            torch.no_grad(),
            torch.autocast(
                device_type="cuda",
                dtype=self.autocast_dtype,
                enabled=self.device_type == "cuda",
            ),
        ):
            outputs = model(**inputs)

        result = cast(
            JsonObject,
            processor.post_process_instance_segmentation(
                outputs,
                threshold=requested_confidence,
                mask_threshold=self.mask_threshold,
                target_sizes=inputs.get("original_sizes").tolist(),
            )[0],
        )
        masks = result.get("masks")
        boxes = result.get("boxes")
        scores = result.get("scores")
        attempt: JsonObject = {
            "label": f"SAM3 prompt='{prompt}' conf={requested_confidence:.2f}",
            "model_manifest": build_model_manifest(
                backend="sam3",
                repository=self.model_id,
                revision=self.model_revision,
                artifact="model.safetensors",
                artifact_sha256=self._artifact_sha256,
                libraries=installed_versions("transformers", "torch"),
                runtime={
                    "device": self.device_type,
                    "autocast_dtype": str(self.autocast_dtype),
                    "inference_batch_size": SAM3_INFERENCE_BATCH_SIZE,
                    "processor_target_size": [
                        SAM3_PROCESSOR_TARGET_SIZE,
                        SAM3_PROCESSOR_TARGET_SIZE,
                    ],
                },
                inference={
                    "prompt": prompt,
                    "confidence": requested_confidence,
                    "mask_threshold": self.mask_threshold,
                },
            ),
        }
        if masks is None or boxes is None or scores is None or len(scores) == 0:
            return [], attempt

        detections: list[DetectionRecord] = []
        for mask, box, score in zip(masks, boxes, scores, strict=False):
            x1, y1, x2, y2 = [float(value) for value in box.tolist()]
            fallback_box = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
            polygon, center_x, center_y = self.contour_to_polygon(
                cast(NDArray[Any], mask.detach().cpu().numpy()),
                fallback_box,
            )
            detections.append(
                {
                    "polygon": polygon,
                    "center_x": center_x,
                    "center_y": center_y,
                    "confidence": float(score),
                    "class_id": 0,
                    "class_name": prompt,
                }
            )

        return detections, attempt
