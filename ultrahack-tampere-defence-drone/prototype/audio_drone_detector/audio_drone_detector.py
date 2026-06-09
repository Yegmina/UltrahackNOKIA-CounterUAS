"""Audio drone detector wrapper for Rashidbm/samid-drone-detector.

The model card recommends 1.0 second windows at 16 kHz mono, 0.5 second hops,
median filtering, and requiring consecutive positive windows. This module keeps
that logic reusable for a test UI, batch files, and a UDP bridge into the wider
Counter-UAS fusion node.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import time
import urllib.request
import wave
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np


MODEL_ID = "Rashidbm/samid-drone-detector"
TARGET_SAMPLE_RATE = 16_000
WINDOW_SECONDS = 1.0
HOP_SECONDS = 0.5


@dataclass
class WindowScore:
    start_s: float
    end_s: float
    p_drone: float
    p_drone_smooth: float


@dataclass
class AudioDetectionEvent:
    timestamp: float
    source: str
    model_id: str
    p_drone: float
    detected: bool
    threshold: float
    consecutive_required: int
    consecutive_hits: int
    window_count: int
    sample_rate: int
    duration_s: float
    windows: list[WindowScore]

    def to_json_dict(self, include_windows: bool = True) -> dict[str, Any]:
        record = asdict(self)
        if not include_windows:
            record.pop("windows", None)
        return record


class AudioDroneDetector:
    def __init__(
        self,
        model_id: str = MODEL_ID,
        device: str | None = None,
        target_sample_rate: int = TARGET_SAMPLE_RATE,
    ) -> None:
        self.model_id = model_id
        self.target_sample_rate = target_sample_rate
        self.device = device
        self._model: Any | None = None
        self._feature_extractor: Any | None = None
        self._torch: Any | None = None

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
        except ImportError as exc:
            raise RuntimeError(
                "Install prototype/audio_drone_detector/requirements-audio-drone-detector.txt "
                "before running the Hugging Face detector."
            ) from exc

        if self.device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._torch = torch
        self._feature_extractor = AutoFeatureExtractor.from_pretrained(self.model_id)
        self._model = AutoModelForAudioClassification.from_pretrained(self.model_id)
        self._model.to(self.device)
        self._model.eval()

    def predict_window(self, audio_16k: np.ndarray) -> float:
        self._load()
        assert self._torch is not None
        assert self._feature_extractor is not None
        assert self._model is not None

        audio_16k = np.asarray(audio_16k, dtype=np.float32)
        inputs = self._feature_extractor(
            audio_16k,
            sampling_rate=self.target_sample_rate,
            return_tensors="pt",
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with self._torch.no_grad():
            logits = self._model(**inputs).logits
            probs = self._torch.softmax(logits, dim=-1)[0].detach().cpu().numpy()

        id2label = getattr(self._model.config, "id2label", {}) or {}
        drone_index = find_drone_label_index(id2label, len(probs))
        return float(probs[drone_index])


def find_drone_label_index(id2label: dict[int | str, str], class_count: int) -> int:
    for raw_index, label in id2label.items():
        if "drone" in str(label).lower() and "no" not in str(label).lower():
            return int(raw_index)
    return min(1, class_count - 1)


def read_audio_file(path: Path) -> tuple[np.ndarray, int]:
    try:
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError("Install soundfile to read audio files.") from exc
    audio, sample_rate = sf.read(path, always_2d=False)
    return normalize_audio(audio), int(sample_rate)


def normalize_audio(audio: np.ndarray) -> np.ndarray:
    audio = np.asarray(audio)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    if np.issubdtype(audio.dtype, np.integer):
        max_value = float(np.iinfo(audio.dtype).max)
        audio = audio.astype(np.float32) / max_value
    else:
        audio = audio.astype(np.float32)
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(audio, -1.0, 1.0)


def resample_audio(audio: np.ndarray, sample_rate: int, target_sample_rate: int) -> np.ndarray:
    if sample_rate == target_sample_rate:
        return audio.astype(np.float32, copy=False)
    try:
        from scipy.signal import resample_poly
    except ImportError as exc:
        raise RuntimeError("Install scipy for resampling non-16 kHz audio.") from exc
    gcd = math.gcd(sample_rate, target_sample_rate)
    up = target_sample_rate // gcd
    down = sample_rate // gcd
    return resample_poly(audio, up, down).astype(np.float32)


def iter_windows(
    audio: np.ndarray,
    sample_rate: int,
    window_seconds: float = WINDOW_SECONDS,
    hop_seconds: float = HOP_SECONDS,
) -> Iterable[tuple[float, float, np.ndarray]]:
    window_samples = max(1, int(round(window_seconds * sample_rate)))
    hop_samples = max(1, int(round(hop_seconds * sample_rate)))
    if audio.size < window_samples:
        padded = np.zeros(window_samples, dtype=np.float32)
        padded[: audio.size] = audio
        yield 0.0, window_seconds, padded
        return
    for start in range(0, audio.size - window_samples + 1, hop_samples):
        end = start + window_samples
        yield start / sample_rate, end / sample_rate, audio[start:end]


def median_smooth(values: list[float], kernel_size: int) -> list[float]:
    if kernel_size <= 1 or len(values) <= 2:
        return values[:]
    if kernel_size % 2 == 0:
        kernel_size += 1
    radius = kernel_size // 2
    smoothed: list[float] = []
    for index in range(len(values)):
        lo = max(0, index - radius)
        hi = min(len(values), index + radius + 1)
        smoothed.append(float(np.median(values[lo:hi])))
    return smoothed


def analyze_audio(
    audio: np.ndarray,
    sample_rate: int,
    source: str,
    detector: AudioDroneDetector | None = None,
    predictor: Callable[[np.ndarray], float] | None = None,
    threshold: float = 0.65,
    consecutive_required: int = 3,
    median_kernel: int = 3,
) -> AudioDetectionEvent:
    if predictor is None:
        detector = detector or AudioDroneDetector()
        predictor = detector.predict_window

    audio = resample_audio(normalize_audio(audio), sample_rate, TARGET_SAMPLE_RATE)
    raw_scores: list[tuple[float, float, float]] = []
    for start_s, end_s, window in iter_windows(audio, TARGET_SAMPLE_RATE):
        raw_scores.append((start_s, end_s, float(predictor(window))))

    smoothed = median_smooth([score for _, _, score in raw_scores], median_kernel)
    windows = [
        WindowScore(start_s=start, end_s=end, p_drone=score, p_drone_smooth=smooth)
        for (start, end, score), smooth in zip(raw_scores, smoothed)
    ]

    consecutive_hits = 0
    best_run = 0
    for score in smoothed:
        if score >= threshold:
            consecutive_hits += 1
            best_run = max(best_run, consecutive_hits)
        else:
            consecutive_hits = 0

    p_drone = max(smoothed) if smoothed else 0.0
    return AudioDetectionEvent(
        timestamp=time.time(),
        source=source,
        model_id=MODEL_ID,
        p_drone=float(p_drone),
        detected=best_run >= consecutive_required,
        threshold=threshold,
        consecutive_required=consecutive_required,
        consecutive_hits=best_run,
        window_count=len(windows),
        sample_rate=TARGET_SAMPLE_RATE,
        duration_s=float(audio.size / TARGET_SAMPLE_RATE),
        windows=windows,
    )


def read_wav_bytes(payload: bytes) -> tuple[np.ndarray, int]:
    with wave.open(BytesIO(payload), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    if sample_width != 2:
        raise ValueError(f"Only 16-bit PCM WAV streams are supported, got {sample_width * 8}-bit.")
    pcm = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    return pcm, sample_rate


def fetch_wav_url(url: str, timeout: float = 10.0, max_bytes: int = 20_000_000) -> tuple[np.ndarray, int]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise RuntimeError(f"Response exceeded --max-bytes={max_bytes}.")
    return read_wav_bytes(payload)


def send_udp_event(event: AudioDetectionEvent, host: str, port: int) -> None:
    payload = json.dumps(event.to_json_dict(include_windows=False), separators=(",", ":")).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(payload, (host, port))
    finally:
        sock.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Test and bridge the Samid drone audio detector.")
    add_common_cli_options(parser)

    subparsers = parser.add_subparsers(dest="command", required=True)
    file_parser = subparsers.add_parser("file", help="Run detector on a local WAV/FLAC/OGG file.")
    file_parser.add_argument("path")
    add_common_cli_options(file_parser, duplicate=True)

    url_parser = subparsers.add_parser("url", help="Run detector on a WAV URL snapshot.")
    url_parser.add_argument("url")
    url_parser.add_argument("--max-bytes", type=int, default=20_000_000)
    add_common_cli_options(url_parser, duplicate=True)
    return parser


def add_common_cli_options(parser: argparse.ArgumentParser, duplicate: bool = False) -> None:
    default = argparse.SUPPRESS if duplicate else None
    parser.add_argument("--threshold", type=float, default=0.65 if not duplicate else default)
    parser.add_argument("--consecutive", type=int, default=3 if not duplicate else default)
    parser.add_argument("--median-kernel", type=int, default=3 if not duplicate else default)
    parser.add_argument(
        "--json",
        action="store_true",
        default=False if not duplicate else default,
        help="Write machine-readable JSON.",
    )
    parser.add_argument("--jsonl-out", default=default, help="Append event JSON to this file.")
    parser.add_argument("--udp-host", default=default, help="Send compact event JSON to this host.")
    parser.add_argument("--udp-port", type=int, default=25100 if not duplicate else default)


def emit_event(args: argparse.Namespace, event: AudioDetectionEvent) -> None:
    if args.json:
        print(json.dumps(event.to_json_dict(), indent=2))
    else:
        verdict = "DRONE" if event.detected else "clear"
        print(
            f"{verdict} p_drone={event.p_drone:.3f} "
            f"hits={event.consecutive_hits}/{event.consecutive_required} "
            f"windows={event.window_count} source={event.source}"
        )
    if args.jsonl_out:
        out = Path(args.jsonl_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_json_dict(include_windows=False)) + "\n")
    if args.udp_host:
        send_udp_event(event, args.udp_host, args.udp_port)


def main() -> None:
    args = build_parser().parse_args()
    detector = AudioDroneDetector()
    if args.command == "file":
        path = Path(args.path)
        audio, sample_rate = read_audio_file(path)
        event = analyze_audio(
            audio,
            sample_rate,
            source=str(path),
            detector=detector,
            threshold=args.threshold,
            consecutive_required=args.consecutive,
            median_kernel=args.median_kernel,
        )
    elif args.command == "url":
        audio, sample_rate = fetch_wav_url(args.url, max_bytes=args.max_bytes)
        event = analyze_audio(
            audio,
            sample_rate,
            source=args.url,
            detector=detector,
            threshold=args.threshold,
            consecutive_required=args.consecutive,
            median_kernel=args.median_kernel,
        )
    else:
        raise AssertionError(args.command)
    emit_event(args, event)


if __name__ == "__main__":
    main()
