"""
LichtFeld-Studio headless training wrapper.

Wraps LichtFeld-Studio's ``--headless --train`` mode as a subprocess,
monitors progress via its MCP HTTP endpoint, and exports a standard
3DGS PLY file for loading into our GaussianModel.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import urllib.request
import urllib.error


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

# Default MCP port for LichtFeld-Studio
LFS_MCP_PORT = 45677

# Poll interval for training progress (seconds)
POLL_INTERVAL = 5.0

# Maximum time to wait for the MCP server to come up (seconds)
MCP_STARTUP_TIMEOUT = 120


@dataclass
class LichtFeldTrainConfig:
    """Parameters for LichtFeld headless training."""
    iterations: int = 30_000
    strategy: str = "mrnf"            # "mrnf" (recommended) or "mcmc"
    sh_degree: int = 3
    cap_max: int = 5_000_000          # MRNF sweet spot for 24GB VRAM
    data_path: str = ""               # Root dir containing sparse/ and images/
    output_path: str = ""             # Checkpoint output directory
    data_factor: int = 1              # Image downscaling (auto-scheduled internally by MRNF)


# ---------------------------------------------------------------------------
#  Binary discovery
# ---------------------------------------------------------------------------

def find_lichtfeld_binary() -> Optional[str]:
    """
    Locate the LichtFeld-Studio binary.

    Search order:
      1. LICHTFELD_BIN environment variable
      2. ./LichtFeld-Studio/build/LichtFeld-Studio
      3. ~/LichtFeld-Studio/build/LichtFeld-Studio
      4. System PATH
    """
    # 1. Env variable
    env_bin = os.environ.get("LICHTFELD_BIN")
    if env_bin and os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
        return env_bin

    # 2-3. Known build locations relative to this file and home
    script_root = Path(__file__).resolve().parent.parent.parent  # repo root
    candidates = [
        script_root / "LichtFeld-Studio" / "build" / "LichtFeld-Studio",
        Path.home() / "LichtFeld-Studio" / "build" / "LichtFeld-Studio",
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(str(candidate), os.X_OK):
            return str(candidate)

    # 4. System PATH
    which = shutil.which("LichtFeld-Studio")
    if which:
        return which

    return None


def is_lichtfeld_available() -> bool:
    """Check whether LichtFeld-Studio binary is available."""
    return find_lichtfeld_binary() is not None


# ---------------------------------------------------------------------------
#  MCP HTTP client helpers
# ---------------------------------------------------------------------------

def _mcp_request(method: str, params: dict = None, port: int = LFS_MCP_PORT,
                 timeout: float = 10.0) -> dict:
    """Send a JSON-RPC 2.0 request to LichtFeld MCP."""
    url = f"http://127.0.0.1:{port}/mcp"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
    }
    if params:
        payload["params"] = params
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _mcp_read_resource(uri: str, port: int = LFS_MCP_PORT) -> dict:
    """Read an MCP resource."""
    return _mcp_request("resources/read", {"uri": uri}, port=port)


def _mcp_call_tool(name: str, arguments: dict = None, port: int = LFS_MCP_PORT) -> dict:
    """Call an MCP tool."""
    params = {"name": name}
    if arguments:
        params["arguments"] = arguments
    return _mcp_request("tools/call", params, port=port)


def _wait_for_mcp(port: int = LFS_MCP_PORT, timeout: float = MCP_STARTUP_TIMEOUT) -> bool:
    """Wait until the MCP HTTP endpoint is reachable."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _mcp_request("resources/list", port=port, timeout=3.0)
            return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(1.0)
    return False


def _get_training_state(port: int = LFS_MCP_PORT) -> dict:
    """
    Query the training job status.

    Returns dict with keys: state, current_iteration, total_iterations, loss.
    """
    try:
        result = _mcp_read_resource("lichtfeld://runtime/jobs/training.main", port=port)
        # Extract text content from MCP response
        contents = result.get("result", {}).get("contents", [])
        for c in contents:
            if c.get("mimeType") == "application/json":
                return json.loads(c["text"])
            if "text" in c:
                try:
                    return json.loads(c["text"])
                except (json.JSONDecodeError, TypeError):
                    pass
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------------------
#  Training subprocess
# ---------------------------------------------------------------------------

def train_with_lichtfeld(
    config: LichtFeldTrainConfig,
    report_fn: Optional[Callable] = None,
    mcp_port: int = LFS_MCP_PORT,
    verbose: bool = False,
) -> str:
    """
    Run LichtFeld-Studio headless training and return the exported PLY path.

    Parameters
    ----------
    config : LichtFeldTrainConfig
        Training configuration.
    report_fn : callable, optional
        Progress callback: report_fn(iteration, loss, n_gaussians).
    mcp_port : int
        MCP HTTP port for progress monitoring.
    verbose : bool
        If True, print all raw LichtFeld stdout lines.

    Returns
    -------
    str
        Path to the exported PLY file.

    Raises
    ------
    FileNotFoundError
        If LichtFeld binary is not found.
    RuntimeError
        If training fails.
    """
    binary = find_lichtfeld_binary()
    if not binary:
        raise FileNotFoundError(
            "LichtFeld-Studio binary not found. "
            "Build with: ./scripts/build_lichtfeld.sh  "
            "Or set LICHTFELD_BIN=/path/to/LichtFeld-Studio"
        )

    # Validate data path — need sparse/0 or sparse/ with COLMAP files
    data_path = Path(config.data_path)
    if not data_path.exists():
        raise FileNotFoundError(f"Data path does not exist: {data_path}")

    # Ensure output directory exists
    output_path = Path(config.output_path)
    output_path.mkdir(parents=True, exist_ok=True)

    # Build command
    cmd = [
        binary,
        "--headless",
        "--train",
        "--data-path", str(data_path),
        "--output-path", str(output_path),
        "--iter", str(config.iterations),
        "--strategy", config.strategy,
        "--sh-degree", str(config.sh_degree),
        "--max-cap", str(config.cap_max),
    ]

    # Log the command
    if verbose:
        print(f"[LichtFeld] cmd: {' '.join(cmd)}")
    if report_fn:
        report_fn(0, 0.0, 0)

    # Start the subprocess
    env = os.environ.copy()
    # Ensure WSL2 CUDA driver libs are found before stale system packages
    wsl_lib = "/usr/lib/wsl/lib"
    if os.path.isdir(wsl_lib):
        env["LD_LIBRARY_PATH"] = wsl_lib + ":" + env.get("LD_LIBRARY_PATH", "")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        preexec_fn=os.setsid,  # new process group for clean kill
    )

    try:
        # Wait for MCP to become available
        mcp_available = _wait_for_mcp(port=mcp_port, timeout=MCP_STARTUP_TIMEOUT)

        if mcp_available:
            # Monitor via MCP
            _monitor_training_mcp(proc, config, report_fn, mcp_port, verbose)
        else:
            # Fallback: monitor via stdout
            _monitor_training_stdout(proc, config, report_fn, verbose)

        # Wait for process to finish
        returncode = proc.wait(timeout=60)
        if returncode != 0:
            raise RuntimeError(
                f"LichtFeld training failed with exit code {returncode}"
            )

    except KeyboardInterrupt:
        # Clean shutdown
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=30)
        raise
    except Exception:
        # Kill on any unexpected error
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=30)
        raise

    # Find the exported checkpoint PLY
    ply_path = _find_checkpoint_ply(output_path)
    if not ply_path:
        raise RuntimeError(
            f"Training completed but no PLY checkpoint found in {output_path}"
        )

    return ply_path


def _monitor_training_mcp(
    proc: subprocess.Popen,
    config: LichtFeldTrainConfig,
    report_fn: Optional[Callable],
    mcp_port: int,
    verbose: bool = False,
):
    """Monitor training progress via MCP HTTP polling."""
    import threading

    # Drain stdout in a background thread so the pipe doesn't fill up
    def _drain_stdout():
        for raw_line in iter(proc.stdout.readline, b""):
            if verbose:
                print(f"[LichtFeld] {raw_line.decode('utf-8', errors='replace').rstrip()}")
    stdout_thread = threading.Thread(target=_drain_stdout, daemon=True)
    stdout_thread.start()

    last_iter = -1
    while proc.poll() is None:
        state = _get_training_state(port=mcp_port)
        current_iter = state.get("current_iteration", 0)
        total_iter = state.get("total_iterations", config.iterations)
        loss = state.get("loss", 0.0)
        n_gauss = state.get("num_gaussians", 0)
        job_state = state.get("state", "unknown")

        if current_iter > last_iter and report_fn:
            report_fn(current_iter, loss, n_gauss)
            last_iter = current_iter

        if job_state in ("completed", "stopped", "failed"):
            break

        time.sleep(POLL_INTERVAL)

    stdout_thread.join(timeout=10)


def _monitor_training_stdout(
    proc: subprocess.Popen,
    config: LichtFeldTrainConfig,
    report_fn: Optional[Callable],
    verbose: bool = False,
):
    """Fallback: monitor training via stdout parsing."""
    import re
    iter_pattern = re.compile(r"iter\s+(\d+).*loss[=:]\s*([\d.]+)", re.IGNORECASE)

    for raw_line in iter(proc.stdout.readline, b""):
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue

        if verbose:
            print(f"[LichtFeld] {line}")

        match = iter_pattern.search(line)
        if match and report_fn:
            it = int(match.group(1))
            loss = float(match.group(2))
            report_fn(it, loss, 0)


def _find_checkpoint_ply(output_dir: Path) -> Optional[str]:
    """
    Find the best checkpoint PLY in a LichtFeld output directory.

    Searches for:
      1. point_cloud.ply (standard export name)
      2. latest.ply
      3. Any .ply sorted descending by iteration number
    """
    candidates = [
        output_dir / "point_cloud.ply",
        output_dir / "latest.ply",
        output_dir / "model.ply",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)

    # Search for iteration-numbered PLYs
    plys = sorted(output_dir.glob("*.ply"), reverse=True)
    if plys:
        return str(plys[0])

    # Search subdirectories (LichtFeld may nest under point_cloud/)
    for subdir in output_dir.iterdir():
        if subdir.is_dir():
            for c in [subdir / "point_cloud.ply", subdir / "latest.ply"]:
                if c.is_file():
                    return str(c)
            plys = sorted(subdir.glob("*.ply"), reverse=True)
            if plys:
                return str(plys[0])

    return None


# ---------------------------------------------------------------------------
#  COLMAP subset export (for partitioned training)
# ---------------------------------------------------------------------------

def export_colmap_subset(
    source_sparse_dir: str,
    target_dir: str,
    camera_names: list[str],
    point_ids: Optional[set[int]] = None,
    images_dir: Optional[str] = None,
) -> str:
    """
    Write a filtered COLMAP sparse reconstruction for LichtFeld training.

    Creates ``target_dir/sparse/0/`` with cameras.bin, images.bin,
    points3D.bin containing only the specified cameras (and optionally
    a subset of points).  Symlinks images instead of copying.

    Parameters
    ----------
    source_sparse_dir : str
        Path to the original COLMAP sparse/0 directory.
    target_dir : str
        Root of the per-cell workspace.
    camera_names : list[str]
        Image filenames to include.
    point_ids : set[int], optional
        If given, keep only these point3D IDs.
    images_dir : str, optional
        Path to the images directory.  If given, a symlink is created.

    Returns
    -------
    str
        Path to the created sparse/0 directory.
    """
    import struct

    source = Path(source_sparse_dir)
    target_sparse = Path(target_dir) / "sparse" / "0"
    target_sparse.mkdir(parents=True, exist_ok=True)

    # -- Read source binary files --
    cameras_bin = _read_colmap_cameras_bin(source / "cameras.bin")
    images_bin = _read_colmap_images_bin(source / "images.bin")
    points3d_bin = _read_colmap_points3d_bin(source / "points3D.bin")

    # Filter by camera names
    name_set = set(camera_names)
    filtered_images = {
        img_id: img for img_id, img in images_bin.items()
        if img["name"] in name_set
    }

    # Camera IDs used by filtered images
    used_camera_ids = {img["camera_id"] for img in filtered_images.values()}
    filtered_cameras = {
        cam_id: cam for cam_id, cam in cameras_bin.items()
        if cam_id in used_camera_ids
    }

    # Point IDs visible in filtered images
    if point_ids is None:
        visible_point_ids = set()
        for img in filtered_images.values():
            visible_point_ids.update(
                pid for pid in img["point3D_ids"] if pid != -1
            )
    else:
        visible_point_ids = point_ids

    filtered_points = {
        pid: pt for pid, pt in points3d_bin.items()
        if pid in visible_point_ids
    }

    # -- Write filtered binary files --
    _write_colmap_cameras_bin(filtered_cameras, target_sparse / "cameras.bin")
    _write_colmap_images_bin(filtered_images, target_sparse / "images.bin")
    _write_colmap_points3d_bin(filtered_points, target_sparse / "points3D.bin")

    # Symlink images directory
    target_images = Path(target_dir) / "images"
    if images_dir and not target_images.exists():
        os.symlink(os.path.abspath(images_dir), str(target_images))

    return str(target_sparse)


# ---------------------------------------------------------------------------
#  COLMAP binary I/O helpers
# ---------------------------------------------------------------------------

def _read_colmap_cameras_bin(path: Path) -> dict:
    """Read cameras.bin → {camera_id: {model_id, width, height, params}}."""
    cameras = {}
    with open(path, "rb") as f:
        num_cameras = struct.unpack("<Q", f.read(8))[0]
        for _ in range(num_cameras):
            camera_id = struct.unpack("<I", f.read(4))[0]
            model_id = struct.unpack("<i", f.read(4))[0]
            width = struct.unpack("<Q", f.read(8))[0]
            height = struct.unpack("<Q", f.read(8))[0]
            # Number of parameters per camera model
            num_params = {0: 3, 1: 4, 2: 4, 3: 5, 4: 4, 5: 5,
                          6: 8, 7: 12, 8: 4, 9: 5}.get(model_id, 4)
            params = struct.unpack(f"<{num_params}d", f.read(8 * num_params))
            cameras[camera_id] = {
                "model_id": model_id, "width": width, "height": height,
                "params": list(params),
            }
    return cameras


def _read_colmap_images_bin(path: Path) -> dict:
    """Read images.bin → {image_id: {qw,qx,qy,qz,tx,ty,tz,camera_id,name,xys,point3D_ids}}."""
    images = {}
    with open(path, "rb") as f:
        num_images = struct.unpack("<Q", f.read(8))[0]
        for _ in range(num_images):
            image_id = struct.unpack("<I", f.read(4))[0]
            qw, qx, qy, qz = struct.unpack("<4d", f.read(32))
            tx, ty, tz = struct.unpack("<3d", f.read(24))
            camera_id = struct.unpack("<I", f.read(4))[0]
            # Read name (null-terminated)
            name_chars = []
            while True:
                ch = f.read(1)
                if ch == b"\x00":
                    break
                name_chars.append(ch)
            name = b"".join(name_chars).decode("utf-8")
            # Read 2D points
            num_points2d = struct.unpack("<Q", f.read(8))[0]
            xys = []
            point3d_ids = []
            for _ in range(num_points2d):
                x, y = struct.unpack("<2d", f.read(16))
                pid = struct.unpack("<q", f.read(8))[0]  # signed int64
                xys.append((x, y))
                point3d_ids.append(pid)
            images[image_id] = {
                "qw": qw, "qx": qx, "qy": qy, "qz": qz,
                "tx": tx, "ty": ty, "tz": tz,
                "camera_id": camera_id, "name": name,
                "xys": xys, "point3D_ids": point3d_ids,
            }
    return images


def _read_colmap_points3d_bin(path: Path) -> dict:
    """Read points3D.bin → {point3D_id: {xyz, rgb, error, track}}."""
    points = {}
    with open(path, "rb") as f:
        num_points = struct.unpack("<Q", f.read(8))[0]
        for _ in range(num_points):
            pid = struct.unpack("<Q", f.read(8))[0]
            xyz = struct.unpack("<3d", f.read(24))
            rgb = struct.unpack("<3B", f.read(3))
            error = struct.unpack("<d", f.read(8))[0]
            track_length = struct.unpack("<Q", f.read(8))[0]
            track = []
            for _ in range(track_length):
                img_id = struct.unpack("<I", f.read(4))[0]
                pt2d_idx = struct.unpack("<I", f.read(4))[0]
                track.append((img_id, pt2d_idx))
            points[pid] = {
                "xyz": xyz, "rgb": rgb, "error": error, "track": track,
            }
    return points


def _write_colmap_cameras_bin(cameras: dict, path: Path):
    """Write cameras.bin from dict."""
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(cameras)))
        for camera_id, cam in cameras.items():
            f.write(struct.pack("<I", camera_id))
            f.write(struct.pack("<i", cam["model_id"]))
            f.write(struct.pack("<Q", cam["width"]))
            f.write(struct.pack("<Q", cam["height"]))
            for p in cam["params"]:
                f.write(struct.pack("<d", p))


def _write_colmap_images_bin(images: dict, path: Path):
    """Write images.bin from dict."""
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(images)))
        for image_id, img in images.items():
            f.write(struct.pack("<I", image_id))
            f.write(struct.pack("<4d", img["qw"], img["qx"], img["qy"], img["qz"]))
            f.write(struct.pack("<3d", img["tx"], img["ty"], img["tz"]))
            f.write(struct.pack("<I", img["camera_id"]))
            f.write(img["name"].encode("utf-8") + b"\x00")
            f.write(struct.pack("<Q", len(img["xys"])))
            for (x, y), pid in zip(img["xys"], img["point3D_ids"]):
                f.write(struct.pack("<2d", x, y))
                f.write(struct.pack("<q", pid))


def _write_colmap_points3d_bin(points: dict, path: Path):
    """Write points3D.bin from dict."""
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(points)))
        for pid, pt in points.items():
            f.write(struct.pack("<Q", pid))
            f.write(struct.pack("<3d", *pt["xyz"]))
            f.write(struct.pack("<3B", *pt["rgb"]))
            f.write(struct.pack("<d", pt["error"]))
            f.write(struct.pack("<Q", len(pt["track"])))
            for img_id, pt2d_idx in pt["track"]:
                f.write(struct.pack("<I", img_id))
                f.write(struct.pack("<I", pt2d_idx))
