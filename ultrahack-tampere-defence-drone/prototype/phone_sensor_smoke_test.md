# Phone Sensor Smoke Test

This test verifies the easiest high-FPS phone sensor path:

```text
IP Webcam on Ulefone
  -> ADB tcp forward 8080
  -> Python/Jetson reads http://127.0.0.1:8080
```

## Prerequisites

1. Phone connected with ADB authorized.
2. IP Webcam running on the phone.
3. IP Webcam server started.

The server screen should show a URL like:

```text
http://PHONE_IP:8080
```

ADB forwarding lets the host use:

```text
http://127.0.0.1:8080
```

## Run

```powershell
& "C:\Users\teres\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" prototype\phone_sensor_smoke_test.py
```

## Expected Results

- `/status.json` returns device metadata.
- `/shot.jpg` returns repeated JPEG frames.
- `/video` returns an MJPEG multipart stream.
- `/audio.wav`, `/audio.aac`, and `/audio.opus` return microphone audio streams.

This is the preferred path for the RGB and audio parts of the hackathon MVP.

