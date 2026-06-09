# ThermoVue Video Artifacts

ThermoVue still photos are useful because they are IJPEG files that embed raw
thermal planes. ThermoVue `.mp4` recordings are different.

## Confirmed Sample

Pulled sample:

```text
/sdcard/Pictures/thermo_tc2c/2026_06_06_23_47_11.mp4
```

`ffprobe` reports only:

```text
video: H.264/AVC avc1, 1080x1440, about 22 fps
audio: AAC mono, 44100 Hz
```

The byte scan found no `IJPEG`, `APP3`, `APP5`, `uuid`, `rawTemp`,
`remapTemp`, or thermal marker payloads.

Run the reusable probe:

```powershell
py -3 prototype\thermovue_mp4_probe.py prototype\logs\thermovue_video_probe\2026_06_06_23_47_11.mp4 --out prototype\logs\thermovue_video_probe\mp4_probe.md --json prototype\logs\thermovue_video_probe\mp4_probe.json
```

## Code Path Evidence

The decompiled recording path uses Android media encoders:

```text
MediaRecordManager.callStartRecording(...)
  MediaSurfaceEncoder(...)
  MediaAudioEncoder(...)
  Tiny2CDualFusionProxy.getInstance().setMediaEncoder(...)

TakeVideoHelper.takeVideoByData(...)
  bitmapToArgb(...)
  ARGBToNV12(...) or ARGBToI420(...)
  MediaVideoBufferEncoder.encode(...)
```

So recorded MP4 is a visual/fusion recording. It is useful for visual demos or
offline visual detection, but it is not a raw thermal source like IJPEG photos
or the native `IIrFrameCallback.onFrame(...)` packets.
