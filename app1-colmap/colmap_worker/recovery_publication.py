"""Best-effort publication of COLMAP recovery and diagnostic artifacts."""

from __future__ import annotations

import os

from shared import storage

from . import runtime


def upload_optional_recovery_artifacts(
    *,
    geo_data_file: str,
    mission_s3_prefix: str,
    vol_id: str,
    db_path: str,
    sparse_path: str,
    workspace_dir: str,
    gcp_enabled: bool,
    dense_path: str,
    durable_checkpoint_dir: str,
    upload_count: int,
) -> tuple[int, bool]:
    """Publish optional recovery assets without invalidating final products."""

    gaussian_upload_complete = False
    try:
        if os.path.exists(geo_data_file):
            storage.upload_file(geo_data_file, f"{mission_s3_prefix}/geo_data.txt")
            upload_count += 1
            crs_file = f"{geo_data_file}.crs"
            if os.path.exists(crs_file):
                storage.upload_file(
                    crs_file,
                    f"{mission_s3_prefix}/geo_data.txt.crs",
                )
                upload_count += 1
            crs_metadata_file = f"{geo_data_file}.crs.json"
            if os.path.exists(crs_metadata_file):
                storage.upload_file(
                    crs_metadata_file,
                    f"{mission_s3_prefix}/geo_data.txt.crs.json",
                )
                upload_count += 1

        runtime.report_mission_progress(
            vol_id,
            "UPLOADING",
            92,
            log="Geo data uploaded",
        )

        if os.path.exists(db_path):
            storage.upload_file(db_path, f"{mission_s3_prefix}/colmap/database.db")
            upload_count += 1

        sparse_0_path = os.path.join(sparse_path, "0")
        if os.path.isdir(sparse_0_path):
            upload_count += storage.upload_directory(
                sparse_0_path,
                f"{mission_s3_prefix}/colmap/sparse/0/",
            )

        sparse_geo_path = os.path.join(workspace_dir, "sparse_geo")
        if not gcp_enabled and os.path.isdir(sparse_geo_path):
            upload_count += storage.upload_directory(
                sparse_geo_path,
                f"{mission_s3_prefix}/colmap/sparse_geo/",
            )

        runtime.report_mission_progress(
            vol_id,
            "UPLOADING",
            94,
            log="COLMAP sparse models uploaded",
        )

        if os.path.isdir(dense_path):
            dense_upload_count = storage.upload_directory(
                dense_path,
                f"{mission_s3_prefix}/dense/",
            )
            upload_count += dense_upload_count
            runtime.report_mission_progress(
                vol_id,
                "UPLOADING",
                96,
                log=f"Dense reconstruction uploaded ({dense_upload_count} files)",
            )

        if durable_checkpoint_dir and os.path.isdir(durable_checkpoint_dir):
            gaussian_upload_count = storage.upload_directory(
                durable_checkpoint_dir,
                f"{mission_s3_prefix}/gaussian/",
            )
            upload_count += gaussian_upload_count
            gaussian_upload_complete = True
            runtime.report_mission_progress(
                vol_id,
                "UPLOADING",
                98,
                log=(
                    "Gaussian models & checkpoints uploaded "
                    f"({gaussian_upload_count} files)"
                ),
            )

        runtime.report_mission_progress(
            vol_id,
            "UPLOADING",
            99,
            log=f"All artifacts uploaded to S3 ({upload_count} files total)",
        )
    except Exception as upload_error:
        runtime.report_mission_progress(
            vol_id,
            "UPLOADING",
            98,
            log=(
                "Warning: an optional recovery/debug artifact could not be "
                f"uploaded: {upload_error}"
            ),
        )
    return upload_count, gaussian_upload_complete
