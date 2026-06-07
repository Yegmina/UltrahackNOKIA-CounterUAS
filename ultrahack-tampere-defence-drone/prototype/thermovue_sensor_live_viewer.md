# ThermoVue Sensor Live Viewer

This script is the laptop-side viewer for true ThermoVue sensor packets. It is separate from `thermal_stream_test.py`, which captures the phone screen.

## What It Expects

`thermovue_sensor_live_viewer.py` expects raw packets with the layout found in `thermovue_reverse_engineering.md`:

- IR: `256 x 192 x uint16`
- Info lines: `256 x 2 x uint16`
- Temperature: `256 x 192 x uint16`
- Visible RGB: `1440 x 1080 x 3`
- Total: `4,863,232` bytes per frame

The phone still needs a bridge/hook to forward `IIrFrameCallback.onFrame(byte[], int)` bytes to the laptop.

## Run Demo Visualization

```powershell
py -3 ultrahack-tampere-defence-drone\prototype\thermovue_sensor_live_viewer.py --source demo
```

Headless smoke test:

```powershell
py -3 ultrahack-tampere-defence-drone\prototype\thermovue_sensor_live_viewer.py --source demo --headless --frames 3
```

## Listen For Real Sensor Packets

Once the phone bridge sends raw packets over TCP:

```powershell
py -3 ultrahack-tampere-defence-drone\prototype\thermovue_sensor_live_viewer.py --source tcp-listen --bind 0.0.0.0 --port 7777
```

The default protocol is fixed-size frames: exactly `4,863,232` bytes per packet. A bridge can also send a 4-byte little-endian length prefix:

```powershell
py -3 ultrahack-tampere-defence-drone\prototype\thermovue_sensor_live_viewer.py --source tcp-listen --protocol u32le --port 7777
```

## Check Phone State

```powershell
py -3 ultrahack-tampere-defence-drone\prototype\thermovue_sensor_live_viewer.py --check-phone --launch-thermovue
```

This checks whether ThermoVue is installed and whether Android's USB host manager sees the internal thermal camera. It does not pull raw packets by itself.
