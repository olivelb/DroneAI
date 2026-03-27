import fcntl
import logging
import math
import os
import shutil
import signal
import subprocess
import time

import numpy as np
import rasterio

from shared.pipeline_params import FUSION_CHUNK_BYTES_PER_PIXEL


logger = logging.getLogger("app1-colmap.runtime")


def has_valid_fused_output(fused_path, min_size_bytes=100_000):
    return os.path.exists(fused_path) and os.path.getsize(fused_path) >= min_size_bytes


def read_image_dimensions(path):
    try:
        with rasterio.open(path) as src:
            return src.width, src.height
    except Exception as error:
        logger.debug("Failed to read image dimensions for %s: %s", path, error)
        return None


def scale_dimensions(width, height, max_image_size):
    longest_side = max(width, height)
    if longest_side <= max_image_size:
        return width, height
    scale = max_image_size / float(longest_side)
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def load_fusion_entries(fusion_cfg_path):
    with open(fusion_cfg_path, "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def write_fusion_entries(fusion_cfg_path, entries):
    with open(fusion_cfg_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(entries))
        handle.write("\n")


def estimate_fusion_entry_bytes(dense_path, image_name, max_image_size):
    image_path = os.path.join(dense_path, "images", image_name)
    dims = read_image_dimensions(image_path)
    if not dims:
        fallback_pixels = max_image_size * max_image_size
        return fallback_pixels * FUSION_CHUNK_BYTES_PER_PIXEL
    width, height = dims
    scaled_width, scaled_height = scale_dimensions(width, height, max_image_size)
    return scaled_width * scaled_height * FUSION_CHUNK_BYTES_PER_PIXEL


def build_fusion_chunks(dense_path, entries, max_image_size, target_memory_gib):
    target_bytes = max(4.0, float(target_memory_gib)) * (1024 ** 3)
    chunks = []
    current_chunk = []
    current_bytes = 0

    for image_name in entries:
        estimated_bytes = estimate_fusion_entry_bytes(dense_path, image_name, max_image_size)
        if current_chunk and current_bytes + estimated_bytes > target_bytes:
            chunks.append(current_chunk)
            current_chunk = []
            current_bytes = 0
        current_chunk.append(image_name)
        current_bytes += estimated_bytes

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def merge_ply_chunks(chunk_paths, output_path):
    from plyfile import PlyData, PlyElement

    vertex_arrays = []
    base_dtype = None

    for chunk_path in chunk_paths:
        if not has_valid_fused_output(chunk_path):
            continue
        ply_data = PlyData.read(chunk_path)
        if "vertex" not in ply_data:
            continue
        vertex_data = np.array(ply_data["vertex"].data)
        if vertex_data.size == 0:
            continue
        if base_dtype is None:
            base_dtype = vertex_data.dtype
        elif vertex_data.dtype != base_dtype:
            vertex_data = vertex_data.astype(base_dtype, copy=False)
        vertex_arrays.append(vertex_data)

    if not vertex_arrays or base_dtype is None:
        raise RuntimeError("Chunked fusion produced no valid point chunks to merge.")

    merged_vertices = np.concatenate(vertex_arrays)
    PlyData([PlyElement.describe(merged_vertices, "vertex")], text=False).write(output_path)


def run_chunked_fusion(dense_path, fused_path, vol_id, params, effective_min_num_pixels, report_fn, run_command_fn):
    fusion_cfg_path = os.path.join(dense_path, "stereo", "fusion.cfg")
    original_entries = load_fusion_entries(fusion_cfg_path)
    if not original_entries:
        raise RuntimeError("fusion.cfg is empty; cannot run stereo fusion.")

    chunk_target_gib = float(params.get("fusion_chunk_target_memory_gib", "16"))
    max_image_size = int(float(params["fusion_max_image_size"]))
    chunks = build_fusion_chunks(dense_path, original_entries, max_image_size, chunk_target_gib)

    if len(chunks) <= 1:
        run_command_fn([
            "colmap", "stereo_fusion",
            "--workspace_path", dense_path,
            "--input_type", "geometric",
            "--output_path", fused_path,
            "--StereoFusion.max_image_size", params["fusion_max_image_size"],
            "--StereoFusion.cache_size", params["fusion_cache_size"],
            "--StereoFusion.min_num_pixels", str(effective_min_num_pixels),
        ], vol_id, "FUSION", 92)
        return

    chunk_dir = os.path.join(dense_path, "fusion_chunks")
    shutil.rmtree(chunk_dir, ignore_errors=True)
    os.makedirs(chunk_dir, exist_ok=True)
    chunk_outputs = []

    report_fn(
        vol_id,
        "FUSION",
        90,
        log=f"Chunked fusion enabled: {len(original_entries)} images split into {len(chunks)} chunks targeting ~{chunk_target_gib:.1f} GiB each.",
    )

    try:
        for index, chunk_entries in enumerate(chunks, start=1):
            chunk_output = os.path.join(chunk_dir, f"fused_chunk_{index:03d}.ply")
            write_fusion_entries(fusion_cfg_path, chunk_entries)
            chunk_progress = 92 + math.floor((index - 1) * 5 / max(len(chunks), 1))
            report_fn(
                vol_id,
                "FUSION",
                chunk_progress,
                log=f"Running fusion chunk {index}/{len(chunks)} with {len(chunk_entries)} reference images.",
            )
            run_command_fn([
                "colmap", "stereo_fusion",
                "--workspace_path", dense_path,
                "--input_type", "geometric",
                "--output_path", chunk_output,
                "--StereoFusion.max_image_size", params["fusion_max_image_size"],
                "--StereoFusion.cache_size", params["fusion_cache_size"],
                "--StereoFusion.min_num_pixels", str(effective_min_num_pixels),
            ], vol_id, "FUSION", chunk_progress)
            if not has_valid_fused_output(chunk_output):
                chunk_size = os.path.getsize(chunk_output) if os.path.exists(chunk_output) else 0
                raise RuntimeError(
                    f"Fusion chunk {index}/{len(chunks)} did not produce a valid PLY (size={chunk_size} bytes)."
                )
            chunk_outputs.append(chunk_output)

        report_fn(vol_id, "FUSION", 97, log=f"Merging {len(chunk_outputs)} fusion chunks into fused.ply...")
        merge_ply_chunks(chunk_outputs, fused_path)
    finally:
        write_fusion_entries(fusion_cfg_path, original_entries)


def run_command(command, vol_id, step, base_progress, report_fn, cancel_check_fn=None):
    heartbeat_interval = float(os.getenv("COLMAP_COMMAND_HEARTBEAT_SECONDS", "15"))
    report_fn(
        vol_id,
        step,
        base_progress,
        log=f"Executing: {' '.join(command)}",
        details={"event": "command_started", "command": command},
    )
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    start_time = time.monotonic()
    last_heartbeat_time = start_time

    flags = fcntl.fcntl(process.stdout, fcntl.F_GETFL)
    fcntl.fcntl(process.stdout, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    while True:
        if cancel_check_fn:
            cancel_check_fn(process)

        try:
            line = process.stdout.readline()
            if line:
                clean_line = line.strip()
                if clean_line:
                    report_fn(vol_id, step, base_progress, log=clean_line)
            else:
                if process.poll() is not None:
                    break
                now = time.monotonic()
                if heartbeat_interval > 0 and now - last_heartbeat_time >= heartbeat_interval:
                    elapsed_seconds = int(now - start_time)
                    report_fn(
                        vol_id,
                        step,
                        base_progress,
                        log=f"Still running after {elapsed_seconds}s: {' '.join(command)}",
                        details={"event": "command_heartbeat", "command": command, "elapsed_seconds": elapsed_seconds},
                    )
                    last_heartbeat_time = now
                time.sleep(0.1)
        except IOError:
            if process.poll() is not None:
                break
            now = time.monotonic()
            if heartbeat_interval > 0 and now - last_heartbeat_time >= heartbeat_interval:
                elapsed_seconds = int(now - start_time)
                report_fn(
                    vol_id,
                    step,
                    base_progress,
                    log=f"Still running after {elapsed_seconds}s: {' '.join(command)}",
                    details={"event": "command_heartbeat", "command": command, "elapsed_seconds": elapsed_seconds},
                )
                last_heartbeat_time = now
            time.sleep(0.1)

    # Restore blocking mode and drain any remaining output
    fcntl.fcntl(process.stdout, fcntl.F_SETFL, flags)
    for line in process.stdout:
        clean_line = line.strip()
        if clean_line:
            report_fn(vol_id, step, base_progress, log=clean_line)

    return_code = process.wait()
    if return_code != 0:
        if cancel_check_fn:
            try:
                cancel_check_fn(None)
            except Exception:
                report_fn(vol_id, step, base_progress, details={"event": "command_cancelled", "command": command, "return_code": return_code})
                raise
        report_fn(vol_id, step, base_progress, details={"event": "command_failed", "command": command, "return_code": return_code})
        if return_code < 0:
            signal_number = -return_code
            try:
                signal_name = signal.Signals(signal_number).name
            except ValueError:
                signal_name = f"SIG{signal_number}"
            hint = " Likely causes: OOM kill, pod eviction, or manual termination." if signal_name == "SIGKILL" else ""
            raise RuntimeError(f"Command '{' '.join(command)}' died with {signal_name}.{hint}")
        raise subprocess.CalledProcessError(return_code, command)
    report_fn(vol_id, step, base_progress, details={"event": "command_finished", "command": command, "return_code": 0})