# Counter-UAV / Counter-UAS Challenge Notes

Updated: 7 June 2026

## Challenge Interpretation

The June 2026 public materials mention Counter-UAS as one of the challenge areas. Based on the January event materials and Hackathon 2 framing, a strong project should probably address one or more of:

- Detecting drones in a difficult indoor/arena environment.
- Tracking drone movement over time.
- Maintaining situational awareness under degraded conditions.
- Helping an operator decide what to do safely.
- Validating performance with clear metrics rather than only showing a cool demo.

## Safe Problem Statements

Choose one of these if the challenge statement is still broad:

1. "Can we detect and track small UAVs reliably in a noisy indoor arena using low-cost sensors and confidence scoring?"
2. "Can we fuse weak evidence from video, audio, RF metadata, or telemetry into a clearer operator view?"
3. "Can we create a repeatable validation harness for counter-UAS systems under signal degradation, occlusion, and latency?"
4. "Can we reduce false alarms for security operators while preserving fast response to real drone presence?"

## Recommended MVP

Build an operator dashboard plus detection pipeline:

- Inputs: video feed, logged telemetry, synthetic test stream, audio sample, RF-like metadata, or any available event-provided feed.
- Processing: lightweight detection/classification, simple tracker, confidence over time, degraded-signal handling.
- Output: live status panel with "seen / uncertain / lost", confidence, last known position or sector, and event log.
- Validation: latency, detection confidence, false positives, false negatives, recovery time after signal loss.

## Scoring Narrative

Use this story in the pitch:

"Counter-UAS systems fail when they overpromise certainty. Our prototype makes uncertainty visible, tracks evidence over time, and gives operators a clear, auditable picture of what the system knows, what it does not know, and what changed."

## Technical Options

- Vision: YOLO-family detector, OpenCV motion detection, object tracking, or simple background subtraction if no dataset is available.
- Audio: spectral signature features, anomaly detection, or directional microphone proof-of-concept if equipment exists.
- RF / connectivity: jamming-aware telemetry health indicators, packet-loss simulation, latency monitor, link-quality dashboard.
- Sensor fusion: weighted confidence model combining independent weak signals.
- Simulation fallback: replay a prepared video/log stream through the same dashboard.

## Demo Checklist

- One live demo path.
- One replay/offline fallback path.
- Metrics visible on screen.
- Clear operator UI: no raw wall of logs.
- A 90-second pitch.
- A 3-minute technical explanation.
- A safety statement: detection and decision support only; no autonomous harm or weaponization.

## Risks

- No access to expected sensors or feeds.
- Indoor lighting, noise, and occlusion degrade vision/audio.
- Network latency or event Wi-Fi instability.
- Live drone availability may be constrained by arena rules.
- Team scope expands beyond what can be demoed by 11 June.

## Risk Reductions

- Bring a small recorded/replay dataset.
- Build a simulator interface that mimics live input.
- Make the UI sensor-agnostic: same display works for video, audio, or telemetry evidence.
- Keep model dependencies optional; basic OpenCV or logged data should still run.
- Define success by useful operator awareness, not perfect drone interception.

