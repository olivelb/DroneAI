"""Deterministic image selection for HD facade reconstructions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from shared.dji_metadata import image_sequence_number, parse_aerial_xmp


@dataclass(frozen=True)
class FacadeImage:
    path: Path
    pitch_deg: float
    yaw_deg: float | None
    sequence: int | None


def _same_file(first: Path, second: Path) -> bool:
    if first.stat().st_size != second.stat().st_size:
        return False
    digests = []
    for path in (first, second):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        digests.append(digest.digest())
    return digests[0] == digests[1]


def deduplicate_identical_basenames(paths: Iterable[Path]) -> tuple[list[Path], list[dict]]:
    """Drop byte-identical duplicate names and reject ambiguous collisions."""

    unique: dict[str, Path] = {}
    duplicates: list[dict] = []
    for path in sorted(paths):
        previous = unique.get(path.name)
        if previous is None:
            unique[path.name] = path
            continue
        if not _same_file(previous, path):
            raise ValueError(
                "Facade input contains different images with the same filename: "
                f"{previous} and {path}"
            )
        duplicates.append({"kept": str(previous), "discarded": str(path)})
    return list(unique.values()), duplicates


def parse_excluded_basename_ranges(
    value: str | Iterable[tuple[str, str]] | None,
) -> list[tuple[str, str]]:
    """Parse inclusive basename ranges used to omit coherent detail sequences.

    Text values use ``START..END;START..END``. Paths are deliberately rejected:
    ranges apply to image basenames after duplicate-name validation, regardless
    of how an input dataset is organized into folders.
    """

    if value is None:
        return []
    if isinstance(value, str):
        entries: list[tuple[str, str]] = []
        for raw_entry in value.split(";"):
            entry = raw_entry.strip()
            if not entry:
                continue
            bounds = [part.strip() for part in entry.split("..", 1)]
            if len(bounds) != 2 or not all(bounds):
                raise ValueError(
                    "Facade excluded image ranges must use START..END, separated by semicolons"
                )
            entries.append((bounds[0], bounds[1]))
    else:
        entries = [(str(start).strip(), str(end).strip()) for start, end in value]

    normalized: list[tuple[str, str]] = []
    for start, end in entries:
        for label, name in (("start", start), ("end", end)):
            if not name or Path(name).name != name or "/" in name or "\\" in name:
                raise ValueError(
                    f"Facade excluded range {label} must be an image basename: {name!r}"
                )
        if start.casefold() > end.casefold():
            raise ValueError(
                f"Facade excluded range start must sort before its end: {start}..{end}"
            )
        normalized.append((start, end))

    normalized.sort(key=lambda bounds: (bounds[0].casefold(), bounds[1].casefold()))
    for previous, current in zip(normalized, normalized[1:]):
        if current[0].casefold() <= previous[1].casefold():
            raise ValueError(
                "Facade excluded image ranges must not overlap: "
                f"{previous[0]}..{previous[1]} and {current[0]}..{current[1]}"
            )
    return normalized


def exclude_basename_ranges(
    paths: Iterable[Path],
    ranges: str | Iterable[tuple[str, str]] | None,
) -> tuple[list[Path], dict]:
    """Exclude inclusive basename ranges and return an auditable report."""

    normalized_ranges = parse_excluded_basename_ranges(ranges)
    candidates = sorted(paths)
    if not normalized_ranges:
        return candidates, {
            "excluded_image_ranges": [],
            "excluded_image_count": 0,
            "excluded_basenames": [],
        }

    kept: list[Path] = []
    excluded_by_range: list[list[str]] = [[] for _ in normalized_ranges]
    for path in candidates:
        folded_name = path.name.casefold()
        matched_index = next(
            (
                index
                for index, (start, end) in enumerate(normalized_ranges)
                if start.casefold() <= folded_name <= end.casefold()
            ),
            None,
        )
        if matched_index is None:
            kept.append(path)
        else:
            excluded_by_range[matched_index].append(path.name)

    empty_ranges = [
        f"{start}..{end}"
        for (start, end), excluded in zip(normalized_ranges, excluded_by_range)
        if not excluded
    ]
    if empty_ranges:
        raise ValueError(
            "Facade excluded image range matched no input image: "
            + ", ".join(empty_ranges)
        )

    range_reports = []
    for (start, end), excluded in zip(normalized_ranges, excluded_by_range):
        range_reports.append(
            {
                "start": start,
                "end": end,
                "excluded_images": len(excluded),
                "first_excluded": excluded[0],
                "last_excluded": excluded[-1],
            }
        )
    excluded_basenames = sorted(
        name for names in excluded_by_range for name in names
    )
    return kept, {
        "excluded_image_ranges": range_reports,
        "excluded_image_count": len(excluded_basenames),
        "excluded_basenames": excluded_basenames,
    }


def select_facade_images(
    paths: Iterable[Path],
    *,
    max_abs_pitch_deg: float = 40.0,
    min_pass_images: int = 12,
    pitch_continuity_deg: float = 8.0,
    target_yaw_deg: float | None = None,
    yaw_tolerance_deg: float = 35.0,
    excluded_basename_ranges: str | Iterable[tuple[str, str]] | None = None,
) -> tuple[list[Path], dict]:
    """Keep coherent horizontal/oblique passes and reject isolated detail shots.

    DJI gimbal pitch is approximately 0 degrees for a horizontal camera and
    -90 degrees for nadir. A pass is a sufficiently long, filename-contiguous
    run with a stable pitch. When ``target_yaw_deg`` is supplied, circular yaw
    distance limits the solve to one wall while retaining useful oblique views.
    Without a target, yaw remains unrestricted for articulated facades.
    """

    candidates, duplicates = deduplicate_identical_basenames(paths)
    unique_image_count = len(candidates)
    candidates, exclusion_report = exclude_basename_ranges(
        candidates,
        excluded_basename_ranges,
    )
    records: list[FacadeImage] = []
    missing_attitude: list[str] = []
    rejected_pitch: list[str] = []
    rejected_yaw: list[str] = []
    for path in candidates:
        metadata = parse_aerial_xmp(path)
        attitude = metadata.get("gimbal_attitude_deg") or {}
        pitch = attitude.get("pitch")
        if pitch is None:
            missing_attitude.append(str(path))
            continue
        if abs(float(pitch)) > max_abs_pitch_deg:
            rejected_pitch.append(str(path))
            continue
        yaw = attitude.get("yaw")
        if target_yaw_deg is not None:
            if yaw is None:
                rejected_yaw.append(str(path))
                continue
            yaw_distance = abs(
                (float(yaw) - float(target_yaw_deg) + 180.0) % 360.0 - 180.0
            )
            if yaw_distance > float(yaw_tolerance_deg):
                rejected_yaw.append(str(path))
                continue
        records.append(
            FacadeImage(
                path=path,
                pitch_deg=float(pitch),
                yaw_deg=None if yaw is None else float(yaw),
                sequence=image_sequence_number(path),
            )
        )

    by_folder: dict[Path, list[FacadeImage]] = {}
    for record in records:
        by_folder.setdefault(record.path.parent, []).append(record)

    selected: list[Path] = []
    pass_reports: list[dict] = []
    rejected_short: list[str] = []
    for folder, folder_records in sorted(by_folder.items(), key=lambda item: str(item[0])):
        folder_records.sort(
            key=lambda item: (
                item.sequence is None,
                item.sequence if item.sequence is not None else item.path.name,
            )
        )
        runs: list[list[FacadeImage]] = []
        current: list[FacadeImage] = []
        for record in folder_records:
            contiguous = bool(current)
            if current:
                previous = current[-1]
                sequence_ok = (
                    previous.sequence is None
                    or record.sequence is None
                    or 0 < record.sequence - previous.sequence <= 3
                )
                pitch_ok = abs(record.pitch_deg - previous.pitch_deg) <= pitch_continuity_deg
                contiguous = sequence_ok and pitch_ok
            if not contiguous and current:
                runs.append(current)
                current = []
            current.append(record)
        if current:
            runs.append(current)

        dominant_run_length = max((len(run) for run in runs), default=0)
        effective_minimum = max(
            min_pass_images,
            int(dominant_run_length * 0.25),
        )
        for run in runs:
            accepted = len(run) >= effective_minimum
            if accepted:
                selected.extend(record.path for record in run)
            else:
                rejected_short.extend(str(record.path) for record in run)
            pass_reports.append(
                {
                    "folder": str(folder),
                    "first": run[0].path.name,
                    "last": run[-1].path.name,
                    "images": len(run),
                    "mean_pitch_deg": sum(item.pitch_deg for item in run) / len(run),
                    "effective_minimum_images": effective_minimum,
                    "accepted": accepted,
                }
            )

    if len(selected) < max(3, min_pass_images):
        raise ValueError(
            "Automatic facade selection found too few coherent horizontal/oblique "
            f"images ({len(selected)}). Use facade_selection_mode=all or lower "
            "facade_min_pass_images after checking the dataset."
        )

    return sorted(selected), {
        "schema_version": 2,
        "mode": "auto",
        "input_images": unique_image_count + len(duplicates),
        "unique_images": unique_image_count,
        "selected_images": len(selected),
        "max_abs_pitch_deg": max_abs_pitch_deg,
        "min_pass_images": min_pass_images,
        "duplicate_basenames": duplicates,
        "missing_attitude_count": len(missing_attitude),
        "rejected_pitch_count": len(rejected_pitch),
        "target_yaw_deg": target_yaw_deg,
        "yaw_tolerance_deg": yaw_tolerance_deg if target_yaw_deg is not None else None,
        "rejected_yaw_count": len(rejected_yaw),
        "rejected_short_run_count": len(rejected_short),
        "passes": pass_reports,
        **exclusion_report,
    }
