# UltraHack Tampere Defence Drone Hackathon

Project workspace for the Drone Innovation Hackathon 2 at Nokia Arena, Tampere.

Current focus: Counter-UAV / Counter-UAS challenge preparation.

## Quick Context

- Event: Drone Innovation Hackathon 2
- Dates: 9-11 June 2026
- Place: Nokia Arena, Kansikatu 3, 33100 Tampere, Finland
- Format: Build, test, and refine drone and counter-drone solutions in a controlled indoor arena with 5G and drone testing facilities.
- Current date of this project setup: 7 June 2026

## Start Here

1. Read [research/event-brief.md](research/event-brief.md) for sourced hackathon facts.
2. Read [research/counter-uav-challenge-notes.md](research/counter-uav-challenge-notes.md) for the challenge framing and safe solution directions.
3. Use [planning/hackathon-plan.md](planning/hackathon-plan.md) as the short execution plan for the next few days.
4. Put prototype code, models, test scripts, or simulations under `prototype/`.
5. Put diagrams, pitch assets, screenshots, and datasets under `assets/`.

## Recommended Project Direction

Build a defensive Counter-UAS validation demo focused on:

- Detecting or classifying drones from one or more benign sensor streams.
- Tracking confidence over time instead of making a single brittle detection.
- Demonstrating resilience under degraded conditions such as noise, signal loss, occlusion, latency, or arena interference.
- Producing an operator-friendly status view: target seen, confidence, uncertainty, last known direction, and safe next action.

Avoid anything that creates physical harm, autonomous engagement, weaponization, or evasion guidance. For the hackathon, a credible validation story is more valuable than a flashy but unsafe demo.

