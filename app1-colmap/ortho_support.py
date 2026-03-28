import hashlib
import json
import logging
import math
import os
import shutil

import numpy as np
import rasterio
from rasterio.transform import from_origin

from runtime_support import load_fusion_entries


logger = logging.getLogger("app1-colmap.ortho")


def apply_iterative_gap_fill(img, passes=3):
    from scipy.ndimage import maximum_filter, uniform_filter

    mask = np.any(img > 0, axis=2).astype(np.uint8)
    fill_before = np.count_nonzero(mask)
    for _ in range(passes):
        dilated_mask = maximum_filter(mask, size=3)
        new_pixels = (dilated_mask > 0) & (mask == 0)
        if not np.any(new_pixels):
            break
        mask_f = mask.astype(np.float32)
        neighbor_count = uniform_filter(mask_f, size=3, mode="constant") * 9
        for channel_index in range(3):
            channel = img[:, :, channel_index].astype(np.float32)
            neighbor_sum = uniform_filter(channel * mask_f, size=3, mode="constant") * 9
            fill_vals = np.zeros_like(channel, dtype=np.float32)
            np.divide(neighbor_sum, neighbor_count, out=fill_vals, where=neighbor_count > 0)
            fill_vals = fill_vals.astype(np.uint8)
            img[:, :, channel_index] = np.where(new_pixels, fill_vals, img[:, :, channel_index])
        mask = dilated_mask
    fill_after = np.count_nonzero(mask)
    return img, fill_before, fill_after


def generate_ortho_from_ply(ply_path, ortho_file, utm_crs, vol_id, report_fn, transform_file=None, resolution=0.05):
    from plyfile import PlyData

    report_fn(vol_id, "ORTHO", 95, log=f"Generating orthomosaic from point cloud (PLY projection) at {resolution}m/px...")
    plydata = PlyData.read(ply_path)
    x = np.array(plydata["vertex"]["x"], dtype=np.float64)
    y = np.array(plydata["vertex"]["y"], dtype=np.float64)
    z = np.array(plydata["vertex"]["z"], dtype=np.float64)
    r = np.array(plydata["vertex"]["red"])
    g = np.array(plydata["vertex"]["green"])
    b = np.array(plydata["vertex"]["blue"])

    if len(x) == 0:
        raise ValueError("Point cloud is empty (0 vertices).")

    if transform_file and os.path.exists(transform_file):
        with open(transform_file, "r", encoding="utf-8") as handle:
            tdata = json.load(handle)
        rotation = np.array(tdata["R"])
        scale = tdata["scale"]
        translation = np.array(tdata["t"])
        xyz = np.column_stack([x, y, z])
        xyz_geo = (scale * (rotation @ xyz.T) + translation[:, np.newaxis]).T
        x, y, z = xyz_geo[:, 0], xyz_geo[:, 1], xyz_geo[:, 2]
        report_fn(vol_id, "ORTHO", 95, log=f"Applied geo-transform in float64 ({len(x)} pts)")

    min_x, max_x = float(np.min(x)), float(np.max(x))
    min_y, max_y = float(np.min(y)), float(np.max(y))

    width_m = max_x - min_x
    height_m = max_y - min_y

    max_dim = max(width_m, height_m)
    if max_dim > 0 and (max_dim / resolution) > 15000:
        resolution = max_dim / 15000.0
        report_fn(vol_id, "ORTHO", 95, log=f"Ortho dimension too large. Adjusted resolution to {resolution:.3f} m/px")
    elif max_dim == 0:
        resolution = 0.05

    width = max(1, int(np.ceil(width_m / resolution)))
    height = max(1, int(np.ceil(height_m / resolution)))
    img = np.zeros((height, width, 3), dtype=np.uint8)

    px = ((x - min_x) / resolution).astype(np.int32)
    py = ((max_y - y) / resolution).astype(np.int32)
    px = np.clip(px, 0, width - 1)
    py = np.clip(py, 0, height - 1)

    order = np.argsort(z)
    img[py[order], px[order], 0] = r[order]
    img[py[order], px[order], 1] = g[order]
    img[py[order], px[order], 2] = b[order]

    img, fill_before, fill_after = apply_iterative_gap_fill(img)
    report_fn(vol_id, "ORTHO", 97, log=f"Gap-fill: {100 * fill_before / (width * height):.1f}% -> {100 * fill_after / (width * height):.1f}%")

    img_data = img.transpose(2, 0, 1)
    transform = from_origin(min_x, max_y, resolution, resolution)
    crs_to_use = utm_crs if utm_crs else "EPSG:4326"

    with rasterio.open(
        ortho_file,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=3,
        dtype=img_data.dtype,
        crs=crs_to_use,
        transform=transform,
    ) as dst:
        dst.write(img_data)

    report_fn(vol_id, "ORTHO", 98, log=f"PLY ortho written: {width}x{height}px @ {resolution:.3f}m/px")


def generate_ortho_from_mesh(
    dense_path,
    ortho_file,
    utm_crs,
    vol_id,
    report_fn,
    run_command_fn,
    cancel_check_fn=None,
    workspace_dir=None,
    params=None,
):
    from PIL import Image as PILImage
    from plyfile import PlyData

    params = params or {}
    fused_ply = os.path.join(dense_path, "fused.ply")
    meshed_ply = os.path.join(dense_path, "meshed-poisson.ply")
    auto_texturing_face_threshold = 12_000_000
    auto_vertex_color_image_threshold = 300
    auto_vertex_color_face_threshold = 8_000_000

    if not os.path.exists(meshed_ply):
        report_fn(vol_id, "MESHING", 93, log="Running Poisson surface reconstruction...")
        run_command_fn([
            "colmap", "poisson_mesher",
            "--input_path", fused_ply,
            "--output_path", meshed_ply,
        ], vol_id, "MESHING", 93)
    else:
        report_fn(vol_id, "MESHING", 93, log="Poisson mesh found, skipping meshing.")

    textured_dir = os.path.join(dense_path, "textured")
    textured_mesh = os.path.join(textured_dir, "mesh.ply")
    texturing_metadata_path = os.path.join(textured_dir, "texturing_metadata.json")

    def check_cancel():
        if cancel_check_fn:
            cancel_check_fn()

    def build_texturing_metadata():
        relevant_params = {
            key: params.get(key)
            for key in (
                "texturing_image_max_size",
                "ortho_mesh_simplify_ratio",
                "ortho_mesh_simplify_max_faces",
                "ortho_mesh_simplify_on_retry",
                "ortho_mesh_simplify_retry_ratio",
                "ortho_texture_scale_factor",
                "ortho_texturing_apply_color_correction",
                "ortho_texturing_num_threads",
            )
        }
        metadata_payload = json.dumps(relevant_params, sort_keys=True)
        return {
            "version": 1,
            "params": relevant_params,
            "params_sha1": hashlib.sha1(metadata_payload.encode("utf-8")).hexdigest(),
        }

    def load_texturing_metadata():
        if not os.path.exists(texturing_metadata_path):
            return None
        try:
            with open(texturing_metadata_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("Failed to read texturing metadata %s: %s", texturing_metadata_path, error)
            return None

    def write_texturing_metadata(metadata):
        os.makedirs(textured_dir, exist_ok=True)
        with open(texturing_metadata_path, "w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2, sort_keys=True)

    def invalidate_texturing_outputs(reason):
        report_fn(vol_id, "TEXTURING", 94, log=f"Invalidating existing textured mesh: {reason}")
        shutil.rmtree(textured_dir, ignore_errors=True)

    current_texturing_metadata = build_texturing_metadata()

    def count_texturing_images():
        fusion_cfg_path = os.path.join(dense_path, "stereo", "fusion.cfg")
        if os.path.exists(fusion_cfg_path):
            try:
                return len(load_fusion_entries(fusion_cfg_path))
            except OSError:
                pass
        images_dir = os.path.join(dense_path, "images")
        if os.path.isdir(images_dir):
            return len([
                name for name in os.listdir(images_dir)
                if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
            ])
        return 0

    def parse_bool(value, default=False):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def read_ply_element_count(ply_path, element_name):
        with open(ply_path, "rb") as handle:
            while True:
                raw_line = handle.readline()
                if not raw_line:
                    break
                line = raw_line.decode("ascii", errors="ignore").strip()
                if line.startswith(f"element {element_name} "):
                    return int(line.split()[-1])
                if line == "end_header":
                    break
        return 0

    def scale_face_cap(face_cap, ratio):
        face_cap = int(face_cap)
        if face_cap <= 0:
            return 0
        return max(1, int(math.floor(face_cap * ratio)))

    def format_ratio_token(value):
        return f"{float(value):.4f}".replace(".", "p")

    def prepare_texturing_workspace():
        requested_max_size = int(float(params.get("texturing_image_max_size", "0") or 0))
        if requested_max_size <= 0:
            return dense_path

        sparse_dir = os.path.join(dense_path, "sparse")
        images_dir = os.path.join(dense_path, "images")
        if not os.path.isdir(sparse_dir) or not os.path.isdir(images_dir):
            raise RuntimeError("Dense workspace is missing sparse/ or images/ needed for texturing")

        try:
            import pycolmap
        except ImportError as error:
            raise RuntimeError(f"Reduced-resolution texturing requires pycolmap and Pillow in the worker image: {error}")

        texturing_workspace = os.path.join(dense_path, f"texturing_workspace_{requested_max_size}")
        texturing_sparse_dir = os.path.join(texturing_workspace, "sparse")
        texturing_images_dir = os.path.join(texturing_workspace, "images")

        reconstruction = pycolmap.Reconstruction(sparse_dir)
        camera_sizes = {}
        resized_any = False

        for camera_id in reconstruction.cameras.keys():
            camera = reconstruction.camera(camera_id)
            longest_side = max(int(camera.width), int(camera.height))
            if longest_side > requested_max_size:
                camera.rescale(requested_max_size / float(longest_side))
                resized_any = True
            camera_sizes[int(camera_id)] = (int(camera.width), int(camera.height))

        if not resized_any:
            report_fn(
                vol_id,
                "TEXTURING",
                94,
                log=(
                    f"Texturing image max size {requested_max_size}px does not reduce the current dense workspace images. "
                    "Using the existing dense workspace for texturing."
                ),
            )
            return dense_path

        shutil.rmtree(texturing_workspace, ignore_errors=True)
        os.makedirs(texturing_sparse_dir, exist_ok=True)
        os.makedirs(texturing_images_dir, exist_ok=True)

        report_fn(
            vol_id,
            "TEXTURING",
            94,
            log=f"Preparing reduced-resolution texturing workspace with max image size {requested_max_size}px...",
        )
        reconstruction.write(texturing_sparse_dir)
        reconstruction.create_image_dirs(texturing_images_dir)

        for image_id in reconstruction.reg_image_ids():
            image = reconstruction.image(image_id)
            source_path = os.path.join(images_dir, image.name)
            target_path = os.path.join(texturing_images_dir, image.name)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            target_size = camera_sizes[int(image.camera_id)]

            with PILImage.open(source_path) as bitmap:
                if bitmap.size != target_size:
                    bitmap = bitmap.resize(target_size, PILImage.Resampling.LANCZOS)
                save_kwargs = {}
                if os.path.splitext(target_path)[1].lower() in (".jpg", ".jpeg"):
                    save_kwargs.update({"quality": 95, "optimize": True})
                bitmap.save(target_path, **save_kwargs)

        return texturing_workspace

    def resolve_mesh_variant(base_mesh_path, simplify_ratio, max_faces, suffix):
        base_face_count = read_ply_element_count(base_mesh_path, "face")
        if base_face_count <= 0:
            raise RuntimeError(f"Mesh {base_mesh_path} has no faces; cannot simplify for texturing")

        target_face_count = base_face_count
        simplify_ratio = max(0.05, min(1.0, float(simplify_ratio)))
        if simplify_ratio < 0.999:
            target_face_count = min(target_face_count, max(1, int(math.floor(base_face_count * simplify_ratio))))

        max_faces = int(max_faces)
        if max_faces > 0:
            target_face_count = min(target_face_count, max_faces)

        if target_face_count >= base_face_count:
            return base_mesh_path, base_face_count, base_face_count

        ratio_token = format_ratio_token(simplify_ratio)
        face_cap_token = str(max_faces) if max_faces > 0 else "auto"
        variant_path = os.path.join(
            dense_path,
            f"meshed-poisson-{suffix}-r{ratio_token}-cap{face_cap_token}-base{base_face_count}.ply",
        )

        rebuild_variant = True
        if os.path.exists(variant_path):
            existing_face_count = read_ply_element_count(variant_path, "face")
            if 0 < existing_face_count <= target_face_count:
                rebuild_variant = False
            else:
                logger.info(
                    "Discarding stale texturing mesh variant %s (faces=%s, target<=%s)",
                    variant_path,
                    existing_face_count,
                    target_face_count,
                )
                try:
                    os.remove(variant_path)
                except OSError:
                    pass

        if rebuild_variant:
            report_fn(
                vol_id,
                "MESHING",
                94,
                log=f"Simplifying mesh for texturing: {base_face_count:,} -> {target_face_count:,} faces",
            )
            run_command_fn([
                "colmap", "mesh_simplifier",
                "--input_path", base_mesh_path,
                "--output_path", variant_path,
                "--MeshSimplification.target_face_ratio", f"{target_face_count / float(base_face_count):.6f}",
                "--MeshSimplification.max_error", "0",
            ], vol_id, "MESHING", 94)
        simplified_face_count = read_ply_element_count(variant_path, "face")
        if simplified_face_count <= 0:
            raise RuntimeError(f"Simplified mesh {variant_path} has no faces")
        return variant_path, base_face_count, simplified_face_count

    def build_texturing_attempts(base_face_count):
        requested_mesh_ratio = float(params.get("ortho_mesh_simplify_ratio", "1.0"))
        requested_mesh_max_faces = int(params.get("ortho_mesh_simplify_max_faces", "0"))
        simplify_on_retry = parse_bool(params.get("ortho_mesh_simplify_on_retry", True), default=True)
        retry_mesh_ratio = float(params.get("ortho_mesh_simplify_retry_ratio", "0.5"))
        requested_scale = float(params.get("ortho_texture_scale_factor", "1.0"))
        requested_threads = int(params.get("ortho_texturing_num_threads", "-1"))
        requested_color_correction = parse_bool(params.get("ortho_texturing_apply_color_correction", False), default=False)

        if requested_mesh_ratio >= 0.999 and requested_mesh_max_faces <= 0 and base_face_count > auto_texturing_face_threshold:
            requested_mesh_max_faces = auto_texturing_face_threshold
            report_fn(
                vol_id,
                "TEXTURING",
                94,
                log=(
                    f"Large mesh detected for texturing ({base_face_count:,} faces). "
                    f"Applying preemptive face cap of {requested_mesh_max_faces:,} faces before first texturing attempt."
                ),
            )

        if requested_threads <= 0 and base_face_count > auto_texturing_face_threshold:
            requested_threads = 4

        if requested_scale >= 0.999 and base_face_count > auto_texturing_face_threshold:
            requested_scale = 0.5

        if requested_mesh_ratio < 0.999 or requested_mesh_max_faces > 0:
            report_fn(
                vol_id,
                "TEXTURING",
                94,
                log=(
                    "Texturing will use a simplified mesh on the first attempt: "
                    f"ratio={requested_mesh_ratio:.3f}, max_faces={requested_mesh_max_faces}."
                ),
            )

        mesh_specs = [{
            "label": "base mesh",
            "ratio": requested_mesh_ratio,
            "max_faces": requested_mesh_max_faces,
            "suffix": "base-texture",
        }]

        if simplify_on_retry:
            retry_specs = [
                {
                    "label": "retry simplified mesh",
                    "ratio": min(requested_mesh_ratio, retry_mesh_ratio),
                    "max_faces": scale_face_cap(requested_mesh_max_faces, retry_mesh_ratio),
                    "suffix": "retry-texture",
                },
                {
                    "label": "aggressive simplified mesh",
                    "ratio": min(requested_mesh_ratio, retry_mesh_ratio, 0.25),
                    "max_faces": scale_face_cap(requested_mesh_max_faces, 0.25),
                    "suffix": "retry-texture-aggressive",
                },
            ]
            for retry_spec in retry_specs:
                if retry_spec["ratio"] < 0.999 or retry_spec["max_faces"] > 0:
                    mesh_specs.append(retry_spec)

        deduped_mesh_specs = []
        seen_mesh_specs = set()
        for mesh_spec in mesh_specs:
            key = (round(mesh_spec["ratio"], 4), int(mesh_spec["max_faces"]))
            if key in seen_mesh_specs:
                continue
            seen_mesh_specs.add(key)
            deduped_mesh_specs.append(mesh_spec)

        texture_attempt_specs = [{
            "label": "primary",
            "texture_scale_factor": requested_scale,
            "apply_color_correction": requested_color_correction,
            "num_threads": requested_threads,
        }]

        for fallback_scale in [0.5, 0.25, 0.125]:
            if fallback_scale >= requested_scale:
                continue
            texture_attempt_specs.append({
                "label": f"fallback texture_scale_factor={fallback_scale}",
                "texture_scale_factor": fallback_scale,
                "apply_color_correction": False,
                "num_threads": requested_threads if requested_threads > 0 and requested_threads <= 4 else 4,
            })

        attempts = []
        for texture_spec in texture_attempt_specs:
            for mesh_index, mesh_spec in enumerate(deduped_mesh_specs):
                label_parts = [texture_spec["label"]]
                if mesh_index > 0 or mesh_spec["ratio"] < 0.999 or mesh_spec["max_faces"] > 0:
                    label_parts.append(mesh_spec["label"])
                attempts.append({**mesh_spec, **texture_spec, "label": " + ".join(label_parts)})

        deduped_attempts = []
        seen_attempts = set()
        for attempt in attempts:
            key = (
                round(attempt["ratio"], 4),
                int(attempt["max_faces"]),
                round(attempt["texture_scale_factor"], 4),
                attempt["apply_color_correction"],
                attempt["num_threads"],
            )
            if key in seen_attempts:
                continue
            seen_attempts.add(key)
            deduped_attempts.append(attempt)
        return deduped_attempts

    def run_mesh_texturer_with_retries():
        base_face_count = read_ply_element_count(meshed_ply, "face")
        attempts = build_texturing_attempts(base_face_count)
        last_error = None
        mesh_cache = {}
        texturing_workspace = prepare_texturing_workspace()

        for attempt_index, attempt in enumerate(attempts, start=1):
            mesh_cache_key = (round(attempt["ratio"], 4), int(attempt["max_faces"]), attempt["suffix"])
            if mesh_cache_key not in mesh_cache:
                mesh_cache[mesh_cache_key] = resolve_mesh_variant(
                    meshed_ply,
                    attempt["ratio"],
                    attempt["max_faces"],
                    attempt["suffix"],
                )
            attempt_mesh_path, original_faces, attempt_faces = mesh_cache[mesh_cache_key]
            shutil.rmtree(textured_dir, ignore_errors=True)
            os.makedirs(textured_dir, exist_ok=True)
            report_fn(
                vol_id,
                "TEXTURING",
                95,
                log=(
                    f"Running mesh texturing ({attempt['label']}, attempt {attempt_index}/{len(attempts)}): "
                    f"faces={attempt_faces:,}/{original_faces:,}, "
                    f"scale={attempt['texture_scale_factor']}, color_correction={int(attempt['apply_color_correction'])}, "
                    f"threads={attempt['num_threads']}"
                ),
            )
            command = [
                "colmap", "mesh_texturer",
                "--workspace_path", texturing_workspace,
                "--input_path", attempt_mesh_path,
                "--output_path", textured_dir,
                "--MeshTextureMapping.texture_scale_factor", str(attempt["texture_scale_factor"]),
                "--MeshTextureMapping.apply_color_correction", "1" if attempt["apply_color_correction"] else "0",
                "--MeshTextureMapping.num_threads", str(attempt["num_threads"]),
            ]
            try:
                run_command_fn(command, vol_id, "TEXTURING", 95)
                if os.path.exists(textured_mesh):
                    write_texturing_metadata(current_texturing_metadata)
                    return
                raise RuntimeError("mesh_texturer completed without producing textured mesh output")
            except Exception as error:
                last_error = error
                logger.warning("Mesh texturing attempt %s/%s failed: %s", attempt_index, len(attempts), error)
                report_fn(vol_id, "TEXTURING", 95, log=f"Mesh texturing attempt {attempt_index}/{len(attempts)} failed: {error}")

        raise RuntimeError(f"Mesh texturing failed after {len(attempts)} attempts: {last_error}")

    def resolve_primary_render_mesh():
        base_face_count = read_ply_element_count(meshed_ply, "face")
        attempts = build_texturing_attempts(base_face_count)
        if not attempts:
            return meshed_ply, base_face_count, base_face_count
        primary_attempt = attempts[0]
        return resolve_mesh_variant(
            meshed_ply,
            primary_attempt["ratio"],
            primary_attempt["max_faces"],
            primary_attempt["suffix"],
        )

    def rasterize_vertex_colored_mesh(mesh_path, original_face_count, mesh_face_count):
        PILImage.MAX_IMAGE_PIXELS = None

        requested_resolution = float(os.getenv("ORTHO_MESH_RESOLUTION", params.get("ortho_mesh_resolution", "0.02")))
        max_dimension = int(os.getenv("ORTHO_MESH_MAX_DIMENSION", params.get("ortho_mesh_max_dimension", "12000")))
        rasterizer = os.getenv("ORTHO_MESH_RASTERIZER", params.get("ortho_mesh_rasterizer", "cuda")).lower()
        min_normal_cos = float(os.getenv("ORTHO_MESH_MIN_NORMAL_COS", params.get("ortho_mesh_min_normal_cos", "0.5")))
        require_upward = str(os.getenv("ORTHO_MESH_REQUIRE_UPWARD", str(params.get("ortho_mesh_require_upward", True)))).lower() in ("1", "true", "yes", "on")
        gap_fill_passes = int(os.getenv("ORTHO_MESH_GAP_FILL_PASSES", params.get("ortho_mesh_gap_fill_passes", "3")))

        report_fn(
            vol_id,
            "ORTHO",
            97,
            log=(
                f"Rasterizing vertex-colored mesh directly (faces={mesh_face_count:,}/{original_face_count:,}) "
                "to bypass COLMAP mesh_texturer atlas generation."
            ),
        )

        ply_data = PlyData.read(mesh_path)
        if "vertex" not in ply_data or "face" not in ply_data:
            raise RuntimeError(f"Mesh {mesh_path} is missing vertex or face elements")

        vertex_data = ply_data["vertex"].data
        vertex_fields = set(vertex_data.dtype.names or ())
        if not {"red", "green", "blue"}.issubset(vertex_fields):
            raise RuntimeError(f"Mesh {mesh_path} has no per-vertex RGB colors; cannot bypass mesh_texturer safely")

        vertices = np.column_stack([
            np.asarray(vertex_data["x"], dtype=np.float64),
            np.asarray(vertex_data["y"], dtype=np.float64),
            np.asarray(vertex_data["z"], dtype=np.float64),
        ])
        vertex_colors_u8 = np.column_stack([
            np.asarray(vertex_data["red"], dtype=np.uint8),
            np.asarray(vertex_data["green"], dtype=np.uint8),
            np.asarray(vertex_data["blue"], dtype=np.uint8),
        ])

        raw_faces = ply_data["face"].data["vertex_indices"]
        tri_faces = [np.asarray(face, dtype=np.int32) for face in raw_faces if len(face) == 3]
        if not tri_faces:
            raise RuntimeError(f"Mesh {mesh_path} has no triangular faces for rasterization")
        tri_indices = np.vstack(tri_faces).astype(np.int32, copy=False)

        transform_file = os.path.join(workspace_dir, "alignment_transform.json") if workspace_dir else None
        if transform_file and os.path.exists(transform_file):
            with open(transform_file, "r", encoding="utf-8") as handle:
                tdata = json.load(handle)
            rotation = np.array(tdata["R"])
            scale = tdata["scale"]
            translation = np.array(tdata["t"])
            vertices = (scale * (rotation @ vertices.T) + translation[:, np.newaxis]).T
            report_fn(vol_id, "ORTHO", 97, log="Applied geo-alignment to vertex-colored mesh")

        min_x, max_x = vertices[:, 0].min(), vertices[:, 0].max()
        min_y, max_y = vertices[:, 1].min(), vertices[:, 1].max()
        min_z, max_z = vertices[:, 2].min(), vertices[:, 2].max()
        width_m = max_x - min_x
        height_m = max_y - min_y
        depth_m = max_z - min_z
        resolution = max(
            requested_resolution,
            width_m / max_dimension if width_m > 0 else requested_resolution,
            height_m / max_dimension if height_m > 0 else requested_resolution,
        )
        raster_w = max(1, int(np.ceil(width_m / resolution)))
        raster_h = max(1, int(np.ceil(height_m / resolution)))
        report_fn(vol_id, "ORTHO", 97, log=f"Raster: {raster_w}x{raster_h}px @ {resolution:.3f}m/px")

        local_vertices = np.empty((vertices.shape[0], 3), dtype=np.float32)
        local_vertices[:, 0] = (vertices[:, 0] - min_x).astype(np.float32)
        local_vertices[:, 1] = (vertices[:, 1] - min_y).astype(np.float32)
        local_vertices[:, 2] = (vertices[:, 2] - min_z).astype(np.float32)
        vertex_colors = vertex_colors_u8.astype(np.float32) / np.float32(255.0)

        def write_geotiff(image):
            img_data = image.transpose(2, 0, 1)
            transform = from_origin(min_x, max_y, resolution, resolution)
            crs_to_use = utm_crs if utm_crs else "EPSG:4326"
            with rasterio.open(
                ortho_file,
                "w",
                driver="GTiff",
                height=raster_h,
                width=raster_w,
                count=3,
                dtype=img_data.dtype,
                crs=crs_to_use,
                transform=transform,
            ) as dst:
                dst.write(img_data)

        edge_1 = local_vertices[tri_indices[:, 1]] - local_vertices[tri_indices[:, 0]]
        edge_2 = local_vertices[tri_indices[:, 2]] - local_vertices[tri_indices[:, 0]]
        normals = np.cross(edge_1, edge_2)
        normal_mag = np.linalg.norm(normals, axis=1)
        normal_cos = normals[:, 2] / np.maximum(normal_mag, 1e-8)
        keep = (normal_mag > 1e-8) & ((normal_cos >= min_normal_cos) if require_upward else (np.abs(normal_cos) >= min_normal_cos))
        total_candidate_faces = tri_indices.shape[0]
        total_kept_faces = int(np.count_nonzero(keep))
        if not np.any(keep):
            raise RuntimeError("Vertex-colored mesh rasterization kept no faces after normal filtering")
        tri_indices = tri_indices[keep]

        if rasterizer == "cuda":
            try:
                import nvdiffrast.torch as dr
                import torch

                if not torch.cuda.is_available():
                    raise RuntimeError("torch.cuda.is_available() is false")

                device = torch.device("cuda")
                report_fn(vol_id, "ORTHO", 97, log="Initializing CUDA rasterizer for vertex-colored mesh...")
                glctx = dr.RasterizeCudaContext(device=device)
                final_depth = torch.full((raster_h, raster_w), 1.0, dtype=torch.float32, device=device)
                final_color = torch.zeros((raster_h, raster_w, 3), dtype=torch.float16, device=device)

                batch_faces = 100000
                total_batches = max(1, (tri_indices.shape[0] + batch_faces - 1) // batch_faces)
                for batch_index in range(total_batches):
                    check_cancel()
                    batch = tri_indices[batch_index * batch_faces:(batch_index + 1) * batch_faces]
                    tri_positions = local_vertices[batch]
                    tri_colors = vertex_colors[batch]

                    tri_positions = torch.from_numpy(tri_positions.astype(np.float32)).to(device=device)
                    tri_colors = torch.from_numpy(tri_colors.astype(np.float32)).to(device=device)

                    clip = torch.empty((tri_positions.shape[0], 3, 4), dtype=torch.float32, device=device)
                    clip[..., 0] = tri_positions[..., 0] / max(float(width_m), 1e-6) * 2.0 - 1.0
                    clip[..., 1] = tri_positions[..., 1] / max(float(height_m), 1e-6) * 2.0 - 1.0
                    clip[..., 2] = 1.0 - 2.0 * (tri_positions[..., 2] / max(float(depth_m), 1e-6))
                    clip[..., 3] = 1.0

                    pos = clip.reshape(-1, 4).contiguous()
                    color_attr = tri_colors.reshape(-1, 3).contiguous()
                    tri = torch.arange(pos.shape[0], dtype=torch.int32, device=device).reshape(-1, 3)
                    rast, _ = dr.rasterize(glctx, pos.unsqueeze(0), tri, resolution=[raster_h, raster_w])
                    batch_color, _ = dr.interpolate(color_attr.unsqueeze(0), rast, tri)
                    batch_color = batch_color[0].to(dtype=torch.float16)
                    batch_depth = rast[0, :, :, 2]
                    batch_mask = rast[0, :, :, 3] > 0
                    nearer = batch_mask & (batch_depth < final_depth)
                    final_color[nearer] = batch_color[nearer]
                    final_depth[nearer] = batch_depth[nearer]

                    del tri_positions, tri_colors, clip, pos, color_attr, tri, rast, batch_color, batch_depth, batch_mask, nearer
                    torch.cuda.empty_cache()

                    if batch_index == 0 or (batch_index + 1) % 10 == 0 or batch_index + 1 == total_batches:
                        report_fn(vol_id, "ORTHO", 97, log=f"CUDA vertex-color rasterization: batch {batch_index + 1}/{total_batches}")

                img = final_color.clamp(0.0, 1.0).mul(255.0).byte().cpu().numpy()
                img = np.flipud(img)
                filled = np.count_nonzero(np.any(img > 0, axis=2))
                total = raster_w * raster_h
                if filled <= 0:
                    raise RuntimeError("CUDA vertex-color rasterizer produced an empty image")
                img, fill_before, fill_after = apply_iterative_gap_fill(img, passes=gap_fill_passes)
                write_geotiff(img)
                mode = "n_z" if require_upward else "|n_z|"
                report_fn(vol_id, "ORTHO", 97, log=f"Vertex-color face filter kept {total_kept_faces:,}/{total_candidate_faces:,} faces ({mode}>={min_normal_cos:.2f})")
                report_fn(vol_id, "ORTHO", 97, log=f"Vertex-color gap-fill: {100.0 * fill_before / total:.1f}% -> {100.0 * fill_after / total:.1f}%")
                report_fn(vol_id, "ORTHO", 98, log=f"Mesh ortho written with vertex colors (CUDA): {raster_w}x{raster_h}px @ {resolution:.3f}m/px")
                return
            except Exception as gpu_error:
                logger.warning("Vertex-color CUDA rasterization failed: %s", gpu_error)

        img = np.zeros((raster_h, raster_w, 3), dtype=np.uint8)
        z_buf = np.full((raster_h, raster_w), -np.inf, dtype=np.float32)
        barycentric_samples = np.array([
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [0.5, 0.5, 0.0],
            [0.5, 0.0, 0.5],
            [0.0, 0.5, 0.5],
            [1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0],
        ], dtype=np.float32)

        batch_faces = 500000
        total_batches = max(1, (tri_indices.shape[0] + batch_faces - 1) // batch_faces)
        for batch_index in range(total_batches):
            check_cancel()
            batch = tri_indices[batch_index * batch_faces:(batch_index + 1) * batch_faces]
            tri_positions = local_vertices[batch]
            tri_colors = vertex_colors_u8[batch].astype(np.float32)

            for weights in barycentric_samples:
                pts = np.sum(tri_positions * weights[np.newaxis, :, np.newaxis], axis=1)
                colors = np.sum(tri_colors * weights[np.newaxis, :, np.newaxis], axis=1)
                px = (pts[:, 0] / resolution).astype(np.int32)
                py = ((height_m - pts[:, 1]) / resolution).astype(np.int32)
                valid_px = (px >= 0) & (px < raster_w) & (py >= 0) & (py < raster_h)
                if not np.any(valid_px):
                    continue
                px = px[valid_px]
                py = py[valid_px]
                pz = pts[valid_px, 2]
                colors = np.clip(colors[valid_px], 0, 255).astype(np.uint8)
                closer = pz > z_buf[py, px]
                img[py[closer], px[closer]] = colors[closer]
                z_buf[py[closer], px[closer]] = pz[closer]

            if batch_index == 0 or (batch_index + 1) % 10 == 0 or batch_index + 1 == total_batches:
                report_fn(vol_id, "ORTHO", 97, log=f"CPU vertex-color rasterization: batch {batch_index + 1}/{total_batches}")

        filled = np.any(img > 0, axis=2).sum()
        total = raster_w * raster_h
        report_fn(vol_id, "ORTHO", 97, log=f"Vertex-color face filter kept {total_kept_faces:,}/{total_candidate_faces:,} faces")
        report_fn(vol_id, "ORTHO", 97, log=f"CPU vertex-color fill: {100.0 * filled / total:.1f}%")
        img, fill_before, fill_after = apply_iterative_gap_fill(img, passes=gap_fill_passes)
        report_fn(vol_id, "ORTHO", 97, log=f"CPU vertex-color gap-fill: {100.0 * fill_before / total:.1f}% -> {100.0 * fill_after / total:.1f}%")
        write_geotiff(img)
        report_fn(vol_id, "ORTHO", 98, log=f"Mesh ortho written with vertex colors (CPU): {raster_w}x{raster_h}px @ {resolution:.3f}m/px")

    primary_mesh_path, _, _ = resolve_primary_render_mesh()
    full_mesh_face_count = read_ply_element_count(meshed_ply, "face")
    dense_image_count = count_texturing_images()
    use_vertex_color_mesh = dense_image_count >= auto_vertex_color_image_threshold or full_mesh_face_count >= auto_vertex_color_face_threshold

    if use_vertex_color_mesh:
        report_fn(
            vol_id,
            "TEXTURING",
            94,
            log=(
                f"Large mission detected ({dense_image_count} images, {full_mesh_face_count:,} Poisson mesh faces). "
                "Skipping COLMAP mesh_texturer and rasterizing the full vertex-colored Poisson mesh instead."
            ),
        )
        if primary_mesh_path != meshed_ply:
            report_fn(
                vol_id,
                "TEXTURING",
                94,
                log=(
                    f"Ignoring simplified render mesh {os.path.basename(primary_mesh_path)} for large-mission bypass; "
                    f"using full mesh {os.path.basename(meshed_ply)} instead."
                ),
            )
        rasterize_vertex_colored_mesh(meshed_ply, full_mesh_face_count, full_mesh_face_count)
        return

    saved_texturing_metadata = load_texturing_metadata()
    if os.path.exists(textured_mesh) and saved_texturing_metadata != current_texturing_metadata:
        invalidate_texturing_outputs("texturing parameters changed since the last successful mesh_texturer run")

    if not os.path.exists(textured_mesh):
        run_mesh_texturer_with_retries()
    else:
        report_fn(vol_id, "TEXTURING", 95, log="Textured mesh found, skipping texturing.")

    report_fn(vol_id, "ORTHO", 97, log="Rasterizing textured mesh to GeoTIFF...")
    try:
        PILImage.MAX_IMAGE_PIXELS = None

        requested_resolution = float(os.getenv("ORTHO_MESH_RESOLUTION", params.get("ortho_mesh_resolution", "0.02")))
        max_dimension = int(os.getenv("ORTHO_MESH_MAX_DIMENSION", params.get("ortho_mesh_max_dimension", "12000")))
        rasterizer = os.getenv("ORTHO_MESH_RASTERIZER", params.get("ortho_mesh_rasterizer", "cuda")).lower()
        min_normal_cos = float(os.getenv("ORTHO_MESH_MIN_NORMAL_COS", params.get("ortho_mesh_min_normal_cos", "0.5")))
        require_upward = str(os.getenv("ORTHO_MESH_REQUIRE_UPWARD", str(params.get("ortho_mesh_require_upward", True)))).lower() in ("1", "true", "yes", "on")
        gap_fill_passes = int(os.getenv("ORTHO_MESH_GAP_FILL_PASSES", params.get("ortho_mesh_gap_fill_passes", "3")))

        with open(textured_mesh, "rb") as handle:
            header_lines = []
            while True:
                line = handle.readline().decode("ascii").strip()
                header_lines.append(line)
                if line == "end_header":
                    break
            header_offset = handle.tell()

        n_verts = 0
        n_faces = 0
        for line in header_lines:
            if line.startswith("element vertex"):
                n_verts = int(line.split()[-1])
            elif line.startswith("element face"):
                n_faces = int(line.split()[-1])

        if n_verts == 0 or n_faces == 0:
            raise ValueError(f"Textured mesh has {n_verts} vertices, {n_faces} faces.")

        report_fn(vol_id, "ORTHO", 97, log=f"Loading mesh: {n_verts:,} vertices, {n_faces:,} faces...")
        with open(textured_mesh, "rb") as handle:
            handle.seek(header_offset)
            vertices = np.frombuffer(handle.read(n_verts * 12), dtype="<f4").reshape(-1, 3).astype(np.float64)

        transform_file = os.path.join(workspace_dir, "alignment_transform.json") if workspace_dir else None
        if transform_file and os.path.exists(transform_file):
            with open(transform_file, "r", encoding="utf-8") as handle:
                tdata = json.load(handle)
            rotation = np.array(tdata["R"])
            scale = tdata["scale"]
            translation = np.array(tdata["t"])
            vertices = (scale * (rotation @ vertices.T) + translation[:, np.newaxis]).T
            report_fn(vol_id, "ORTHO", 97, log="Applied geo-alignment to mesh vertices")

        min_x, max_x = vertices[:, 0].min(), vertices[:, 0].max()
        min_y, max_y = vertices[:, 1].min(), vertices[:, 1].max()
        min_z, max_z = vertices[:, 2].min(), vertices[:, 2].max()
        width_m = max_x - min_x
        height_m = max_y - min_y
        depth_m = max_z - min_z
        resolution = max(
            requested_resolution,
            width_m / max_dimension if width_m > 0 else requested_resolution,
            height_m / max_dimension if height_m > 0 else requested_resolution,
        )
        raster_w = max(1, int(np.ceil(width_m / resolution)))
        raster_h = max(1, int(np.ceil(height_m / resolution)))
        report_fn(vol_id, "ORTHO", 97, log=f"Raster: {raster_w}x{raster_h}px @ {resolution:.3f}m/px")

        local_vertices = np.empty((n_verts, 3), dtype=np.float32)
        local_vertices[:, 0] = (vertices[:, 0] - min_x).astype(np.float32)
        local_vertices[:, 1] = (vertices[:, 1] - min_y).astype(np.float32)
        local_vertices[:, 2] = (vertices[:, 2] - min_z).astype(np.float32)
        del vertices

        face_dtype = np.dtype([
            ("n_vert", "u1"), ("v0", "<i4"), ("v1", "<i4"), ("v2", "<i4"), ("n_tc", "u1"),
            ("u0", "<f4"), ("t0", "<f4"), ("u1", "<f4"), ("t1", "<f4"), ("u2", "<f4"), ("t2", "<f4"),
        ])
        texture_path = os.path.join(textured_dir, "texture.png")

        def write_geotiff(image):
            img_data = image.transpose(2, 0, 1)
            transform = from_origin(min_x, max_y, resolution, resolution)
            crs_to_use = utm_crs if utm_crs else "EPSG:4326"
            with rasterio.open(
                ortho_file,
                "w",
                driver="GTiff",
                height=raster_h,
                width=raster_w,
                count=3,
                dtype=img_data.dtype,
                crs=crs_to_use,
                transform=transform,
            ) as dst:
                dst.write(img_data)

        if rasterizer == "cuda":
            try:
                import nvdiffrast.torch as dr
                import torch

                if not torch.cuda.is_available():
                    raise RuntimeError("torch.cuda.is_available() is false")

                device = torch.device("cuda")
                report_fn(vol_id, "ORTHO", 97, log="Initializing CUDA rasterizer...")
                glctx = dr.RasterizeCudaContext(device=device)
                report_fn(vol_id, "ORTHO", 97, log="Loading texture for CUDA rasterizer...")
                texture_image = np.array(PILImage.open(texture_path).convert("RGB"), dtype=np.float32) / np.float32(255.0)
                texture_filter_mode = "linear"
                texture_sampling_kwargs = {"boundary_mode": "clamp"}

                tex_h, tex_w = texture_image.shape[:2]
                can_build_full_mips = True
                mip_test_w = tex_w
                mip_test_h = tex_h
                while mip_test_w > 1 or mip_test_h > 1:
                    if (mip_test_w > 1 and (mip_test_w & 1)) or (mip_test_h > 1 and (mip_test_h & 1)):
                        can_build_full_mips = False
                        break
                    mip_test_w = max(1, mip_test_w // 2)
                    mip_test_h = max(1, mip_test_h // 2)

                if not can_build_full_mips:
                    target_long_side = int(max(raster_w, raster_h) * 2)
                    resize_scale = min(1.0, target_long_side / float(max(tex_w, tex_h)))
                    if resize_scale < 1.0:
                        resized_w = max(2, int(round(tex_w * resize_scale)))
                        resized_h = max(2, int(round(tex_h * resize_scale)))
                        if resized_w % 2:
                            resized_w += 1
                        if resized_h % 2:
                            resized_h += 1
                        report_fn(vol_id, "ORTHO", 97, log=f"Prefiltering texture atlas for CUDA rasterizer: {tex_w}x{tex_h} -> {resized_w}x{resized_h}")
                        texture_image = np.array(
                            PILImage.fromarray((texture_image * 255.0).astype(np.uint8)).resize((resized_w, resized_h), PILImage.Resampling.LANCZOS),
                            dtype=np.float32,
                        ) / np.float32(255.0)
                        tex_h, tex_w = texture_image.shape[:2]

                texture = torch.from_numpy(texture_image).to(device=device).unsqueeze(0).contiguous()
                max_tex_scale = max(texture.shape[2] / max(raster_h, 1), texture.shape[1] / max(raster_w, 1), 1.0)
                if can_build_full_mips:
                    mip_bias_value = float(np.log2(max_tex_scale)) if max_tex_scale > 1.0 else 0.0
                    texture_filter_mode = "linear-mipmap-linear"
                    texture_sampling_kwargs["mip_level_bias"] = torch.full((1, raster_h, raster_w), mip_bias_value, dtype=torch.float32, device=device)
                    texture_sampling_kwargs["mip"] = dr.texture_construct_mip(texture, max_mip_level=6)

                report_fn(vol_id, "ORTHO", 97, log="Allocating CUDA ortho buffers...")
                final_depth = torch.full((raster_h, raster_w), 1.0, dtype=torch.float32, device=device)
                final_color = torch.zeros((raster_h, raster_w, 3), dtype=torch.float16, device=device)
                total_candidate_faces = 0
                total_kept_faces = 0

                batch_faces = 100000
                total_batches = max(1, (n_faces + batch_faces - 1) // batch_faces)
                face_offset = header_offset + n_verts * 12

                with open(textured_mesh, "rb") as handle:
                    handle.seek(face_offset)
                    for batch_index in range(total_batches):
                        check_cancel()
                        batch_count = min(batch_faces, n_faces - batch_index * batch_faces)
                        face_data = np.fromfile(handle, dtype=face_dtype, count=batch_count)
                        if len(face_data) == 0:
                            break
                        valid = (face_data["n_vert"] == 3) & (face_data["n_tc"] == 6)
                        if not np.any(valid):
                            continue

                        tri_indices = np.column_stack([face_data["v0"][valid], face_data["v1"][valid], face_data["v2"][valid]])
                        tri_positions = local_vertices[tri_indices]
                        edge_1 = tri_positions[:, 1] - tri_positions[:, 0]
                        edge_2 = tri_positions[:, 2] - tri_positions[:, 0]
                        normals = np.cross(edge_1, edge_2)
                        normal_mag = np.linalg.norm(normals, axis=1)
                        normal_cos = normals[:, 2] / np.maximum(normal_mag, 1e-8)
                        keep = (normal_mag > 1e-8) & ((normal_cos >= min_normal_cos) if require_upward else (np.abs(normal_cos) >= min_normal_cos))
                        total_candidate_faces += tri_positions.shape[0]
                        total_kept_faces += int(np.count_nonzero(keep))
                        if not np.any(keep):
                            continue
                        tri_positions = tri_positions[keep]
                        tri_uvs = np.column_stack([
                            face_data["u0"][valid], face_data["t0"][valid],
                            face_data["u1"][valid], face_data["t1"][valid],
                            face_data["u2"][valid], face_data["t2"][valid],
                        ]).reshape(-1, 3, 2)[keep]
                        tri_positions = torch.from_numpy(tri_positions.astype(np.float32)).to(device=device)
                        tri_uvs = torch.from_numpy(tri_uvs.astype(np.float32)).to(device=device)
                        tri_uvs[..., 1] = 1.0 - tri_uvs[..., 1]

                        clip = torch.empty((tri_positions.shape[0], 3, 4), dtype=torch.float32, device=device)
                        clip[..., 0] = tri_positions[..., 0] / max(float(width_m), 1e-6) * 2.0 - 1.0
                        clip[..., 1] = tri_positions[..., 1] / max(float(height_m), 1e-6) * 2.0 - 1.0
                        clip[..., 2] = 1.0 - 2.0 * (tri_positions[..., 2] / max(float(depth_m), 1e-6))
                        clip[..., 3] = 1.0

                        pos = clip.reshape(-1, 4).contiguous()
                        uv_attr = tri_uvs.reshape(-1, 2).contiguous()
                        tri = torch.arange(pos.shape[0], dtype=torch.int32, device=device).reshape(-1, 3)

                        rast, _ = dr.rasterize(glctx, pos.unsqueeze(0), tri, resolution=[raster_h, raster_w])
                        texc, _ = dr.interpolate(uv_attr.unsqueeze(0), rast, tri)
                        batch_color = dr.texture(texture, texc, filter_mode=texture_filter_mode, **texture_sampling_kwargs)[0].to(dtype=torch.float16)
                        batch_depth = rast[0, :, :, 2]
                        batch_mask = rast[0, :, :, 3] > 0
                        nearer = batch_mask & (batch_depth < final_depth)
                        final_color[nearer] = batch_color[nearer]
                        final_depth[nearer] = batch_depth[nearer]

                        del tri_positions, tri_uvs, clip, pos, uv_attr, tri, rast, texc, batch_color, batch_depth, batch_mask, nearer
                        torch.cuda.empty_cache()

                        if batch_index == 0 or (batch_index + 1) % 10 == 0 or batch_index + 1 == total_batches:
                            report_fn(vol_id, "ORTHO", 97, log=f"CUDA rasterizing mesh: batch {batch_index + 1}/{total_batches}")

                report_fn(vol_id, "ORTHO", 97, log="Reading CUDA rasterizer output...")
                img = final_color.clamp(0.0, 1.0).mul(255.0).byte().cpu().numpy()
                img = np.flipud(img)
                gpu_filled = np.count_nonzero(np.any(img > 0, axis=2))
                gpu_fill_ratio = gpu_filled / float(raster_w * raster_h)
                if gpu_fill_ratio < 0.005:
                    raise RuntimeError(f"CUDA rasterizer nearly empty ({100.0 * gpu_fill_ratio:.2f}% filled)")
                img, fill_before, fill_after = apply_iterative_gap_fill(img, passes=gap_fill_passes)
                write_geotiff(img)
                if total_candidate_faces > 0:
                    mode = "n_z" if require_upward else "|n_z|"
                    report_fn(vol_id, "ORTHO", 97, log=f"CUDA mesh face filter kept {total_kept_faces:,}/{total_candidate_faces:,} faces ({mode}>={min_normal_cos:.2f})")
                report_fn(vol_id, "ORTHO", 97, log=f"CUDA gap-fill: {100.0 * fill_before / (raster_w * raster_h):.1f}% -> {100.0 * fill_after / (raster_w * raster_h):.1f}%")
                report_fn(vol_id, "ORTHO", 98, log=f"Mesh ortho written with CUDA rasterizer: {raster_w}x{raster_h}px @ {resolution:.3f}m/px, fill={100.0 * fill_after / (raster_w * raster_h):.1f}%")
                return
            except Exception as gpu_error:
                raise RuntimeError(f"CUDA rasterizer unavailable ({gpu_error})")

        face_offset = header_offset + n_verts * 12
        img = np.zeros((raster_h, raster_w, 3), dtype=np.uint8)
        z_buf = np.full((raster_h, raster_w), -np.inf, dtype=np.float32)
        texture = np.array(PILImage.open(texture_path).convert("RGB"))
        tex_h, tex_w = texture.shape[:2]
        barycentric_samples = np.array([
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [0.5, 0.5, 0.0],
            [0.5, 0.0, 0.5],
            [0.0, 0.5, 0.5],
            [1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0],
        ], dtype=np.float32)

        batch_faces = 500000
        total_batches = max(1, (n_faces + batch_faces - 1) // batch_faces)
        total_candidate_faces = 0
        total_kept_faces = 0
        with open(textured_mesh, "rb") as handle:
            handle.seek(face_offset)
            for batch_index in range(total_batches):
                check_cancel()
                batch_count = min(batch_faces, n_faces - batch_index * batch_faces)
                face_data = np.fromfile(handle, dtype=face_dtype, count=batch_count)
                if len(face_data) == 0:
                    break
                valid = (face_data["n_vert"] == 3) & (face_data["n_tc"] == 6)
                if not np.any(valid):
                    continue
                tri_indices = np.column_stack([face_data["v0"][valid], face_data["v1"][valid], face_data["v2"][valid]])
                tri_positions = local_vertices[tri_indices]
                edge_1 = tri_positions[:, 1] - tri_positions[:, 0]
                edge_2 = tri_positions[:, 2] - tri_positions[:, 0]
                normals = np.cross(edge_1, edge_2)
                normal_mag = np.linalg.norm(normals, axis=1)
                normal_cos = normals[:, 2] / np.maximum(normal_mag, 1e-8)
                keep = (normal_mag > 1e-8) & ((normal_cos >= min_normal_cos) if require_upward else (np.abs(normal_cos) >= min_normal_cos))
                total_candidate_faces += tri_positions.shape[0]
                total_kept_faces += int(np.count_nonzero(keep))
                if not np.any(keep):
                    continue
                tri_positions = tri_positions[keep]
                tri_uvs = np.column_stack([
                    face_data["u0"][valid], face_data["t0"][valid],
                    face_data["u1"][valid], face_data["t1"][valid],
                    face_data["u2"][valid], face_data["t2"][valid],
                ]).reshape(-1, 3, 2)[keep]

                for weights in barycentric_samples:
                    pts = np.sum(tri_positions * weights[np.newaxis, :, np.newaxis], axis=1)
                    uv = np.sum(tri_uvs * weights[np.newaxis, :, np.newaxis], axis=1)
                    px = (pts[:, 0] / resolution).astype(np.int32)
                    py = ((height_m - pts[:, 1]) / resolution).astype(np.int32)
                    valid_px = (px >= 0) & (px < raster_w) & (py >= 0) & (py < raster_h)
                    if not np.any(valid_px):
                        continue
                    px = px[valid_px]
                    py = py[valid_px]
                    pz = pts[valid_px, 2]
                    uv = uv[valid_px]
                    tx = np.clip((uv[:, 0] * tex_w).astype(np.int32), 0, tex_w - 1)
                    ty = np.clip(((1.0 - uv[:, 1]) * tex_h).astype(np.int32), 0, tex_h - 1)
                    colors = texture[ty, tx]
                    closer = pz > z_buf[py, px]
                    img[py[closer], px[closer]] = colors[closer]
                    z_buf[py[closer], px[closer]] = pz[closer]

                if batch_index == 0 or (batch_index + 1) % 10 == 0 or batch_index + 1 == total_batches:
                    report_fn(vol_id, "ORTHO", 97, log=f"CPU mesh sampling: batch {batch_index + 1}/{total_batches}")

        filled = np.any(img > 0, axis=2).sum()
        total = raster_w * raster_h
        if total_candidate_faces > 0:
            mode = "n_z" if require_upward else "|n_z|"
            report_fn(vol_id, "ORTHO", 97, log=f"CPU mesh face filter kept {total_kept_faces:,}/{total_candidate_faces:,} faces ({mode}>={min_normal_cos:.2f})")
        report_fn(vol_id, "ORTHO", 97, log=f"CPU mesh fill: {100.0 * filled / total:.1f}%")
        img, fill_before, fill_after = apply_iterative_gap_fill(img, passes=gap_fill_passes)
        report_fn(vol_id, "ORTHO", 97, log=f"CPU gap-fill: {100.0 * fill_before / total:.1f}% -> {100.0 * fill_after / total:.1f}%")
        write_geotiff(img)
        report_fn(vol_id, "ORTHO", 98, log=f"Mesh ortho written with CPU mesh sampling: {raster_w}x{raster_h}px @ {resolution:.3f}m/px")

    except Exception as error:
        report_fn(vol_id, "ORTHO", 97, log=f"Mesh rasterization failed ({error}), falling back to PLY projection...")
        transform_file = os.path.join(workspace_dir, "alignment_transform.json") if workspace_dir else None
        generate_ortho_from_ply(fused_ply, ortho_file, utm_crs, vol_id, report_fn, transform_file=transform_file)


def generate_dummy_ortho(ortho_file, fallback_x=0, fallback_y=0):
    dummy_data = np.zeros((3, 1024, 1024), dtype=rasterio.uint8)
    transform = from_origin(fallback_x, fallback_y, 1, 1)
    with rasterio.open(
        ortho_file,
        "w",
        driver="GTiff",
        height=1024,
        width=1024,
        count=3,
        dtype=dummy_data.dtype,
        crs="EPSG:4326",
        transform=transform,
    ) as dst:
        dst.write(dummy_data)