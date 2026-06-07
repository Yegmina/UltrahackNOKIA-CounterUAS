# Hackathon Execution Plan

Updated: 7 June 2026

## Main Goal

Create a defensible Counter-UAS prototype that demonstrates reliable detection/tracking awareness and validation under degraded arena conditions.

## Before Arrival

- Confirm the exact Counter-UAS challenge statement from UltraHack/event platform or organizer email.
- Identify what hardware and data sources the team can bring.
- Prepare offline replay data so the demo still works if live feeds fail.
- Prepare a one-slide architecture diagram.
- Prepare a one-slide evaluation table with metrics.

## Day 1

- Clarify judging criteria and available test environment.
- Select the final sensor/input path.
- Get the prototype running end-to-end with fake or replay data.
- Talk to mentors about what they consider operationally credible.
- Freeze MVP scope by end of day.

## Day 2

- Integrate live or event-provided data.
- Run repeated tests and record results.
- Improve operator dashboard and logging.
- Capture screenshots/video of successful runs.
- Prepare final pitch skeleton.

## Day 3

- Stabilize demo.
- Rehearse pitch.
- Keep a known-good offline fallback ready.
- Present measured results and next steps.

## MVP Backlog

- Input adapter for live camera or replay video.
- Simple detector/tracker.
- Confidence score over time.
- Dashboard with status, confidence, timeline, and alerts.
- Test harness for latency, packet loss, occlusion, and false alarms.
- Exportable demo log.

## Pitch Structure

1. Problem: Counter-UAS systems need trustworthy awareness under messy real conditions.
2. Solution: A sensor-agnostic confidence and tracking layer for operators.
3. Demo: Live/replay detection, confidence changes, lost/reacquired behavior.
4. Validation: Show metrics from arena-style degradation tests.
5. Path forward: Add more sensors, field-test, integrate with partner systems.

## Questions To Ask Organizers

- What exact inputs are available for the Counter-UAS challenge?
- Are teams expected to bring drones, sensors, or only software?
- What are the safety restrictions inside Nokia Arena?
- Are there specific sponsor systems we can integrate with?
- How will Counter-UAS submissions be judged?
- Can teams record test data during the event for final pitch evidence?

