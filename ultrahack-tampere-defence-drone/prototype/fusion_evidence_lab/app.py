"""Streamlit frontend for the fusion evidence lab."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from fusion_evidence_lab import DEFAULT_ARCHIVE, DEFAULT_EXTRA_VIDEO, DEFAULT_OUTPUT_ROOT, PROGRESS_PREFIX


SCRIPT_PATH = Path(__file__).with_name("fusion_evidence_lab.py")
UPLOAD_ROOT = DEFAULT_OUTPUT_ROOT / "_uploads"


st.set_page_config(page_title="Fusion Evidence Lab", layout="wide")
st.title("Fusion Evidence Lab")
st.caption("Multi-sensor archive parsing, video sync, perspective correction, and fused drone/aircraft evidence review.")


SETTING_DEFAULTS: dict[str, Any] = {
    "archive_path": str(DEFAULT_ARCHIVE) if DEFAULT_ARCHIVE.exists() else "",
    "extra_videos": str(DEFAULT_EXTRA_VIDEO) if DEFAULT_EXTRA_VIDEO.exists() else "",
    "detector_json": "",
    "threshold": 0.55,
    "bin_s": 0.5,
    "sample_every": 12,
    "max_frames": 0,
    "motion_threshold": 18,
    "min_area": 30.0,
    "blur_kernel": 5,
    "morph_kernel": 3,
    "trail_frames": 3,
    "max_motion_ratio": 0.18,
    "analysis_scale": 0.5,
    "audio_window_s": 0.5,
    "auto_perspective": True,
    "auto_perspective_reference": "",
    "auto_perspective_samples": 3,
    "auto_perspective_min_matches": 24,
    "auto_perspective_min_inliers": 18,
    "perspective_json": "",
}


def initialize_state() -> None:
    for key, value in SETTING_DEFAULTS.items():
        st.session_state.setdefault(key, value)


def parse_lines(value: str) -> list[str]:
    return [line.strip().strip('"') for line in value.splitlines() if line.strip()]


def save_upload(upload, subdir: str) -> Path:
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    target_dir = UPLOAD_ROOT / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / upload.name
    target.write_bytes(upload.getbuffer())
    return target


def profile_payload() -> dict[str, Any]:
    return {key: st.session_state.get(key, value) for key, value in SETTING_DEFAULTS.items()}


def load_profile(upload) -> None:
    payload = json.loads(upload.getvalue().decode("utf-8"))
    settings = payload.get("settings", payload)
    for key in SETTING_DEFAULTS:
        if key in settings:
            st.session_state[key] = settings[key]


def run_analyzer(args: list[str]) -> dict[str, Any]:
    process = subprocess.Popen(
        [sys.executable, str(SCRIPT_PATH), "analyze", "--json", *args],
        cwd=str(Path(__file__).parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    progress_box = st.empty()
    log_lines: list[str] = []
    json_line = ""
    started = time.time()
    assert process.stdout is not None
    while True:
        line = process.stdout.readline()
        if line:
            stripped = line.strip()
            if stripped.startswith(PROGRESS_PREFIX):
                log_lines.append(stripped.removeprefix(PROGRESS_PREFIX).strip())
                progress_box.info(f"{log_lines[-1]}  |  {time.time() - started:.1f}s")
            elif stripped.startswith("{"):
                json_line = stripped
            else:
                log_lines.append(stripped)
        if line == "" and process.poll() is not None:
            break
    stderr = process.stderr.read() if process.stderr is not None else ""
    if process.returncode != 0:
        raise RuntimeError(stderr.strip() or "\n".join(log_lines[-10:]) or f"Analyzer exited with {process.returncode}")
    if not json_line:
        raise RuntimeError(stderr.strip() or "Analyzer did not return JSON.")
    progress_box.success(f"Analysis complete in {time.time() - started:.1f}s")
    return json.loads(json_line)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render_events(summary: dict[str, Any]) -> None:
    events_path = summary["paths"]["events"]
    events = read_json(events_path)
    if not events:
        st.warning("No fused events passed the current threshold. Lower the threshold or inspect the timeline/evidence scores.")
        return
    rows = [
        {
            "event": event["event_index"],
            "start_s": round(event["start_s"], 2),
            "end_s": round(event["end_s"], 2),
            "duration_s": round(event["duration_s"], 2),
            "peak_score": round(event["peak_score"], 3),
            "sources": ", ".join(event["sources"]),
            "proofs": len(event.get("evidence", [])),
        }
        for event in events
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    selected = st.selectbox("Event proof gallery", [f"Event {event['event_index']}" for event in events])
    event = events[int(selected.split()[-1])]
    st.write(
        f"Peak score `{event['peak_score']:.2f}` from `{event['start_s']:.2f}s` to `{event['end_s']:.2f}s`."
    )
    render_evidence_cards(event.get("evidence", []), max_items=36)


def render_timeline(summary: dict[str, Any]) -> None:
    timeline = read_json(summary["paths"]["fusion_timeline"])
    if not timeline:
        st.info("Timeline is empty.")
        return
    frame = pd.DataFrame(timeline)
    chart_frame = frame[["start_s", "fused_score", "motion_score", "audio_score", "imported_score"]].set_index("start_s")
    st.line_chart(chart_frame)
    with st.expander("Timeline table"):
        st.dataframe(
            frame[
                [
                    "start_s",
                    "end_s",
                    "fused_score",
                    "motion_score",
                    "audio_score",
                    "imported_score",
                    "source_count",
                    "sources",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )


def render_evidence_cards(evidence: list[dict[str, Any]], max_items: int = 48) -> None:
    if not evidence:
        st.info("No proof images saved for this selection.")
        return
    for evidence_item in evidence[:max_items]:
        kind = evidence_item.get("kind", "evidence")
        score = float(evidence_item.get("score", 0.0))
        source = evidence_item.get("source_id", "")
        with st.container(border=True):
            st.write(f"**{kind}** `{source}` score `{score:.2f}`")
            columns = st.columns(2)
            annotated = evidence_item.get("annotated_path")
            motion = evidence_item.get("motion_path")
            audio = evidence_item.get("audio_path")
            if annotated and Path(annotated).exists():
                columns[0].image(annotated, caption="box proof", use_container_width=True)
            if motion and Path(motion).exists():
                columns[1].image(motion, caption="motion-only proof", use_container_width=True)
            if audio and Path(audio).exists():
                st.image(audio, caption="audio proof", use_container_width=True)


def render_downloads(summary: dict[str, Any]) -> None:
    st.subheader("Artifacts")
    cols = st.columns(7)
    artifact_keys = [
        ("summary", "run_summary.json"),
        ("events", "events.json"),
        ("fusion_timeline", "fusion_timeline.json"),
        ("sync_report", "sync_report.json"),
        ("auto_perspective", "auto_perspective.json"),
        ("evidence_index", "evidence_index.json"),
        ("result_report", "result_report.md"),
    ]
    for column, (key, filename) in zip(cols, artifact_keys):
        path = Path(summary["paths"][key])
        if path.exists():
            mime = "text/markdown" if filename.endswith(".md") else "application/json"
            column.download_button(filename, path.read_bytes(), file_name=filename, mime=mime)
    st.code(summary["paths"]["run_dir"])


def render_auto_perspective(summary: dict[str, Any]) -> None:
    path = Path(summary["paths"]["auto_perspective"])
    if not path.exists():
        st.info("No auto perspective report was saved.")
        return
    payload = read_json(path)
    st.json(payload)
    for source_id, item in payload.get("transforms", {}).items():
        if not isinstance(item, dict):
            continue
        preview = item.get("preview") or {}
        if not preview:
            continue
        st.write(f"**{source_id}**")
        columns = st.columns(2)
        matches = preview.get("matches_path")
        warp = preview.get("warp_preview_path")
        if matches and Path(matches).exists():
            columns[0].image(matches, caption="shared landmark matches", use_container_width=True)
        if warp and Path(warp).exists():
            columns[1].image(warp, caption="reference / auto-warp / overlay", use_container_width=True)


initialize_state()

with st.sidebar:
    st.header("Inputs")
    uploaded_archive = st.file_uploader("Data collection archive", type=["zip"])
    if uploaded_archive is not None:
        st.session_state.archive_path = str(save_upload(uploaded_archive, "archives"))
    st.text_input("Archive path", key="archive_path")

    uploaded_extra = st.file_uploader("Extra video upload", type=["mp4", "mov", "avi", "mkv"], accept_multiple_files=True)
    if uploaded_extra:
        saved_paths = [str(save_upload(upload, "extra_videos")) for upload in uploaded_extra]
        existing = parse_lines(st.session_state.extra_videos)
        st.session_state.extra_videos = "\n".join(existing + saved_paths)
    st.text_area("Extra video paths", key="extra_videos", height=90)

    uploaded_json = st.file_uploader("Detector JSON/JSONL upload", type=["json", "jsonl"], accept_multiple_files=True)
    if uploaded_json:
        saved_json = [str(save_upload(upload, "detector_json")) for upload in uploaded_json]
        existing = parse_lines(st.session_state.detector_json)
        st.session_state.detector_json = "\n".join(existing + saved_json)
    st.text_area("Detector JSON/JSONL paths", key="detector_json", height=72)

    st.header("Fusion")
    st.slider("Fused detection threshold", 0.05, 0.95, key="threshold", step=0.01)
    st.number_input("Timeline bin seconds", min_value=0.1, max_value=5.0, key="bin_s", step=0.1)
    st.number_input("Sample every N frames", min_value=1, max_value=300, key="sample_every", step=1)
    st.number_input("Max frames per video (0 = full)", min_value=0, max_value=1000000, key="max_frames", step=60)

    st.header("Motion")
    st.slider("Difference threshold", 1, 100, key="motion_threshold")
    st.number_input("Minimum area", min_value=1.0, max_value=100000.0, key="min_area", step=5.0)
    st.selectbox("Blur kernel", [1, 3, 5, 7, 9, 11], key="blur_kernel")
    st.selectbox("Morph kernel", [1, 3, 5, 7, 9], key="morph_kernel")
    st.slider("Trail frames", 1, 20, key="trail_frames")
    st.slider("Max motion ratio", 0.01, 1.0, key="max_motion_ratio", step=0.01)
    st.slider("Analysis scale", 0.05, 1.0, key="analysis_scale", step=0.05)
    st.number_input("Audio window seconds", min_value=0.1, max_value=5.0, key="audio_window_s", step=0.1)

    st.header("Perspective")
    st.checkbox("Autonomous perspective correction", key="auto_perspective")
    st.text_input("Reference source id", key="auto_perspective_reference", placeholder="auto")
    st.number_input("Auto sample frames per pair", min_value=1, max_value=9, key="auto_perspective_samples", step=1)
    st.number_input("Minimum landmark matches", min_value=8, max_value=200, key="auto_perspective_min_matches", step=2)
    st.number_input("Minimum RANSAC inliers", min_value=6, max_value=160, key="auto_perspective_min_inliers", step=2)
    with st.expander("Manual perspective override"):
        st.text_area(
            "Perspective JSON",
            key="perspective_json",
            height=150,
            placeholder='{"sources":{"demo1":{"src":[[0.1,0.2],[0.9,0.2],[0.95,0.9],[0.05,0.9]],"dst":[[0,0],[1,0],[1,1],[0,1]]}}}',
        )

    st.header("Profiles")
    profile_upload = st.file_uploader("Import settings", type=["json"], key="profile_upload")
    if profile_upload is not None and st.button("Load settings"):
        load_profile(profile_upload)
        st.rerun()
    st.download_button(
        "Export settings",
        json.dumps({"type": "fusion_evidence_lab_settings", "version": 1, "settings": profile_payload()}, indent=2),
        file_name="fusion_evidence_settings.json",
        mime="application/json",
    )


tabs = st.tabs(["Analyze", "Events", "Timeline", "Sync", "Perspective", "All Proofs"])

with tabs[0]:
    st.subheader("Run Analysis")
    st.write("Use the archive from data collection, then add one or more external videos for sync and fusion.")
    if st.button("Analyze archive and videos", type="primary"):
        archive_path = st.session_state.archive_path.strip().strip('"')
        if not archive_path:
            st.error("Choose a data collection archive first.")
            st.stop()
        run_root = DEFAULT_OUTPUT_ROOT / time.strftime("fusion_%Y%m%dT%H%M%S")
        cli_args = [
            "--archive",
            archive_path,
            "--out-dir",
            str(run_root),
            "--threshold",
            str(float(st.session_state.threshold)),
            "--bin-s",
            str(float(st.session_state.bin_s)),
            "--sample-every",
            str(int(st.session_state.sample_every)),
            "--max-frames",
            str(int(st.session_state.max_frames)),
            "--motion-threshold",
            str(int(st.session_state.motion_threshold)),
            "--min-area",
            str(float(st.session_state.min_area)),
            "--blur-kernel",
            str(int(st.session_state.blur_kernel)),
            "--morph-kernel",
            str(int(st.session_state.morph_kernel)),
            "--trail-frames",
            str(int(st.session_state.trail_frames)),
            "--max-motion-ratio",
            str(float(st.session_state.max_motion_ratio)),
            "--analysis-scale",
            str(float(st.session_state.analysis_scale)),
            "--audio-window-s",
            str(float(st.session_state.audio_window_s)),
            "--auto-perspective-samples",
            str(int(st.session_state.auto_perspective_samples)),
            "--auto-perspective-min-matches",
            str(int(st.session_state.auto_perspective_min_matches)),
            "--auto-perspective-min-inliers",
            str(int(st.session_state.auto_perspective_min_inliers)),
        ]
        if not st.session_state.auto_perspective:
            cli_args.append("--no-auto-perspective")
        if st.session_state.auto_perspective_reference.strip():
            cli_args.extend(["--auto-perspective-reference", st.session_state.auto_perspective_reference.strip()])
        for path in parse_lines(st.session_state.extra_videos):
            cli_args.extend(["--extra-video", path])
        for path in parse_lines(st.session_state.detector_json):
            cli_args.extend(["--detector-json", path])
        if st.session_state.perspective_json.strip():
            cli_args.extend(["--perspective-json", st.session_state.perspective_json.strip()])
        try:
            summary = run_analyzer(cli_args)
        except Exception as exc:
            st.error(f"Analyzer failed: {type(exc).__name__}: {exc}")
            st.stop()
        st.session_state.latest_summary_path = summary["paths"]["summary"]
        st.success(f"Finished. Found {summary['event_count']} fused events and {summary['evidence_count']} proof items.")
        render_downloads(summary)

summary_path = st.session_state.get("latest_summary_path")
summary = read_json(summary_path) if summary_path and Path(summary_path).exists() else None

with tabs[1]:
    st.subheader("Detected Events")
    if summary:
        cols = st.columns(4)
        cols[0].metric("Events", summary["event_count"])
        cols[1].metric("Proof items", summary["evidence_count"])
        cols[2].metric("Timeline bins", summary["timeline_bins"])
        cols[3].metric("Imported detector records", summary["imported_detector_records"])
        render_events(summary)
    else:
        st.info("Run analysis first.")

with tabs[2]:
    st.subheader("Fused Timeline")
    if summary:
        render_timeline(summary)
    else:
        st.info("Run analysis first.")

with tabs[3]:
    st.subheader("Sync Report")
    if summary:
        st.json(read_json(summary["paths"]["sync_report"]))
        with st.expander("Video summaries"):
            st.json(summary["video_summaries"])
    else:
        st.info("Run analysis first.")

with tabs[4]:
    st.subheader("Autonomous Perspective")
    if summary:
        render_auto_perspective(summary)
    else:
        st.info("Run analysis first.")

with tabs[5]:
    st.subheader("All Proofs")
    if summary:
        evidence_index = read_json(summary["paths"]["evidence_index"])
        render_evidence_cards(evidence_index, max_items=72)
    else:
        st.info("Run analysis first.")
