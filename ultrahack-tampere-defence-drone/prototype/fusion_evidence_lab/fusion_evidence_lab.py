"""Multi-sensor fusion and evidence timeline prototype."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import wave
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np


PROGRESS_PREFIX = "PROGRESS "
DEFAULT_OUTPUT_ROOT = Path(__file__).with_name("outputs")
DEFAULT_EXTRA_VIDEO = Path.home() / "Downloads" / "Telegram Desktop" / "VID_20260610_124556_051.mp4"
DEFAULT_ARCHIVE = Path(
    r"C:\Users\teres\OneDrive\Documents\New project-data-collection-firmware"
    r"\ultrahack-tampere-defence-drone\prototype\data_collection_firmware"
    r"\outputs\data_collection_20260610T094549_ffd4bda6_full_recording.zip"
)


@dataclass(frozen=True)
class MotionConfig:
    diff_threshold: int = 18
    min_area: float = 30.0
    blur_kernel: int = 5
    morph_kernel: int = 3
    trail_frames: int = 3
    max_motion_ratio: float = 0.18
    analysis_scale: float = 0.5

    def normalized(self) -> "MotionConfig":
        return MotionConfig(
            diff_threshold=int(np.clip(self.diff_threshold, 1, 255)),
            min_area=max(0.0, float(self.min_area)),
            blur_kernel=odd_kernel(self.blur_kernel),
            morph_kernel=odd_kernel(self.morph_kernel),
            trail_frames=max(0, int(self.trail_frames)),
            max_motion_ratio=float(np.clip(self.max_motion_ratio, 0.01, 1.0)),
            analysis_scale=float(np.clip(self.analysis_scale, 0.05, 1.0)),
        )


@dataclass(frozen=True)
class SourceVideo:
    source_id: str
    label: str
    path: Path
    origin: str
    timing_path: Path | None = None
    creation_utc_ns: int | None = None
    metadata_offset_s: float | None = None


@dataclass
class PerspectiveTransform:
    matrix: np.ndarray
    target_size: tuple[int, int]
    mode: str
    reference_id: str | None
    diagnostics: dict[str, Any]


def emit_progress(message: str) -> None:
    print(f"{PROGRESS_PREFIX}{message}", flush=True)


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "source"


def odd_kernel(value: int) -> int:
    value = max(1, int(value))
    return value if value % 2 == 1 else value + 1


def utc_ns_to_iso(utc_ns: int | None) -> str | None:
    if utc_ns is None:
        return None
    return datetime.fromtimestamp(utc_ns / 1_000_000_000, tz=timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def parse_iso_to_utc_ns(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = f"{cleaned[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path or not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
            if limit is not None and len(rows) >= limit:
                break
    return rows


def load_json_or_jsonl(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    parsed = json.loads(text)
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict) and "records" in parsed and isinstance(parsed["records"], list):
        return [item for item in parsed["records"] if isinstance(item, dict)]
    if isinstance(parsed, dict):
        return [parsed]
    return []


def extract_archive(archive_path: Path, run_dir: Path) -> tuple[dict[str, Any], Path]:
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive not found: {archive_path}")
    extract_dir = run_dir / "extracted_archive"
    manifest_candidates = list(extract_dir.rglob("session_manifest.json")) if extract_dir.exists() else []
    if not manifest_candidates:
        emit_progress("Extracting data collection archive")
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "r") as zip_file:
            zip_file.extractall(extract_dir)
        manifest_candidates = list(extract_dir.rglob("session_manifest.json"))
    if not manifest_candidates:
        raise FileNotFoundError("Archive does not contain session_manifest.json")
    manifest_path = manifest_candidates[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest, manifest_path.parent


def find_extracted_file(session_dir: Path, original_path: str | None, fallback_name: str | None = None) -> Path | None:
    names: list[str] = []
    if original_path:
        names.append(Path(original_path).name)
    if fallback_name:
        names.append(fallback_name)
    for name in names:
        if not name:
            continue
        direct = session_dir / name
        if direct.exists():
            return direct
        matches = list(session_dir.rglob(name))
        if matches:
            return matches[0]
    return None


def stream_sources_from_manifest(manifest: dict[str, Any], session_dir: Path) -> tuple[list[SourceVideo], list[dict[str, Any]]]:
    videos: list[SourceVideo] = []
    audios: list[dict[str, Any]] = []
    for stream in manifest.get("streams", []):
        if stream.get("errors") and not stream.get("path"):
            continue
        kind = stream.get("kind")
        slug = str(stream.get("slug") or slugify(str(stream.get("name") or kind or "stream")))
        media_path = find_extracted_file(session_dir, stream.get("path"))
        timing_path = find_extracted_file(session_dir, stream.get("timing_path"))
        if kind == "video" and media_path:
            videos.append(
                SourceVideo(
                    source_id=slug,
                    label=str(stream.get("name") or slug),
                    path=media_path,
                    origin="archive",
                    timing_path=timing_path,
                    creation_utc_ns=int(stream["started_utc_ns"]) if stream.get("started_utc_ns") else None,
                )
            )
        elif kind == "audio" and media_path:
            audios.append(
                {
                    "source_id": slug,
                    "label": str(stream.get("name") or slug),
                    "path": media_path,
                    "timing_path": timing_path,
                    "started_utc_ns": int(stream["started_utc_ns"]) if stream.get("started_utc_ns") else None,
                }
            )
    return videos, audios


def ffprobe_json(path: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["ffprobe", "-hide_banner", "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return {}
    if completed.returncode != 0 or not completed.stdout.strip():
        return {}
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}


def probe_video(path: Path) -> dict[str, Any]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    if fps <= 0:
        fps = 30.0
    metadata = ffprobe_json(path)
    creation_utc_ns: int | None = None
    tags = metadata.get("format", {}).get("tags", {}) if metadata else {}
    creation_utc_ns = parse_iso_to_utc_ns(tags.get("creation_time"))
    if creation_utc_ns is None:
        for stream in metadata.get("streams", []) if metadata else []:
            creation_utc_ns = parse_iso_to_utc_ns(stream.get("tags", {}).get("creation_time"))
            if creation_utc_ns is not None:
                break
    return {
        "path": str(path),
        "width": width,
        "height": height,
        "fps": fps,
        "frame_count": frame_count,
        "duration_s": frame_count / fps if fps > 0 and frame_count else None,
        "creation_utc_ns": creation_utc_ns,
        "creation_utc": utc_ns_to_iso(creation_utc_ns),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in metadata.get("streams", [])) if metadata else None,
        "ffprobe": metadata,
    }


def load_frame_utc_by_index(path: Path | None) -> dict[int, int]:
    if not path or not path.exists():
        return {}
    mapping: dict[int, int] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if "frame_index" in item and "utc_ns" in item:
                mapping[int(item["frame_index"])] = int(item["utc_ns"])
    return mapping


def load_perspective_spec(raw: str | Path | None) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, Path):
        text = raw.read_text(encoding="utf-8")
    else:
        raw_text = str(raw).strip()
        path = Path(raw_text)
        text = path.read_text(encoding="utf-8") if path.exists() else raw_text
    if not text.strip():
        return {}
    parsed = json.loads(text)
    if "sources" in parsed and isinstance(parsed["sources"], dict):
        return parsed["sources"]
    return parsed if isinstance(parsed, dict) else {}


def points_to_pixels(points: Iterable[Iterable[float]], width: int, height: int) -> np.ndarray:
    pts = np.array([[float(x), float(y)] for x, y in points], dtype=np.float32)
    if pts.shape != (4, 2):
        raise ValueError("Perspective points must contain exactly four [x, y] pairs.")
    if float(np.nanmax(np.abs(pts))) <= 1.5:
        pts[:, 0] *= max(1, width - 1)
        pts[:, 1] *= max(1, height - 1)
    return pts


def perspective_matrix_for_source(
    perspective_sources: dict[str, Any], source_id: str, width: int, height: int
) -> np.ndarray | None:
    spec = perspective_sources.get(source_id)
    if not spec:
        return None
    src = spec.get("src") or spec.get("source") or spec.get("source_points")
    dst = spec.get("dst") or spec.get("destination") or spec.get("destination_points")
    if not src:
        return None
    if not dst:
        dst = [[0, 0], [1, 0], [1, 1], [0, 1]]
    src_px = points_to_pixels(src, width, height)
    dst_px = points_to_pixels(dst, width, height)
    return cv2.getPerspectiveTransform(src_px, dst_px)


def apply_perspective(frame: np.ndarray, matrix: np.ndarray | None) -> np.ndarray:
    if matrix is None:
        return frame
    height, width = frame.shape[:2]
    return cv2.warpPerspective(frame, matrix, (width, height), flags=cv2.INTER_LINEAR)


def apply_perspective_transform(frame: np.ndarray, transform: PerspectiveTransform | None) -> np.ndarray:
    if transform is None:
        return frame
    target_width, target_height = transform.target_size
    return cv2.warpPerspective(frame, transform.matrix, (target_width, target_height), flags=cv2.INTER_LINEAR)


def read_video_frame_at_index(path: Path, frame_index: int) -> np.ndarray | None:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(frame_index)))
        ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()


def resize_for_features(frame: np.ndarray, max_dim: int = 960) -> tuple[np.ndarray, float, float]:
    height, width = frame.shape[:2]
    scale = min(1.0, float(max_dim) / float(max(width, height, 1)))
    if scale >= 0.999:
        return frame, 1.0, 1.0
    resized = cv2.resize(frame, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
    return resized, resized.shape[1] / float(width), resized.shape[0] / float(height)


def source_session_range(
    source: SourceVideo,
    probe: dict[str, Any],
    frame_utc_by_index: dict[int, int],
    session_start_utc_ns: int | None,
) -> tuple[float, float] | None:
    if frame_utc_by_index and session_start_utc_ns:
        values = [(utc_ns - session_start_utc_ns) / 1_000_000_000 for utc_ns in frame_utc_by_index.values()]
        return min(values), max(values)
    duration = float(probe.get("duration_s") or 0.0)
    if source.metadata_offset_s is not None and duration > 0:
        return source.metadata_offset_s, source.metadata_offset_s + duration
    if source.creation_utc_ns is not None and session_start_utc_ns is not None and duration > 0:
        start = (source.creation_utc_ns - session_start_utc_ns) / 1_000_000_000
        return start, start + duration
    return None


def frame_index_near_session_s(
    source: SourceVideo,
    probe: dict[str, Any],
    frame_utc_by_index: dict[int, int],
    session_start_utc_ns: int | None,
    session_s: float,
) -> int | None:
    fps = float(probe.get("fps") or 30.0)
    frame_count = int(probe.get("frame_count") or 0)
    if frame_utc_by_index and session_start_utc_ns:
        target_ns = session_start_utc_ns + int(session_s * 1_000_000_000)
        return min(frame_utc_by_index, key=lambda index: abs(frame_utc_by_index[index] - target_ns))
    if source.metadata_offset_s is not None:
        local_s = session_s - source.metadata_offset_s
    elif source.creation_utc_ns is not None and session_start_utc_ns is not None:
        local_s = session_s - ((source.creation_utc_ns - session_start_utc_ns) / 1_000_000_000)
    else:
        local_s = float(probe.get("duration_s") or 0.0) / 2.0
    if local_s < 0:
        return None
    index = int(round(local_s * fps))
    if frame_count:
        index = int(np.clip(index, 0, max(0, frame_count - 1)))
    return index


def session_s_for_frame_index(
    source: SourceVideo,
    probe: dict[str, Any],
    frame_utc_by_index: dict[int, int],
    session_start_utc_ns: int | None,
    frame_index: int,
) -> float | None:
    fps = float(probe.get("fps") or 30.0)
    if frame_index in frame_utc_by_index and session_start_utc_ns:
        return (frame_utc_by_index[frame_index] - session_start_utc_ns) / 1_000_000_000
    local_s = frame_index / fps if fps > 0 else float(frame_index)
    if source.metadata_offset_s is not None:
        return source.metadata_offset_s + local_s
    if source.creation_utc_ns is not None and session_start_utc_ns is not None:
        return ((source.creation_utc_ns - session_start_utc_ns) / 1_000_000_000) + local_s
    return None


def representative_frame_indices(probe: dict[str, Any], count: int) -> list[int]:
    frame_count = int(probe.get("frame_count") or 0)
    if frame_count <= 1:
        return [0]
    if count <= 1:
        return [frame_count // 2]
    start = max(0, int(frame_count * 0.18))
    stop = min(frame_count - 1, int(frame_count * 0.82))
    return [int(value) for value in np.linspace(start, stop, max(1, count)).tolist()]


def transformed_corner_area(matrix: np.ndarray, source_size: tuple[int, int], target_size: tuple[int, int]) -> float:
    source_width, source_height = source_size
    target_width, target_height = target_size
    corners = np.array(
        [[[0.0, 0.0], [source_width - 1.0, 0.0], [source_width - 1.0, source_height - 1.0], [0.0, source_height - 1.0]]],
        dtype=np.float32,
    )
    transformed = cv2.perspectiveTransform(corners, matrix)[0]
    if not np.isfinite(transformed).all():
        return 0.0
    area = float(abs(cv2.contourArea(transformed)))
    return area / float(max(1, target_width * target_height))


def estimate_homography_from_shared_landmarks(
    source_frame: np.ndarray,
    reference_frame: np.ndarray,
    min_matches: int,
    min_inliers: int,
) -> tuple[np.ndarray | None, dict[str, Any], np.ndarray | None]:
    source_small, sx, sy = resize_for_features(source_frame)
    reference_small, rx, ry = resize_for_features(reference_frame)
    orb = cv2.ORB_create(nfeatures=5000, fastThreshold=7)
    source_gray = cv2.cvtColor(source_small, cv2.COLOR_BGR2GRAY)
    reference_gray = cv2.cvtColor(reference_small, cv2.COLOR_BGR2GRAY)
    source_kp, source_desc = orb.detectAndCompute(source_gray, None)
    reference_kp, reference_desc = orb.detectAndCompute(reference_gray, None)
    diagnostics: dict[str, Any] = {
        "source_keypoints": len(source_kp or []),
        "reference_keypoints": len(reference_kp or []),
        "good_matches": 0,
        "inliers": 0,
        "inlier_ratio": 0.0,
        "accepted": False,
        "reason": "",
    }
    if source_desc is None or reference_desc is None or not source_kp or not reference_kp:
        diagnostics["reason"] = "not_enough_keypoints"
        return None, diagnostics, None

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    raw_matches = matcher.knnMatch(source_desc, reference_desc, k=2)
    good_matches = []
    for pair in raw_matches:
        if len(pair) != 2:
            continue
        first, second = pair
        if first.distance < 0.74 * second.distance:
            good_matches.append(first)
    diagnostics["good_matches"] = len(good_matches)
    if len(good_matches) < min_matches:
        diagnostics["reason"] = "not_enough_good_matches"
        return None, diagnostics, None

    source_points = np.float32(
        [[source_kp[match.queryIdx].pt[0] / sx, source_kp[match.queryIdx].pt[1] / sy] for match in good_matches]
    )
    reference_points = np.float32(
        [[reference_kp[match.trainIdx].pt[0] / rx, reference_kp[match.trainIdx].pt[1] / ry] for match in good_matches]
    )
    matrix, inlier_mask = cv2.findHomography(source_points, reference_points, cv2.RANSAC, 5.0)
    if matrix is None or inlier_mask is None:
        diagnostics["reason"] = "homography_failed"
        return None, diagnostics, None

    inliers = int(inlier_mask.ravel().sum())
    inlier_ratio = inliers / float(max(1, len(good_matches)))
    diagnostics["inliers"] = inliers
    diagnostics["inlier_ratio"] = inlier_ratio
    area_ratio = transformed_corner_area(
        matrix,
        (source_frame.shape[1], source_frame.shape[0]),
        (reference_frame.shape[1], reference_frame.shape[0]),
    )
    diagnostics["warped_area_ratio"] = area_ratio
    if inliers < min_inliers:
        diagnostics["reason"] = "not_enough_ransac_inliers"
        return None, diagnostics, None
    if inlier_ratio < 0.18:
        diagnostics["reason"] = "low_inlier_ratio"
        return None, diagnostics, None
    if not (0.08 <= area_ratio <= 3.8):
        diagnostics["reason"] = "unstable_warp_area"
        return None, diagnostics, None

    inlier_matches = [match for match, keep in zip(good_matches, inlier_mask.ravel()) if keep]
    preview_matches = inlier_matches[:80]
    preview = cv2.drawMatches(
        source_small,
        source_kp,
        reference_small,
        reference_kp,
        preview_matches,
        None,
        flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS,
    )
    diagnostics["accepted"] = True
    diagnostics["reason"] = "accepted"
    diagnostics["score"] = float(inliers * inlier_ratio)
    return matrix, diagnostics, preview


def save_auto_perspective_preview(
    out_dir: Path,
    source_id: str,
    reference_id: str,
    source_frame: np.ndarray,
    reference_frame: np.ndarray,
    matrix: np.ndarray,
    match_preview: np.ndarray | None,
) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    stem = f"{slugify(source_id)}_to_{slugify(reference_id)}"
    if match_preview is not None:
        match_path = out_dir / f"{stem}_matches.png"
        cv2.imwrite(str(match_path), match_preview)
        paths["matches_path"] = str(match_path)
    warped = cv2.warpPerspective(source_frame, matrix, (reference_frame.shape[1], reference_frame.shape[0]))
    alpha = cv2.addWeighted(reference_frame, 0.55, warped, 0.45, 0.0)
    preview = np.hstack(
        [
            cv2.resize(reference_frame, (reference_frame.shape[1] // 2, reference_frame.shape[0] // 2)),
            cv2.resize(warped, (reference_frame.shape[1] // 2, reference_frame.shape[0] // 2)),
            cv2.resize(alpha, (reference_frame.shape[1] // 2, reference_frame.shape[0] // 2)),
        ]
    )
    cv2.putText(preview, "reference", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(
        preview,
        "auto-warped source",
        (reference_frame.shape[1] // 2 + 10, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        preview,
        "overlay",
        (reference_frame.shape[1] + 10, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    warp_path = out_dir / f"{stem}_warp_preview.png"
    cv2.imwrite(str(warp_path), preview)
    paths["warp_preview_path"] = str(warp_path)
    return paths


def build_auto_perspective_transforms(
    sources: list[SourceVideo],
    session_start_utc_ns: int | None,
    manual_sources: dict[str, Any],
    out_dir: Path,
    enabled: bool,
    reference_id: str,
    sample_count: int,
    min_matches: int,
    min_inliers: int,
) -> tuple[dict[str, PerspectiveTransform], dict[str, Any]]:
    diagnostics: dict[str, Any] = {
        "enabled": enabled,
        "reference_id": None,
        "transforms": {},
        "notes": [],
    }
    if not enabled:
        diagnostics["notes"].append("auto_perspective_disabled")
        return {}, diagnostics
    if len(sources) < 2:
        diagnostics["notes"].append("need_at_least_two_video_sources")
        return {}, diagnostics

    probes = {source.source_id: probe_video(source.path) for source in sources}
    timing_maps = {source.source_id: load_frame_utc_by_index(source.timing_path) for source in sources}
    source_by_id = {source.source_id: source for source in sources}
    if reference_id and reference_id in source_by_id:
        reference = source_by_id[reference_id]
    else:
        archive_sources = [source for source in sources if source.origin == "archive"]
        reference = max(archive_sources or sources, key=lambda src: probes[src.source_id]["width"] * probes[src.source_id]["height"])
    diagnostics["reference_id"] = reference.source_id

    reference_probe = probes[reference.source_id]
    reference_timing = timing_maps[reference.source_id]
    reference_range = source_session_range(reference, reference_probe, reference_timing, session_start_utc_ns)
    transforms: dict[str, PerspectiveTransform] = {}
    preview_dir = out_dir / "auto_perspective"

    for source in sources:
        if source.source_id == reference.source_id:
            diagnostics["transforms"][source.source_id] = {"accepted": False, "reason": "reference_source"}
            continue
        if source.source_id in manual_sources:
            diagnostics["transforms"][source.source_id] = {"accepted": False, "reason": "manual_override_present"}
            continue
        source_probe = probes[source.source_id]
        source_timing = timing_maps[source.source_id]
        source_range = source_session_range(source, source_probe, source_timing, session_start_utc_ns)
        pair_diag: dict[str, Any] = {
            "source_id": source.source_id,
            "reference_id": reference.source_id,
            "accepted": False,
            "source_range_s": source_range,
            "reference_range_s": reference_range,
            "attempts": [],
        }
        sample_pairs: list[tuple[float | None, int, int]] = []
        if source_range and reference_range:
            overlap_start = max(source_range[0], reference_range[0])
            overlap_stop = min(source_range[1], reference_range[1])
            if overlap_stop > overlap_start:
                if overlap_stop - overlap_start > 2.0:
                    overlap_start += 0.5
                    overlap_stop -= 0.5
                for session_s in np.linspace(overlap_start, overlap_stop, max(1, int(sample_count))).tolist():
                    source_index = frame_index_near_session_s(
                        source, source_probe, source_timing, session_start_utc_ns, float(session_s)
                    )
                    reference_index = frame_index_near_session_s(
                        reference, reference_probe, reference_timing, session_start_utc_ns, float(session_s)
                    )
                    if source_index is not None and reference_index is not None:
                        sample_pairs.append((float(session_s), int(source_index), int(reference_index)))
            else:
                pair_diag["fallback"] = "no_session_overlap_using_representative_frames"
        else:
            pair_diag["fallback"] = "missing_session_timing_using_representative_frames"

        if not sample_pairs:
            source_indices = representative_frame_indices(source_probe, max(1, int(sample_count)))
            reference_indices = representative_frame_indices(reference_probe, max(1, int(sample_count)))
            sample_pairs = [(None, source_index, reference_index) for source_index, reference_index in zip(source_indices, reference_indices)]

        best: tuple[float, np.ndarray, dict[str, Any], np.ndarray | None, np.ndarray, np.ndarray] | None = None
        for session_s, source_index, reference_index in sample_pairs:
            source_frame = read_video_frame_at_index(source.path, source_index)
            reference_frame = read_video_frame_at_index(reference.path, reference_index)
            if source_frame is None or reference_frame is None:
                continue
            matrix, attempt_diag, match_preview = estimate_homography_from_shared_landmarks(
                source_frame,
                reference_frame,
                min_matches,
                min_inliers,
            )
            attempt_diag.update(
                {
                    "session_s": float(session_s) if session_s is not None else None,
                    "source_frame_index": int(source_index),
                    "reference_frame_index": int(reference_index),
                }
            )
            pair_diag["attempts"].append(attempt_diag)
            if matrix is not None:
                score = float(attempt_diag.get("score", 0.0))
                if best is None or score > best[0]:
                    best = (score, matrix, attempt_diag, match_preview, source_frame, reference_frame)

        if best is None:
            pair_diag["reason"] = "no_reliable_landmark_match"
            diagnostics["transforms"][source.source_id] = pair_diag
            continue
        score, matrix, best_diag, match_preview, source_frame, reference_frame = best
        source_local_s = float(best_diag["source_frame_index"]) / float(source_probe.get("fps") or 30.0)
        reference_session_s = session_s_for_frame_index(
            reference,
            reference_probe,
            reference_timing,
            session_start_utc_ns,
            int(best_diag["reference_frame_index"]),
        )
        if reference_session_s is not None:
            pair_diag["visual_sync_offset_s"] = reference_session_s - source_local_s
            pair_diag["visual_sync_basis"] = {
                "source_frame_index": int(best_diag["source_frame_index"]),
                "source_local_s": source_local_s,
                "reference_frame_index": int(best_diag["reference_frame_index"]),
                "reference_session_s": reference_session_s,
            }
        preview_paths = save_auto_perspective_preview(
            preview_dir,
            source.source_id,
            reference.source_id,
            source_frame,
            reference_frame,
            matrix,
            match_preview,
        )
        pair_diag.update(
            {
                "accepted": True,
                "reason": "accepted",
                "best": best_diag,
                "preview": preview_paths,
                "target_size": [int(reference_frame.shape[1]), int(reference_frame.shape[0])],
            }
        )
        transforms[source.source_id] = PerspectiveTransform(
            matrix=matrix,
            target_size=(int(reference_frame.shape[1]), int(reference_frame.shape[0])),
            mode="auto_landmark_homography",
            reference_id=reference.source_id,
            diagnostics=pair_diag,
        )
        diagnostics["transforms"][source.source_id] = pair_diag
    return transforms, diagnostics


def draw_label(image: np.ndarray, text: str, x: int, y: int, color: tuple[int, int, int]) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.45
    thickness = 1
    (tw, th), baseline = cv2.getTextSize(text, font, scale, thickness)
    y0 = max(th + baseline + 2, y)
    cv2.rectangle(image, (x, y0 - th - baseline - 4), (x + tw + 6, y0 + 3), color, -1)
    cv2.putText(image, text, (x + 3, y0 - baseline - 1), font, scale, (255, 255, 255), thickness, cv2.LINE_AA)


def draw_detections(frame: np.ndarray, detections: list[dict[str, Any]], title: str) -> np.ndarray:
    output = frame.copy()
    cv2.putText(output, title, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(output, title, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (20, 20, 20), 1, cv2.LINE_AA)
    for detection in detections:
        x1 = int(detection["x1"])
        y1 = int(detection["y1"])
        x2 = int(detection["x2"])
        y2 = int(detection["y2"])
        cv2.rectangle(output, (x1, y1), (x2, y2), (0, 70, 255), 2)
        draw_label(output, f"motion {float(detection['confidence']):.2f}", x1, max(0, y1 - 4), (0, 70, 255))
    return output


def detect_motion(
    prev_gray: np.ndarray | None,
    frame: np.ndarray,
    config: MotionConfig,
    mask_history: list[np.ndarray],
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]], float, bool]:
    height, width = frame.shape[:2]
    scale = config.analysis_scale
    small = cv2.resize(frame, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    if config.blur_kernel > 1:
        gray = cv2.GaussianBlur(gray, (config.blur_kernel, config.blur_kernel), 0)
    if prev_gray is None:
        return gray, np.zeros((height, width), dtype=np.uint8), [], 0.0, False

    diff = cv2.absdiff(prev_gray, gray)
    _, mask_small = cv2.threshold(diff, config.diff_threshold, 255, cv2.THRESH_BINARY)
    if config.morph_kernel > 1:
        kernel = np.ones((config.morph_kernel, config.morph_kernel), dtype=np.uint8)
        mask_small = cv2.morphologyEx(mask_small, cv2.MORPH_OPEN, kernel)
        mask_small = cv2.morphologyEx(mask_small, cv2.MORPH_CLOSE, kernel)
        mask_small = cv2.dilate(mask_small, kernel, iterations=1)

    current_motion_ratio = float(np.count_nonzero(mask_small)) / float(mask_small.size or 1)
    global_rejected = current_motion_ratio > config.max_motion_ratio
    if global_rejected:
        mask_full = cv2.resize(mask_small, (width, height), interpolation=cv2.INTER_NEAREST)
        return gray, mask_full, [], current_motion_ratio, True

    mask_history.append(mask_small)
    max_history = max(1, config.trail_frames)
    del mask_history[:-max_history]
    combined = mask_small.copy()
    for previous in mask_history[:-1]:
        combined = cv2.max(combined, previous)

    contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    sx = width / float(combined.shape[1])
    sy = height / float(combined.shape[0])
    detections: list[dict[str, Any]] = []
    for contour in contours:
        area_small = float(cv2.contourArea(contour))
        area = area_small * sx * sy
        if area < config.min_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        x1 = int(np.clip(x * sx, 0, width - 1))
        y1 = int(np.clip(y * sy, 0, height - 1))
        x2 = int(np.clip((x + w) * sx, 0, width - 1))
        y2 = int(np.clip((y + h) * sy, 0, height - 1))
        confidence = float(np.clip(0.15 + math.log1p(area) / math.log1p(max(20.0, width * height * 0.02)), 0.0, 1.0))
        detections.append(
            {
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "center_x": int((x1 + x2) / 2),
                "center_y": int((y1 + y2) / 2),
                "area": float(area),
                "confidence": confidence,
                "category": "motion_candidate",
                "source": "frame_difference",
            }
        )
    detections.sort(key=lambda item: item["confidence"], reverse=True)
    mask_full = cv2.resize(combined, (width, height), interpolation=cv2.INTER_NEAREST)
    return gray, mask_full, detections, current_motion_ratio, False


def save_motion_evidence(
    evidence_dir: Path,
    source_id: str,
    frame_index: int,
    frame: np.ndarray,
    mask: np.ndarray,
    detections: list[dict[str, Any]],
    title: str,
) -> dict[str, str]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    safe_source = slugify(source_id)
    stem = f"{safe_source}_frame_{frame_index:06d}"
    annotated_path = evidence_dir / f"{stem}_boxes.png"
    motion_path = evidence_dir / f"{stem}_motion.png"
    annotated = draw_detections(frame, detections, title)
    motion_only = cv2.bitwise_and(frame, frame, mask=mask)
    cv2.imwrite(str(annotated_path), annotated)
    cv2.imwrite(str(motion_path), motion_only)
    return {"annotated_path": str(annotated_path), "motion_path": str(motion_path)}


def analyze_video_source(
    source: SourceVideo,
    session_start_utc_ns: int | None,
    config: MotionConfig,
    evidence_dir: Path,
    sample_every: int,
    max_frames: int,
    perspective_sources: dict[str, Any],
    perspective_transforms: dict[str, PerspectiveTransform],
    max_evidence: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    emit_progress(f"Analyzing video source {source.source_id}")
    probe = probe_video(source.path)
    width = int(probe["width"])
    height = int(probe["height"])
    fps = float(probe["fps"])
    frame_utc_by_index = load_frame_utc_by_index(source.timing_path)
    transform = perspective_transforms.get(source.source_id)
    if transform is None:
        matrix = perspective_matrix_for_source(perspective_sources, source.source_id, width, height)
        if matrix is not None:
            transform = PerspectiveTransform(
                matrix=matrix,
                target_size=(width, height),
                mode="manual_four_point",
                reference_id=None,
                diagnostics={"accepted": True, "reason": "manual_json"},
            )
    cap = cv2.VideoCapture(str(source.path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {source.path}")

    records: list[dict[str, Any]] = []
    prev_gray: np.ndarray | None = None
    mask_history: list[np.ndarray] = []
    frame_index = -1
    evidence_count = 0
    sample_every = max(1, int(sample_every))
    max_frames = max(0, int(max_frames))

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame_index += 1
        if max_frames and frame_index >= max_frames:
            break
        if frame_index % sample_every != 0:
            continue
        frame = apply_perspective_transform(frame, transform)
        analysis_height, analysis_width = frame.shape[:2]
        next_gray, mask, detections, motion_ratio, global_rejected = detect_motion(prev_gray, frame, config, mask_history)
        prev_gray = next_gray
        local_s = frame_index / fps if fps > 0 else float(frame_index)
        utc_ns = frame_utc_by_index.get(frame_index)
        if utc_ns is None and source.creation_utc_ns is not None:
            utc_ns = int(source.creation_utc_ns + local_s * 1_000_000_000)
        session_s = ((utc_ns - session_start_utc_ns) / 1_000_000_000) if utc_ns is not None and session_start_utc_ns else None
        if source.metadata_offset_s is not None and source.origin == "extra":
            session_s = source.metadata_offset_s + local_s
            if session_start_utc_ns is not None:
                utc_ns = int(session_start_utc_ns + session_s * 1_000_000_000)
        motion_score = max((float(item["confidence"]) for item in detections), default=0.0)
        evidence_paths: dict[str, str] = {}
        if detections and evidence_count < max_evidence:
            title_time = f"t={session_s:.2f}s" if session_s is not None else f"local={local_s:.2f}s"
            evidence_paths = save_motion_evidence(
                evidence_dir,
                source.source_id,
                frame_index,
                frame,
                mask,
                detections[:8],
                f"{source.source_id} {title_time}",
            )
            evidence_count += 1
        records.append(
            {
                "kind": "motion",
                "source_id": source.source_id,
                "source_label": source.label,
                "source_origin": source.origin,
                "source_path": str(source.path),
                "frame_index": frame_index,
                "local_s": local_s,
                "session_s": session_s,
                "utc_ns": utc_ns,
                "utc_iso": utc_ns_to_iso(utc_ns),
                "image_width": analysis_width,
                "image_height": analysis_height,
                "motion_ratio": motion_ratio,
                "global_motion_rejected": global_rejected,
                "motion_score": motion_score,
                "detections": detections,
                "evidence": evidence_paths,
                "perspective_corrected": transform is not None,
                "perspective_mode": transform.mode if transform else None,
                "perspective_reference_id": transform.reference_id if transform else None,
            }
        )
    cap.release()
    summary = {
        "source_id": source.source_id,
        "label": source.label,
        "origin": source.origin,
        "path": str(source.path),
        "probe": {key: value for key, value in probe.items() if key != "ffprobe"},
        "sampled_records": len(records),
        "evidence_images": evidence_count,
        "perspective_corrected": transform is not None,
        "perspective_mode": transform.mode if transform else None,
        "perspective_reference_id": transform.reference_id if transform else None,
        "perspective_diagnostics": transform.diagnostics if transform else None,
    }
    return records, summary


def first_audio_utc_ns(timing_path: Path | None, fallback: int | None) -> int | None:
    rows = read_jsonl(timing_path, limit=1) if timing_path else []
    if rows and rows[0].get("utc_ns"):
        return int(rows[0]["utc_ns"])
    return fallback


def wav_to_float_samples(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if sample_width == 2:
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        samples = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raw = np.frombuffer(frames, dtype=np.uint8).astype(np.float32)
        samples = (raw - 128.0) / 128.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def band_energy_ratio(samples: np.ndarray, rate: int, low_hz: float = 80.0, high_hz: float = 2500.0) -> float:
    if samples.size < 8:
        return 0.0
    window = np.hanning(samples.size).astype(np.float32)
    spectrum = np.abs(np.fft.rfft(samples * window)) ** 2
    freqs = np.fft.rfftfreq(samples.size, d=1.0 / float(rate))
    total_mask = (freqs >= 50.0) & (freqs <= min(8000.0, rate / 2.0))
    band_mask = (freqs >= low_hz) & (freqs <= min(high_hz, rate / 2.0))
    total = float(np.sum(spectrum[total_mask]))
    if total <= 1e-12:
        return 0.0
    return float(np.clip(np.sum(spectrum[band_mask]) / total, 0.0, 1.0))


def save_audio_evidence(
    evidence_dir: Path,
    source_id: str,
    index: int,
    window_samples: np.ndarray,
    rate: int,
    score: float,
    session_s: float | None,
) -> str:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    width, height = 760, 220
    canvas = np.full((height, width, 3), 18, dtype=np.uint8)
    cv2.rectangle(canvas, (0, 0), (width - 1, height - 1), (65, 65, 65), 1)
    if window_samples.size:
        step = max(1, int(window_samples.size / (width - 40)))
        reduced = window_samples[::step][: width - 40]
        mid = height // 2
        amp = max(1e-6, float(np.max(np.abs(reduced))))
        points = []
        for i, value in enumerate(reduced):
            x = 20 + i
            y = int(np.clip(mid - (value / amp) * 78, 18, height - 30))
            points.append((x, y))
        for a, b in zip(points, points[1:]):
            cv2.line(canvas, a, b, (0, 220, 140), 1, cv2.LINE_AA)
    label = f"{source_id} audio score={score:.2f}"
    if session_s is not None:
        label += f" t={session_s:.2f}s"
    cv2.putText(canvas, label, (18, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(canvas, f"{rate} Hz window proof", (18, height - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (190, 190, 190), 1)
    path = evidence_dir / f"{slugify(source_id)}_audio_{index:04d}.png"
    cv2.imwrite(str(path), canvas)
    return str(path)


def analyze_audio_sources(
    audio_sources: list[dict[str, Any]],
    session_start_utc_ns: int | None,
    evidence_dir: Path,
    window_s: float,
    max_evidence: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for source in audio_sources:
        source_id = str(source["source_id"])
        emit_progress(f"Analyzing audio source {source_id}")
        try:
            samples, rate = wav_to_float_samples(Path(source["path"]))
        except Exception as exc:
            summaries.append({"source_id": source_id, "path": str(source["path"]), "error": repr(exc)})
            continue
        window_n = max(512, int(rate * window_s))
        start_ns = first_audio_utc_ns(source.get("timing_path"), source.get("started_utc_ns"))
        source_records: list[dict[str, Any]] = []
        raw_windows: list[np.ndarray] = []
        for sample_start in range(0, max(0, samples.size - window_n + 1), window_n):
            window_samples = samples[sample_start : sample_start + window_n]
            rms = float(np.sqrt(np.mean(np.square(window_samples))) if window_samples.size else 0.0)
            band_ratio = band_energy_ratio(window_samples, rate)
            local_s = sample_start / float(rate)
            utc_ns = int(start_ns + local_s * 1_000_000_000) if start_ns is not None else None
            session_s = ((utc_ns - session_start_utc_ns) / 1_000_000_000) if utc_ns is not None and session_start_utc_ns else None
            source_records.append(
                {
                    "kind": "audio",
                    "source_id": source_id,
                    "source_label": source.get("label", source_id),
                    "source_path": str(source["path"]),
                    "sample_start": sample_start,
                    "sample_end": sample_start + window_n,
                    "local_s": local_s,
                    "session_s": session_s,
                    "utc_ns": utc_ns,
                    "utc_iso": utc_ns_to_iso(utc_ns),
                    "rms": rms,
                    "band_ratio": band_ratio,
                    "audio_score": 0.0,
                    "evidence": {},
                }
            )
            raw_windows.append(window_samples)
        rms_values = np.array([item["rms"] for item in source_records], dtype=np.float32)
        if rms_values.size:
            low = float(np.percentile(rms_values, 20))
            high = float(np.percentile(rms_values, 95))
            denom = max(1e-6, high - low)
            for item in source_records:
                rms_score = float(np.clip((float(item["rms"]) - low) / denom, 0.0, 1.0))
                band_score = float(np.clip((float(item["band_ratio"]) - 0.18) / 0.62, 0.0, 1.0))
                item["audio_score"] = float(np.clip(0.68 * rms_score + 0.32 * band_score, 0.0, 1.0))
        ranked = sorted(enumerate(source_records), key=lambda pair: pair[1]["audio_score"], reverse=True)
        for evidence_index, (record_index, item) in enumerate(ranked[:max_evidence]):
            if item["audio_score"] <= 0.05:
                continue
            item["evidence"] = {
                "audio_path": save_audio_evidence(
                    evidence_dir,
                    source_id,
                    evidence_index,
                    raw_windows[record_index],
                    rate,
                    float(item["audio_score"]),
                    item.get("session_s"),
                )
            }
        records.extend(source_records)
        summaries.append(
            {
                "source_id": source_id,
                "path": str(source["path"]),
                "sample_rate": rate,
                "samples": int(samples.size),
                "windows": len(source_records),
                "max_audio_score": max((float(item["audio_score"]) for item in source_records), default=0.0),
            }
        )
    return records, summaries


def bin_scores(records: Iterable[dict[str, Any]], score_key: str, time_key: str, bin_s: float) -> dict[int, float]:
    bins: dict[int, float] = {}
    for item in records:
        timestamp = item.get(time_key)
        if timestamp is None:
            continue
        key = int(math.floor(float(timestamp) / bin_s))
        bins[key] = max(bins.get(key, 0.0), float(item.get(score_key, 0.0)))
    return bins


def correlation_for_offset(archive_bins: dict[int, float], extra_bins: dict[int, float], offset_s: float, bin_s: float) -> float:
    if not archive_bins or not extra_bins:
        return 0.0
    offset_bins = int(round(offset_s / bin_s))
    numerator = 0.0
    archive_power = 0.0
    extra_power = 0.0
    for extra_bin, extra_score in extra_bins.items():
        archive_score = archive_bins.get(extra_bin + offset_bins, 0.0)
        numerator += archive_score * extra_score
        archive_power += archive_score * archive_score
        extra_power += extra_score * extra_score
    denom = math.sqrt(max(archive_power, 1e-9) * max(extra_power, 1e-9))
    return float(numerator / denom) if denom else 0.0


def estimate_extra_video_sync(
    archive_motion: list[dict[str, Any]],
    extra_records_by_source: dict[str, list[dict[str, Any]]],
    metadata_offsets: dict[str, float | None],
    bin_s: float,
) -> dict[str, dict[str, Any]]:
    archive_bins = bin_scores(archive_motion, "motion_score", "session_s", bin_s)
    report: dict[str, dict[str, Any]] = {}
    if archive_bins:
        archive_min = min(archive_bins) * bin_s
        archive_max = max(archive_bins) * bin_s
    else:
        archive_min, archive_max = 0.0, 0.0
    for source_id, records in extra_records_by_source.items():
        extra_bins = bin_scores(records, "motion_score", "local_s", bin_s)
        metadata_offset = metadata_offsets.get(source_id)
        if metadata_offset is None:
            if extra_bins:
                search_start = archive_min - max(extra_bins) * bin_s
                search_stop = archive_max - min(extra_bins) * bin_s
            else:
                search_start, search_stop = 0.0, 0.0
        else:
            search_start = metadata_offset - 15.0
            search_stop = metadata_offset + 15.0
        best_offset = metadata_offset if metadata_offset is not None else search_start
        best_corr = -1.0
        steps = max(1, int(round((search_stop - search_start) / bin_s)))
        for i in range(steps + 1):
            candidate = search_start + i * bin_s
            corr = correlation_for_offset(archive_bins, extra_bins, candidate, bin_s)
            if corr > best_corr:
                best_corr = corr
                best_offset = candidate
        if metadata_offset is not None and best_corr < 0.05:
            best_offset = metadata_offset
        report[source_id] = {
            "metadata_offset_s": metadata_offset,
            "suggested_offset_s": best_offset,
            "correlation": max(0.0, best_corr),
            "search_start_s": search_start,
            "search_stop_s": search_stop,
            "bin_s": bin_s,
        }
    return report


def choose_sync_offsets(
    motion_report: dict[str, dict[str, Any]],
    audio_report: dict[str, dict[str, Any]],
    metadata_offsets: dict[str, float | None],
) -> dict[str, dict[str, Any]]:
    source_ids = sorted(set(metadata_offsets) | set(motion_report) | set(audio_report))
    combined: dict[str, dict[str, Any]] = {}
    for source_id in source_ids:
        motion = motion_report.get(source_id, {})
        audio = audio_report.get(source_id, {})
        metadata_offset = metadata_offsets.get(source_id)
        motion_corr = float(motion.get("correlation", 0.0) or 0.0)
        audio_corr = float(audio.get("correlation", 0.0) or 0.0)
        chosen_method = "metadata"
        chosen_offset = metadata_offset

        def trustworthy(candidate_report: dict[str, Any], min_corr: float) -> bool:
            candidate = candidate_report.get("suggested_offset_s")
            if candidate is None:
                return False
            corr = float(candidate_report.get("correlation", 0.0) or 0.0)
            if corr < min_corr:
                return False
            if metadata_offset is not None and abs(float(candidate) - float(metadata_offset)) > 5.0:
                return False
            start = candidate_report.get("search_start_s")
            stop = candidate_report.get("search_stop_s")
            bin_width = float(candidate_report.get("bin_s", 0.5) or 0.5)
            if start is not None and abs(float(candidate) - float(start)) <= bin_width:
                return False
            if stop is not None and abs(float(candidate) - float(stop)) <= bin_width:
                return False
            return True

        if trustworthy(audio, 0.35) and audio_corr >= motion_corr:
            chosen_method = "audio_correlation"
            chosen_offset = float(audio["suggested_offset_s"])
        elif trustworthy(motion, 0.12):
            chosen_method = "motion_correlation"
            chosen_offset = float(motion["suggested_offset_s"])
        elif chosen_offset is None:
            chosen_offset = motion.get("suggested_offset_s", audio.get("suggested_offset_s", 0.0))
            chosen_method = "best_available"
        combined[source_id] = {
            "metadata_offset_s": metadata_offset,
            "suggested_offset_s": chosen_offset,
            "chosen_method": chosen_method,
            "motion": motion,
            "audio": audio,
        }
    return combined


def refine_sync_report_with_visual_landmarks(sync_report: dict[str, dict[str, Any]], auto_perspective: dict[str, Any]) -> None:
    transforms = auto_perspective.get("transforms", {})
    for source_id, transform in transforms.items():
        if not isinstance(transform, dict) or not transform.get("visual_sync_applied"):
            continue
        report = sync_report.get(source_id)
        if not report:
            continue
        report["visual_sync_offset_s"] = transform.get("visual_sync_offset_s")
        report["visual_sync_basis"] = transform.get("visual_sync_basis")
        report["previous_metadata_offset_s"] = transform.get("previous_metadata_offset_s")
        report["chosen_method"] = "visual_landmark_sync"

        best_kind = None
        best_candidate = None
        best_corr = 0.0
        for kind in ["motion", "audio"]:
            candidate_report = report.get(kind, {})
            candidate = candidate_report.get("suggested_offset_s")
            corr = float(candidate_report.get("correlation", 0.0) or 0.0)
            if candidate is None or corr < 0.80:
                continue
            start = candidate_report.get("search_start_s")
            stop = candidate_report.get("search_stop_s")
            bin_width = float(candidate_report.get("bin_s", 0.5) or 0.5)
            if start is not None and abs(float(candidate) - float(start)) <= bin_width:
                continue
            if stop is not None and abs(float(candidate) - float(stop)) <= bin_width:
                continue
            if corr > best_corr:
                best_kind = kind
                best_candidate = float(candidate)
                best_corr = corr
        if best_candidate is not None:
            report["suggested_offset_s"] = best_candidate
            report["chosen_method"] = f"visual_landmark_then_{best_kind}_correlation"
            report["correlation_refined_from_visual_sync"] = True


def apply_extra_sync_offsets(
    records_by_source: dict[str, list[dict[str, Any]]],
    sync_report: dict[str, dict[str, Any]],
    session_start_utc_ns: int | None,
) -> None:
    for source_id, records in records_by_source.items():
        offset = sync_report.get(source_id, {}).get("suggested_offset_s")
        if offset is None:
            continue
        for item in records:
            session_s = float(item.get("local_s", 0.0)) + float(offset)
            item["session_s"] = session_s
            if session_start_utc_ns is not None:
                utc_ns = int(session_start_utc_ns + session_s * 1_000_000_000)
                item["utc_ns"] = utc_ns
                item["utc_iso"] = utc_ns_to_iso(utc_ns)


def extract_audio_from_video(video: SourceVideo, out_dir: Path) -> dict[str, Any] | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    wav_path = out_dir / f"{slugify(video.source_id)}_audio.wav"
    try:
        completed = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(video.path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "48000",
                str(wav_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return None
    if completed.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size <= 44:
        return None
    return {
        "source_id": video.source_id,
        "label": f"{video.label} audio",
        "path": wav_path,
        "timing_path": None,
        "started_utc_ns": video.creation_utc_ns,
        "origin": "extra",
    }


def import_detector_records(paths: list[Path], session_start_utc_ns: int | None) -> list[dict[str, Any]]:
    imported: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        for record in load_json_or_jsonl(path):
            detections = record.get("detections") or []
            if not isinstance(detections, list):
                detections = []
            score = max((float(det.get("confidence", det.get("score", 0.0))) for det in detections if isinstance(det, dict)), default=0.0)
            utc_ns = int(record["utc_ns"]) if record.get("utc_ns") else None
            session_s = record.get("timestamp_s")
            if utc_ns is not None and session_start_utc_ns is not None:
                session_s = (utc_ns - session_start_utc_ns) / 1_000_000_000
            imported.append(
                {
                    "kind": "imported_detector",
                    "source_id": slugify(str(record.get("source") or path.stem)),
                    "source_path": str(path),
                    "frame_index": record.get("frame_index"),
                    "session_s": float(session_s) if session_s is not None else None,
                    "utc_ns": utc_ns,
                    "utc_iso": utc_ns_to_iso(utc_ns),
                    "imported_score": score,
                    "detections": detections,
                    "model": record.get("model"),
                    "prompt_type": record.get("prompt_type"),
                    "evidence": {},
                }
            )
    return imported


def add_evidence(target: dict[str, Any], evidence: dict[str, Any], max_items: int = 12) -> None:
    if not evidence:
        return
    items = target.setdefault("evidence", [])
    key = json.dumps(evidence, sort_keys=True)
    if any(json.dumps(item, sort_keys=True) == key for item in items):
        return
    if len(items) < max_items:
        items.append(evidence)


def fuse_records(
    motion_records: list[dict[str, Any]],
    audio_records: list[dict[str, Any]],
    imported_records: list[dict[str, Any]],
    session_start_utc_ns: int | None,
    threshold: float,
    bin_s: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    bins: dict[int, dict[str, Any]] = {}

    def get_bin(session_s: float) -> dict[str, Any]:
        key = int(math.floor(session_s / bin_s))
        item = bins.setdefault(
            key,
            {
                "bin_index": key,
                "start_s": key * bin_s,
                "end_s": (key + 1) * bin_s,
                "motion_score": 0.0,
                "audio_score": 0.0,
                "imported_score": 0.0,
                "fused_score": 0.0,
                "sources": set(),
                "motion_sources": set(),
                "audio_sources": set(),
                "imported_sources": set(),
                "evidence": [],
            },
        )
        return item

    for record in motion_records:
        session_s = record.get("session_s")
        if session_s is None:
            continue
        item = get_bin(float(session_s))
        score = float(record.get("motion_score", 0.0))
        item["motion_score"] = max(item["motion_score"], score)
        if score > 0.05:
            item["sources"].add(record.get("source_id"))
            item["motion_sources"].add(record.get("source_id"))
            evidence = record.get("evidence") or {}
            if evidence:
                add_evidence(
                    item,
                    {
                        "kind": "motion",
                        "source_id": record.get("source_id"),
                        "score": score,
                        "frame_index": record.get("frame_index"),
                        **evidence,
                    },
                )

    for record in audio_records:
        session_s = record.get("session_s")
        if session_s is None:
            continue
        item = get_bin(float(session_s))
        score = float(record.get("audio_score", 0.0))
        item["audio_score"] = max(item["audio_score"], score)
        if score > 0.05:
            item["sources"].add(record.get("source_id"))
            item["audio_sources"].add(record.get("source_id"))
            evidence = record.get("evidence") or {}
            if evidence:
                add_evidence(
                    item,
                    {
                        "kind": "audio",
                        "source_id": record.get("source_id"),
                        "score": score,
                        **evidence,
                    },
                )

    for record in imported_records:
        session_s = record.get("session_s")
        if session_s is None:
            continue
        item = get_bin(float(session_s))
        score = float(record.get("imported_score", 0.0))
        item["imported_score"] = max(item["imported_score"], score)
        if score > 0.05:
            item["sources"].add(record.get("source_id"))
            item["imported_sources"].add(record.get("source_id"))

    timeline: list[dict[str, Any]] = []
    for key in sorted(bins):
        item = bins[key]
        source_count = len([source for source in item["sources"] if source])
        agreement_score = float(np.clip((source_count - 1) / 3.0, 0.0, 1.0))
        fused = float(
            np.clip(
                0.48 * item["motion_score"]
                + 0.22 * item["audio_score"]
                + 0.42 * item["imported_score"]
                + 0.18 * agreement_score,
                0.0,
                1.0,
            )
        )
        item["fused_score"] = fused
        item["source_count"] = source_count
        item["sources"] = sorted(source for source in item["sources"] if source)
        item["motion_sources"] = sorted(source for source in item["motion_sources"] if source)
        item["audio_sources"] = sorted(source for source in item["audio_sources"] if source)
        item["imported_sources"] = sorted(source for source in item["imported_sources"] if source)
        if session_start_utc_ns is not None:
            item["start_utc_ns"] = int(session_start_utc_ns + item["start_s"] * 1_000_000_000)
            item["start_utc"] = utc_ns_to_iso(item["start_utc_ns"])
        timeline.append(item)

    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for item in timeline:
        active = float(item["fused_score"]) >= threshold
        if not active:
            if current is not None:
                events.append(current)
                current = None
            continue
        if current is None:
            current = {
                "event_index": len(events),
                "start_s": item["start_s"],
                "end_s": item["end_s"],
                "peak_score": item["fused_score"],
                "peak_s": item["start_s"],
                "sources": set(item["sources"]),
                "evidence": list(item["evidence"]),
                "bins": [item["bin_index"]],
            }
        else:
            current["end_s"] = item["end_s"]
            current["sources"].update(item["sources"])
            current["bins"].append(item["bin_index"])
            if item["fused_score"] > current["peak_score"]:
                current["peak_score"] = item["fused_score"]
                current["peak_s"] = item["start_s"]
            for evidence in item["evidence"]:
                add_evidence(current, evidence, max_items=24)
    if current is not None:
        events.append(current)

    clean_events: list[dict[str, Any]] = []
    for event_index, event in enumerate(events):
        start_utc_ns = int(session_start_utc_ns + event["start_s"] * 1_000_000_000) if session_start_utc_ns else None
        end_utc_ns = int(session_start_utc_ns + event["end_s"] * 1_000_000_000) if session_start_utc_ns else None
        clean_events.append(
            {
                "event_index": event_index,
                "start_s": event["start_s"],
                "end_s": event["end_s"],
                "duration_s": float(event["end_s"] - event["start_s"]),
                "start_utc_ns": start_utc_ns,
                "start_utc": utc_ns_to_iso(start_utc_ns),
                "end_utc_ns": end_utc_ns,
                "end_utc": utc_ns_to_iso(end_utc_ns),
                "peak_score": event["peak_score"],
                "peak_s": event["peak_s"],
                "sources": sorted(source for source in event["sources"] if source),
                "evidence": event["evidence"],
                "bins": event["bins"],
            }
        )

    evidence_index: list[dict[str, Any]] = []
    for item in timeline:
        for evidence in item.get("evidence", []):
            evidence_index.append({"time_s": item["start_s"], "fused_score": item["fused_score"], **evidence})
    return timeline, clean_events, evidence_index


def inspect_inputs(args: argparse.Namespace) -> dict[str, Any]:
    archive = Path(args.archive) if args.archive else DEFAULT_ARCHIVE
    manifest: dict[str, Any] | None = None
    streams: list[dict[str, Any]] = []
    archive_summary: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory(prefix="fusion_inspect_") as temp_dir:
        if archive.exists():
            manifest, session_dir = extract_archive(archive, Path(temp_dir))
            videos, audios = stream_sources_from_manifest(manifest, session_dir)
            streams = [
                {"kind": "video", "source_id": source.source_id, "path": str(source.path), "timing_path": str(source.timing_path)}
                for source in videos
            ] + [
                {"kind": "audio", "source_id": item["source_id"], "path": str(item["path"]), "timing_path": str(item.get("timing_path"))}
                for item in audios
            ]
            archive_summary = {
                "path": str(archive),
                "session_id": manifest.get("session_id"),
                "session_start_utc_ns": manifest.get("session_start_utc_ns"),
                "session_start_utc": manifest.get("session_start_utc"),
                "session_stop_utc_ns": manifest.get("session_stop_utc_ns"),
                "session_stop_utc": manifest.get("session_stop_utc"),
                "max_start_jitter_ms": manifest.get("max_start_jitter_ms"),
                "streams": streams,
            }
        else:
            archive_summary = {"path": str(archive), "exists": False}

    session_start_ns = int(manifest["session_start_utc_ns"]) if manifest and manifest.get("session_start_utc_ns") else None
    session_stop_ns = int(manifest["session_stop_utc_ns"]) if manifest and manifest.get("session_stop_utc_ns") else None
    extra_summaries = []
    for item in args.extra_video or []:
        path = Path(item)
        if not path.exists():
            extra_summaries.append({"path": str(path), "exists": False})
            continue
        probe = probe_video(path)
        creation_ns = probe.get("creation_utc_ns")
        overlap_s = None
        if creation_ns and session_start_ns and session_stop_ns:
            duration_ns = int((probe.get("duration_s") or 0) * 1_000_000_000)
            overlap_ns = max(0, min(session_stop_ns, creation_ns + duration_ns) - max(session_start_ns, creation_ns))
            overlap_s = overlap_ns / 1_000_000_000
        extra_summaries.append(
            {
                "path": str(path),
                "exists": True,
                "width": probe["width"],
                "height": probe["height"],
                "fps": probe["fps"],
                "frame_count": probe["frame_count"],
                "duration_s": probe["duration_s"],
                "creation_utc": probe["creation_utc"],
                "creation_offset_from_session_s": (creation_ns - session_start_ns) / 1_000_000_000
                if creation_ns and session_start_ns
                else None,
                "overlap_with_archive_s": overlap_s,
                "has_audio": probe["has_audio"],
            }
        )
    return {"archive": archive_summary, "extra_videos": extra_summaries}


def md_path(path_text: str, out_dir: Path) -> str:
    path = Path(path_text)
    try:
        return path.resolve().relative_to(out_dir.resolve()).as_posix()
    except Exception:
        return path.resolve().as_posix()


def make_report_image_asset(
    source_path_text: str,
    report_path: Path,
    label: str,
    index: int,
    max_width: int = 1400,
) -> str | None:
    source_path = Path(source_path_text)
    if not source_path.exists():
        return None
    asset_dir = report_path.parent / "report_assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    safe_label = slugify(label)
    target_path = asset_dir / f"{index:03d}_{safe_label}.jpg"
    image = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
    if image is None:
        fallback_path = asset_dir / f"{index:03d}_{safe_label}{source_path.suffix.lower() or '.png'}"
        shutil.copy2(source_path, fallback_path)
        return md_path(str(fallback_path), report_path.parent)
    height, width = image.shape[:2]
    if width > max_width:
        scale = max_width / float(width)
        image = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(target_path), image, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    return md_path(str(target_path), report_path.parent)


def write_markdown_report(
    report_path: Path,
    summary: dict[str, Any],
    events: list[dict[str, Any]],
    sync_report: dict[str, Any],
    auto_perspective: dict[str, Any],
    evidence_index: list[dict[str, Any]],
) -> None:
    out_dir = report_path.parent
    image_index = 0

    def image_block(path_text: str, label: str, max_width: int = 1400, display_width: int = 980) -> list[str]:
        nonlocal image_index
        image_index += 1
        asset = make_report_image_asset(path_text, report_path, label, image_index, max_width=max_width)
        if not asset:
            return [f"_Missing image: `{path_text}`_", ""]
        return [
            f'<img src="{asset}" alt="{label}" width="{display_width}">',
            "",
            f"_Source image: `{path_text}`_",
            "",
        ]

    lines: list[str] = [
        "# Fusion Evidence Lab Result Report",
        "",
        "## Run Summary",
        "",
        f"- Archive: `{summary.get('archive')}`",
        f"- Session: `{summary.get('session_id')}`",
        f"- Session start UTC: `{summary.get('session_start_utc')}`",
        f"- Fused threshold: `{summary.get('threshold')}`",
        f"- Timeline bins: `{summary.get('timeline_bins')}`",
        f"- Events: `{summary.get('event_count')}`",
        f"- Indexed proof items: `{summary.get('evidence_count')}`",
        f"- Extra video audio sources: `{summary.get('extra_video_audio_sources')}`",
        "",
        "## Autonomous Perspective Correction",
        "",
        f"- Enabled: `{auto_perspective.get('enabled')}`",
        f"- Reference source: `{auto_perspective.get('reference_id')}`",
        "",
    ]
    transforms = auto_perspective.get("transforms", {})
    if transforms:
        lines.append("| Source | Accepted | Reason | Inliers | Inlier ratio |")
        lines.append("| --- | ---: | --- | ---: | ---: |")
        for source_id, item in transforms.items():
            best = item.get("best", {}) if isinstance(item, dict) else {}
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(source_id),
                        str(bool(item.get("accepted"))) if isinstance(item, dict) else "False",
                        str(item.get("reason", "")) if isinstance(item, dict) else "",
                        str(best.get("inliers", "")),
                        f"{float(best.get('inlier_ratio', 0.0)):.3f}" if best else "",
                    ]
                )
                + " |"
            )
        lines.append("")
        for source_id, item in transforms.items():
            if not isinstance(item, dict):
                continue
            preview = item.get("preview") or {}
            if preview.get("matches_path") or preview.get("warp_preview_path"):
                lines.append(f"### Perspective Proof: `{source_id}`")
                lines.append("")
                if preview.get("matches_path"):
                    lines.extend(image_block(preview["matches_path"], f"{source_id} feature matches", 1600, 1100))
                if preview.get("warp_preview_path"):
                    lines.extend(image_block(preview["warp_preview_path"], f"{source_id} warp preview", 1800, 1200))
    else:
        lines.append("No perspective candidates were available.")
        lines.append("")

    lines.extend(["## Sync Report", "", "```json", json.dumps(sync_report, indent=2), "```", ""])

    lines.extend(["## Top Fused Events", ""])
    if events:
        lines.append("| Event | Start s | End s | Peak | Sources | Proofs |")
        lines.append("| ---: | ---: | ---: | ---: | --- | ---: |")
        for event in events[:20]:
            lines.append(
                f"| {event['event_index']} | {event['start_s']:.2f} | {event['end_s']:.2f} | "
                f"{event['peak_score']:.3f} | {', '.join(event.get('sources', []))} | {len(event.get('evidence', []))} |"
            )
        lines.append("")
    else:
        lines.append("No events passed the current threshold.")
        lines.append("")

    lines.extend(["## Proof Screenshots", ""])
    shown = 0
    for event in events[:12]:
        lines.append(f"### Event {event['event_index']} - peak {event['peak_score']:.2f}")
        lines.append("")
        for proof in event.get("evidence", [])[:4]:
            lines.append(f"- `{proof.get('kind')}` source `{proof.get('source_id')}` score `{float(proof.get('score', 0.0)):.2f}`")
            for key, label in [("annotated_path", "Boxes"), ("motion_path", "Motion-only"), ("audio_path", "Audio proof")]:
                if proof.get(key) and Path(proof[key]).exists():
                    lines.extend(image_block(proof[key], f"event_{event['event_index']}_{label}", 1500, 980))
                    shown += 1
        if shown >= 36:
            break
    if not shown and evidence_index:
        for proof in evidence_index[:12]:
            for key, label in [("annotated_path", "Boxes"), ("motion_path", "Motion-only"), ("audio_path", "Audio proof")]:
                if proof.get(key) and Path(proof[key]).exists():
                    lines.extend(image_block(proof[key], label, 1500, 980))
                    shown += 1
            if shown >= 24:
                break

    lines.extend(
        [
            "## Saved Artifacts",
            "",
            f"- Run directory: `{summary['paths']['run_dir']}`",
            f"- Events JSON: `{summary['paths']['events']}`",
            f"- Timeline JSON: `{summary['paths']['fusion_timeline']}`",
            f"- Sync JSON: `{summary['paths']['sync_report']}`",
            f"- Auto perspective JSON: `{summary['paths']['auto_perspective']}`",
            f"- Evidence index: `{summary['paths']['evidence_index']}`",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    archive_path = Path(args.archive) if args.archive else DEFAULT_ARCHIVE
    out_dir = (Path(args.out_dir) if args.out_dir else DEFAULT_OUTPUT_ROOT / datetime.now().strftime("fusion_%Y%m%dT%H%M%S")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = out_dir / "evidence"
    config = MotionConfig(
        diff_threshold=args.motion_threshold,
        min_area=args.min_area,
        blur_kernel=args.blur_kernel,
        morph_kernel=args.morph_kernel,
        trail_frames=args.trail_frames,
        max_motion_ratio=args.max_motion_ratio,
        analysis_scale=args.analysis_scale,
    ).normalized()
    perspective_sources = load_perspective_spec(args.perspective_json)

    manifest, session_dir = extract_archive(archive_path, out_dir)
    session_start_utc_ns = int(manifest["session_start_utc_ns"]) if manifest.get("session_start_utc_ns") else None
    videos, audios = stream_sources_from_manifest(manifest, session_dir)

    extra_sources: list[SourceVideo] = []
    for extra_path_text in args.extra_video or []:
        extra_path = Path(extra_path_text)
        probe = probe_video(extra_path)
        creation_ns = probe.get("creation_utc_ns")
        metadata_offset = ((creation_ns - session_start_utc_ns) / 1_000_000_000) if creation_ns and session_start_utc_ns else None
        extra_sources.append(
            SourceVideo(
                source_id=slugify(extra_path.stem),
                label=extra_path.name,
                path=extra_path,
                origin="extra",
                creation_utc_ns=creation_ns,
                metadata_offset_s=metadata_offset,
            )
        )

    all_video_sources = videos + extra_sources
    perspective_transforms, auto_perspective = build_auto_perspective_transforms(
        all_video_sources,
        session_start_utc_ns,
        perspective_sources,
        out_dir,
        args.auto_perspective,
        args.auto_perspective_reference,
        args.auto_perspective_samples,
        args.auto_perspective_min_matches,
        args.auto_perspective_min_inliers,
    )
    updated_extra_sources: list[SourceVideo] = []
    for source in extra_sources:
        transform_diag = auto_perspective.get("transforms", {}).get(source.source_id, {})
        visual_offset = transform_diag.get("visual_sync_offset_s") if isinstance(transform_diag, dict) else None
        if visual_offset is not None and (
            source.metadata_offset_s is None or abs(float(source.metadata_offset_s) - float(visual_offset)) > 30.0
        ):
            transform_diag["visual_sync_applied"] = True
            transform_diag["previous_metadata_offset_s"] = source.metadata_offset_s
            source = SourceVideo(
                source_id=source.source_id,
                label=source.label,
                path=source.path,
                origin=source.origin,
                timing_path=source.timing_path,
                creation_utc_ns=source.creation_utc_ns,
                metadata_offset_s=float(visual_offset),
            )
        elif isinstance(transform_diag, dict):
            transform_diag["visual_sync_applied"] = False
        updated_extra_sources.append(source)
    extra_sources = updated_extra_sources

    all_motion_records: list[dict[str, Any]] = []
    video_summaries: list[dict[str, Any]] = []
    archive_motion: list[dict[str, Any]] = []
    extra_records_by_source: dict[str, list[dict[str, Any]]] = {}
    metadata_offsets: dict[str, float | None] = {}

    for source in videos:
        records, summary = analyze_video_source(
            source,
            session_start_utc_ns,
            config,
            evidence_dir,
            args.sample_every,
            args.max_frames,
            perspective_sources,
            perspective_transforms,
            args.max_evidence_per_source,
        )
        archive_motion.extend(records)
        all_motion_records.extend(records)
        video_summaries.append(summary)

    for source in extra_sources:
        records, summary = analyze_video_source(
            source,
            session_start_utc_ns,
            config,
            evidence_dir,
            args.sample_every,
            args.max_frames,
            perspective_sources,
            perspective_transforms,
            args.max_evidence_per_source,
        )
        extra_records_by_source[source.source_id] = records
        metadata_offsets[source.source_id] = source.metadata_offset_s
        all_motion_records.extend(records)
        video_summaries.append(summary)

    extra_audio_sources: list[dict[str, Any]] = []
    if not args.no_extra_video_audio:
        for source in extra_sources:
            audio_source = extract_audio_from_video(source, out_dir / "extracted_extra_audio")
            if audio_source:
                extra_audio_sources.append(audio_source)

    archive_audio_records, audio_summaries = analyze_audio_sources(
        audios,
        session_start_utc_ns,
        evidence_dir,
        args.audio_window_s,
        args.max_audio_evidence_per_source,
    )
    extra_audio_records, extra_audio_summaries = analyze_audio_sources(
        extra_audio_sources,
        session_start_utc_ns,
        evidence_dir,
        args.audio_window_s,
        args.max_audio_evidence_per_source,
    )

    motion_sync_report = estimate_extra_video_sync(archive_motion, extra_records_by_source, metadata_offsets, args.bin_s)
    archive_audio_for_sync = [
        {"session_s": item.get("session_s"), "motion_score": item.get("audio_score", 0.0)}
        for item in archive_audio_records
        if item.get("session_s") is not None
    ]
    extra_audio_by_source = {
        source_id: [
            {"local_s": item.get("local_s"), "motion_score": item.get("audio_score", 0.0)}
            for item in extra_audio_records
            if item.get("source_id") == source_id and item.get("local_s") is not None
        ]
        for source_id in metadata_offsets
    }
    audio_sync_report = estimate_extra_video_sync(archive_audio_for_sync, extra_audio_by_source, metadata_offsets, args.bin_s)
    sync_report = choose_sync_offsets(motion_sync_report, audio_sync_report, metadata_offsets)
    refine_sync_report_with_visual_landmarks(sync_report, auto_perspective)
    apply_extra_sync_offsets(extra_records_by_source, sync_report, session_start_utc_ns)
    apply_extra_sync_offsets(
        {source_id: [item for item in extra_audio_records if item.get("source_id") == source_id] for source_id in metadata_offsets},
        sync_report,
        session_start_utc_ns,
    )

    audio_records = archive_audio_records + extra_audio_records
    audio_summaries = audio_summaries + extra_audio_summaries
    imported_records = import_detector_records([Path(path) for path in args.detector_json or []], session_start_utc_ns)

    timeline, events, evidence_index = fuse_records(
        all_motion_records,
        audio_records,
        imported_records,
        session_start_utc_ns,
        args.threshold,
        args.bin_s,
    )

    paths = {
        "run_dir": str(out_dir),
        "fusion_timeline": str(out_dir / "fusion_timeline.json"),
        "events": str(out_dir / "events.json"),
        "sync_report": str(out_dir / "sync_report.json"),
        "auto_perspective": str(out_dir / "auto_perspective.json"),
        "evidence_index": str(out_dir / "evidence_index.json"),
        "motion_records": str(out_dir / "motion_records.jsonl"),
        "audio_records": str(out_dir / "audio_records.jsonl"),
        "result_report": str(out_dir / "result_report.md"),
        "summary": str(out_dir / "run_summary.json"),
    }
    write_json(out_dir / "fusion_timeline.json", timeline)
    write_json(out_dir / "events.json", events)
    write_json(out_dir / "sync_report.json", sync_report)
    write_json(out_dir / "auto_perspective.json", auto_perspective)
    write_json(out_dir / "evidence_index.json", evidence_index)
    with (out_dir / "motion_records.jsonl").open("w", encoding="utf-8") as handle:
        for record in all_motion_records:
            handle.write(json.dumps(record) + "\n")
    with (out_dir / "audio_records.jsonl").open("w", encoding="utf-8") as handle:
        for record in audio_records:
            handle.write(json.dumps(record) + "\n")

    summary = {
        "archive": str(archive_path),
        "session_id": manifest.get("session_id"),
        "session_start_utc_ns": session_start_utc_ns,
        "session_start_utc": utc_ns_to_iso(session_start_utc_ns),
        "threshold": args.threshold,
        "bin_s": args.bin_s,
        "motion_config": asdict(config),
        "video_summaries": video_summaries,
        "audio_summaries": audio_summaries,
        "auto_perspective": auto_perspective,
        "extra_video_audio_sources": len(extra_audio_sources),
        "imported_detector_records": len(imported_records),
        "timeline_bins": len(timeline),
        "event_count": len(events),
        "evidence_count": len(evidence_index),
        "paths": paths,
    }
    write_markdown_report(out_dir / "result_report.md", summary, events, sync_report, auto_perspective, evidence_index)
    write_json(out_dir / "run_summary.json", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fuse data collection archive, external videos, and detector JSON evidence.")
    subparsers = parser.add_subparsers(dest="command")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect an archive and optional extra videos.")
    inspect_parser.add_argument("--archive", default=str(DEFAULT_ARCHIVE if DEFAULT_ARCHIVE.exists() else ""))
    inspect_parser.add_argument("--extra-video", action="append", default=[])
    inspect_parser.add_argument("--json", action="store_true")

    analyze_parser = subparsers.add_parser("analyze", help="Run fusion analysis.")
    analyze_parser.add_argument("--archive", default=str(DEFAULT_ARCHIVE if DEFAULT_ARCHIVE.exists() else ""))
    analyze_parser.add_argument("--extra-video", action="append", default=[])
    analyze_parser.add_argument("--detector-json", action="append", default=[])
    analyze_parser.add_argument("--out-dir", default="")
    analyze_parser.add_argument("--threshold", type=float, default=0.55)
    analyze_parser.add_argument("--bin-s", type=float, default=0.5)
    analyze_parser.add_argument("--sample-every", type=int, default=12)
    analyze_parser.add_argument("--max-frames", type=int, default=0)
    analyze_parser.add_argument("--motion-threshold", type=int, default=18)
    analyze_parser.add_argument("--min-area", type=float, default=30.0)
    analyze_parser.add_argument("--blur-kernel", type=int, default=5)
    analyze_parser.add_argument("--morph-kernel", type=int, default=3)
    analyze_parser.add_argument("--trail-frames", type=int, default=3)
    analyze_parser.add_argument("--max-motion-ratio", type=float, default=0.18)
    analyze_parser.add_argument("--analysis-scale", type=float, default=0.5)
    analyze_parser.add_argument("--audio-window-s", type=float, default=0.5)
    analyze_parser.add_argument("--max-evidence-per-source", type=int, default=40)
    analyze_parser.add_argument("--max-audio-evidence-per-source", type=int, default=8)
    analyze_parser.add_argument("--perspective-json", default="")
    analyze_parser.add_argument("--auto-perspective", dest="auto_perspective", action="store_true", default=True)
    analyze_parser.add_argument("--no-auto-perspective", dest="auto_perspective", action="store_false")
    analyze_parser.add_argument("--auto-perspective-reference", default="")
    analyze_parser.add_argument("--auto-perspective-samples", type=int, default=3)
    analyze_parser.add_argument("--auto-perspective-min-matches", type=int, default=24)
    analyze_parser.add_argument("--auto-perspective-min-inliers", type=int, default=18)
    analyze_parser.add_argument("--no-extra-video-audio", action="store_true")
    analyze_parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "inspect":
        payload = inspect_inputs(args)
    elif args.command == "analyze":
        payload = analyze(args)
    else:
        parser.print_help()
        return 0

    if getattr(args, "json", False):
        print(json.dumps(payload))
    else:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
