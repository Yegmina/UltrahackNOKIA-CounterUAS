# Phone To Jetson Connection Plan

Updated: 7 June 2026

## Recommendation

Use the Ulefone as the sensor and the Jetson Orin Nano as the inference box.

Best first implementation:

```text
Ulefone RGB camera app / IP camera app
  -> local network stream
  -> Jetson OpenCV/GStreamer receiver
  -> detector + tracker + confidence logic
  -> local web dashboard
```

Use USB tethering or a private 5 GHz/Wi-Fi 7 hotspot as the network link. Start with RGB video. Treat thermal as a second-stage input because live thermal camera access may not appear through normal Android Camera2 APIs.

## Ranked Options

### 1. USB Tethering + Phone IP Camera App

Most practical for the hackathon.

```text
Phone runs IP camera / RTSP / HTTP MJPEG app
Phone connected to Jetson by USB cable
Android USB tethering creates network link
Jetson reads phone stream by URL
```

Pros:

- More stable than crowded event Wi-Fi.
- Phone can charge while connected.
- Jetson sees the phone over a local network interface.
- No custom Android app needed for version 1.
- Good for RGB and possibly microphone if the app exposes audio.

Cons:

- Need to verify the IP address assigned by Android tethering.
- Some IP camera apps expose only RGB camera, not thermal.
- USB cable and port strain need physical mounting.

Use this first if USB tethering works on the Jetson.

### 2. Private Wi-Fi Hotspot + Phone IP Camera App

Good fallback if USB tethering is annoying.

```text
Phone creates hotspot OR Jetson creates hotspot
Both devices join same private network
Phone streams RTSP/MJPEG/WebRTC
Jetson consumes stream
```

Pros:

- Simple setup.
- No cable dependency.
- Works with common Android camera streaming apps.
- Ulefone advertises Wi-Fi 7, so the radio side should be strong.

Cons:

- Event RF environment may be noisy.
- Latency and dropped frames can vary.
- Battery drain is higher.

Use this as fallback and demo mode.

### 3. USB Screen Capture / Mirroring

Useful for thermal proof-of-concept if the thermal app cannot be accessed programmatically.

```text
Phone opens thermal camera app
Phone screen is mirrored/captured
Jetson or laptop processes the screen video
```

Pros:

- May be the fastest way to include thermal imagery.
- Avoids vendor SDK/API problem.
- Works even if thermal camera is locked inside Ulefone's app.

Cons:

- Lower quality than raw thermal frames.
- UI overlays may contaminate the image.
- More fragile and less elegant.

Use this to demo thermal concept if native thermal access fails.

### 4. Custom Android Sensor App

Best long-term product route, not the first hackathon route.

```text
Android app captures RGB + audio
App sends compressed frames/audio chunks to Jetson
Jetson runs inference
```

Pros:

- Full control over frame rate, resolution, timestamps, audio chunks, and metadata.
- Can add exact synchronization later.
- Cleaner product story.

Cons:

- Requires Android development during the hackathon.
- Thermal camera may still be blocked without vendor SDK access.
- More failure points.

Use only after the RGB Jetson pipeline is already working.

### 5. Fully Phone-Only App

Best future product, highest short-term complexity.

Pros:

- Most portable.
- No Jetson box.
- Strong field demo.

Cons:

- Requires mobile model conversion and optimization.
- Requires Android UI and camera/audio handling.
- Thermal integration remains uncertain.

Do not start here for the hackathon.

## Suggested MVP Protocols

### Video

Start with either:

- RTSP H.264 stream if the app supports it.
- HTTP MJPEG stream if RTSP buffering becomes annoying.

RTSP is standard and efficient, but OpenCV/GStreamer buffering can create latency if the receiver does not drop old frames. MJPEG is less efficient but easier to debug.

### Audio

Start simple:

- Use the phone mic through the same streaming app if audio is exposed.
- If not, run a separate Android audio recorder/streamer later.
- As fallback, record audio samples and replay them through the Jetson classifier.

### Thermal

Use this order:

1. Check whether the Ulefone thermal app can record video or export images.
2. Try screen recording/mirroring the thermal app for proof-of-concept.
3. Only then investigate a vendor SDK or hidden camera interface.

## Jetson Receiver Notes

For OpenCV, avoid stale buffered frames. The receiver should always process the newest available frame and drop old frames.

Typical GStreamer shape:

```text
rtspsrc location=rtsp://PHONE_IP:PORT/PATH latency=0
  ! decodebin
  ! videoconvert
  ! appsink drop=1
```

If the stream is H.264 and Jetson decoding behaves badly, test with plain `gst-launch-1.0` first, then connect it to OpenCV.

## Test Plan

1. Phone and Jetson on same network.
2. Start phone IP camera stream.
3. Open stream on Jetson with VLC or `gst-launch-1.0`.
4. Open stream in Python/OpenCV.
5. Measure latency by filming a stopwatch or hand clap.
6. Run detector on 640x360 or 640x480 first.
7. Increase resolution only if FPS stays acceptable.

## Hackathon Build Order

1. USB tethering or private hotspot.
2. RGB stream into Jetson.
3. Detector on live stream.
4. Dashboard and confidence timeline.
5. Audio side-channel.
6. Thermal screen-capture or exported thermal replay.
7. Fusion score.

## Decision

For the first working version:

```text
USB tethering + Android IP camera stream + Jetson receiver
```

Fallback:

```text
private hotspot + Android IP camera stream
```

Thermal path:

```text
thermal app screen capture or exported thermal video first,
native thermal API later
```

