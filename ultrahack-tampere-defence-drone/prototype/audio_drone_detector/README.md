# Audio Drone Detector Prototype

Standalone workspace for testing `Rashidbm/samid-drone-detector` and sending
audio-confidence events into the larger Counter-UAS prototype.

The Hugging Face model card describes the model as an Audio Spectrogram
Transformer fine-tuned for binary acoustic drone detection. It is trained on
1.0 second mono windows at 16 kHz and recommends 1.0 second windows with 0.5
second hops, median filtering, and a requirement for multiple consecutive
windows above threshold.

## Install

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\audio_drone_detector\install_audio_detector_cpu.ps1
```

## Test Interface

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\audio_drone_detector\run_audio_detector_ui.ps1
```

Use the file tab for saved recordings, or the WAV URL tab for a snapshot from
IP Webcam:

```text
http://127.0.0.1:8080/audio.wav
```

If ADB forwarding is needed:

```powershell
adb forward tcp:8080 tcp:8080
```

## CLI

Run a local file:

```powershell
py -3 prototype\audio_drone_detector\audio_drone_detector.py file .\clip.wav --json
```

If the global Python has a CUDA `torch` install that fails with `cufft64_10.dll`
or `WinError 1455`, use the virtual environment created by
`install_audio_detector_cpu.ps1`:

```powershell
prototype\audio_drone_detector\.venv-audio-detector\Scripts\python.exe prototype\audio_drone_detector\audio_drone_detector.py file .\clip.wav --json
```

Run a WAV URL snapshot and append compact JSONL:

```powershell
py -3 prototype\audio_drone_detector\audio_drone_detector.py url http://127.0.0.1:8080/audio.wav --jsonl-out prototype\logs\audio_detector.jsonl
```

Send a compact event to the fusion node over UDP:

```powershell
py -3 prototype\audio_drone_detector\audio_drone_detector.py file .\clip.wav --udp-host 127.0.0.1 --udp-port 25100
py -3 prototype\counter_uas_fusion_node.py --demo --audio-event-port 25100 --no-window
```

## Event Contract

Compact UDP/JSONL events contain:

```json
{
  "timestamp": 1781010000.0,
  "source": "clip.wav",
  "model_id": "Rashidbm/samid-drone-detector",
  "p_drone": 0.82,
  "detected": true,
  "threshold": 0.65,
  "consecutive_required": 3,
  "consecutive_hits": 4,
  "window_count": 11,
  "sample_rate": 16000,
  "duration_s": 6.0
}
```

The fusion node treats `p_drone` as the audio confidence. This keeps audio,
RGB, and thermal processes separable while still allowing a combined live demo.
