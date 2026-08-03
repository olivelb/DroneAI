import fcntl
import logging
import os
import signal
import subprocess
import time

import rasterio


logger = logging.getLogger("app1-colmap.runtime")


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


def _report_heartbeat_if_due(
    *,
    command,
    vol_id,
    step,
    base_progress,
    report_fn,
    start_time,
    last_heartbeat_time,
    heartbeat_interval,
):
    now = time.monotonic()
    if heartbeat_interval <= 0 or now - last_heartbeat_time < heartbeat_interval:
        return last_heartbeat_time
    elapsed_seconds = int(now - start_time)
    report_fn(
        vol_id,
        step,
        base_progress,
        log=f"Still running after {elapsed_seconds}s: {' '.join(command)}",
        details={
            "event": "command_heartbeat",
            "command": command,
            "elapsed_seconds": elapsed_seconds,
        },
    )
    return now


def run_command(
    command,
    vol_id,
    step,
    base_progress,
    report_fn,
    cancel_check_fn=None,
    timeout_seconds=None,
):
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

        now = time.monotonic()
        elapsed_seconds = now - start_time
        if timeout_seconds and elapsed_seconds >= float(timeout_seconds):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            report_fn(
                vol_id,
                step,
                base_progress,
                log=(
                    f"Command exceeded its {float(timeout_seconds):.0f}s time budget "
                    f"and was terminated: {' '.join(command)}"
                ),
                details={
                    "event": "command_timeout",
                    "command": command,
                    "elapsed_seconds": round(elapsed_seconds, 3),
                    "timeout_seconds": float(timeout_seconds),
                },
            )
            raise TimeoutError(
                f"Command exceeded {float(timeout_seconds):.0f}s: {' '.join(command)}"
            )

        try:
            line = process.stdout.readline()
            if line:
                clean_line = line.strip()
                if clean_line:
                    report_fn(vol_id, step, base_progress, log=clean_line)
            else:
                if process.poll() is not None:
                    break
                last_heartbeat_time = _report_heartbeat_if_due(
                    command=command,
                    vol_id=vol_id,
                    step=step,
                    base_progress=base_progress,
                    report_fn=report_fn,
                    start_time=start_time,
                    last_heartbeat_time=last_heartbeat_time,
                    heartbeat_interval=heartbeat_interval,
                )
                time.sleep(0.1)
        except IOError:
            if process.poll() is not None:
                break
            last_heartbeat_time = _report_heartbeat_if_due(
                command=command,
                vol_id=vol_id,
                step=step,
                base_progress=base_progress,
                report_fn=report_fn,
                start_time=start_time,
                last_heartbeat_time=last_heartbeat_time,
                heartbeat_interval=heartbeat_interval,
            )
            time.sleep(0.1)

    # Restore blocking mode and drain any remaining output
    fcntl.fcntl(process.stdout, fcntl.F_SETFL, flags)
    for line in process.stdout:
        clean_line = line.strip()
        if clean_line:
            report_fn(vol_id, step, base_progress, log=clean_line)

    return_code = process.wait()
    elapsed_seconds = time.monotonic() - start_time
    if return_code != 0:
        if cancel_check_fn:
            try:
                cancel_check_fn(None)
            except Exception:
                report_fn(vol_id, step, base_progress, details={"event": "command_cancelled", "command": command, "return_code": return_code})
                raise
        report_fn(
            vol_id,
            step,
            base_progress,
            details={
                "event": "command_failed",
                "command": command,
                "return_code": return_code,
                "elapsed_seconds": round(elapsed_seconds, 3),
            },
        )
        if return_code < 0:
            signal_number = -return_code
            try:
                signal_name = signal.Signals(signal_number).name
            except ValueError:
                signal_name = f"SIG{signal_number}"
            hint = " Likely causes: OOM kill, pod eviction, or manual termination." if signal_name == "SIGKILL" else ""
            raise RuntimeError(f"Command '{' '.join(command)}' died with {signal_name}.{hint}")
        raise subprocess.CalledProcessError(return_code, command)
    report_fn(
        vol_id,
        step,
        base_progress,
        details={
            "event": "command_finished",
            "command": command,
            "return_code": 0,
            "elapsed_seconds": round(elapsed_seconds, 3),
        },
    )
