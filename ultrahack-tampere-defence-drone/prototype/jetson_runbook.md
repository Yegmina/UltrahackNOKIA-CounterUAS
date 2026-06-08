# Jetson Counter-UAS Runbook

Use this when setting up the Jetson Orin Nano or a laptop as the inference box.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r prototype/requirements-counter-uas.txt
```

On Jetson, prefer the system OpenCV package if `opencv-python` is slow or fails
to install:

```bash
sudo apt-get update
sudo apt-get install -y python3-opencv
python -m pip install numpy pyserial
```

## Demo Mode

```bash
python prototype/counter_uas_fusion_node.py --demo --audio-demo --no-window --max-frames 90
```

## Phone RGB + Audio

Start IP Webcam on the phone, then use either the phone network URL or ADB
forwarding:

```bash
adb forward tcp:8080 tcp:8080
python prototype/counter_uas_fusion_node.py \
  --rgb-source http://127.0.0.1:8080/video \
  --audio-wav-url http://127.0.0.1:8080/audio.wav
```

## Native Thermal UDP

When the Android thermal bridge validates real frames, run the same fusion node
with the default thermal UDP listener:

```bash
python prototype/counter_uas_fusion_node.py \
  --rgb-source http://127.0.0.1:8080/video \
  --audio-wav-url http://127.0.0.1:8080/audio.wav \
  --thermal-port 25000
```

Start the Android bridge with `-JetsonHost <jetson-ip> -JetsonPort 25000`.

## Mount Control

For a programmable stand, use UDP first because it is easiest to debug:

```bash
python prototype/counter_uas_fusion_node.py \
  --demo \
  --audio-demo \
  --mount-udp-host 192.168.1.60 \
  --mount-udp-port 26000
```

The mount receives ASCII commands:

```text
PT pan=<speed> tilt=<speed> reason=<track|scan|hold|centered>
```
