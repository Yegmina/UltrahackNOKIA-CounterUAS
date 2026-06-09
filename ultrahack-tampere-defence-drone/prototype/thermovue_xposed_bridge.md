# ThermoVue Xposed Frame Forwarder

This is the in-process route for full live thermal access when the phone can run
LSPosed/Xposed or when Ulefone/InfiSense can load equivalent hook code inside
`com.energy.tc2c`.

The module hooks ThermoVue's real Java IR callbacks:

```text
com.energy.dualmodule.sdk.uvc.UvcNativeCamDualFusionPreviewManager$3.onFrame(byte[], int)
com.energy.tc2c.sop.camera.UvcNativeCamDualCalManager$mIIrFrameCallback$1.onFrame(byte[], int)
com.energy.dualmodule.sdk.uvc.UvcNativeCamDualFusionPreviewManager$1.onFrame(byte[])
```

For full ThermoVue raw packets it extracts the temperature plane using the
mapped layout:

```text
[ir_u16le 98304][info 1024][temp_u16le 98304][visible/fusion bytes...]
```

The one-argument callback is a fallback for ThermoVue's fusion temperature
callback:

```text
[fusion_rgba 6220800][temp_u16le 98304][optional tail]
```

The bridge prefers raw `IIrFrameCallback` packets. It only forwards the fusion
temperature callback when no raw packet has been seen recently, so both hooks can
be enabled without intentionally duplicating frames.

It forwards `temp_u16le` as `YEGMINA_THERMAL_RAW_V1` UDP packets, which are
already accepted by:

```powershell
py -3 prototype\thermal_udp_receiver.py --host 0.0.0.0 --port 25000
py -3 prototype\counter_uas_fusion_node.py --thermal-port 25000
```

The bridge also has optional deeper forwarding for validating "same as
ThermoVue" access. The raw ThermoVue callback contains:

```text
[ir_u16le 98304][info 1024][temp_u16le 98304][visible_rgb 4665600]
```

By default these larger payloads are disabled so the phone does not flood the
network. Enable them only while validating a privileged/in-process build:

```bash
adb shell setprop debug.yegmina.thermal_ir_every 25
adb shell setprop debug.yegmina.thermal_packet_every 100
```

- `thermal_ir_every=N` sends the 256x192 raw IR plane every `N` raw callback
  frames using `YEGMINA_THERMAL_FRAME_V2 kind=ir_u16le`.
- `thermal_packet_every=N` sends the full ThermoVue raw callback packet every
  `N` raw callback frames using
  `YEGMINA_THERMAL_FRAME_V2 kind=thermovue_raw_packet`.

`prototype/thermal_udp_receiver.py` now reassembles both the original V1
temperature stream and these optional V2 payloads. For full raw packets it saves
the binary payload and extracts the embedded temperature plane for immediate
preview.

## Build

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File prototype\build_thermovue_xposed_bridge.ps1
```

Output:

```text
prototype/android_thermovue_xposed_bridge/build/thermovue-xposed-bridge.apk
```

## Runtime Configuration

The hook reads debug system properties from inside the ThermoVue process:

```bash
adb shell setprop debug.yegmina.thermal_host <jetson-ip-or-broadcast>
adb shell setprop debug.yegmina.thermal_port 25000
adb shell setprop debug.yegmina.thermal_every 1
adb shell setprop debug.yegmina.thermal_ir_every 0
adb shell setprop debug.yegmina.thermal_packet_every 0
```

Defaults:

```text
host=255.255.255.255
port=25000
thermal_every=1
thermal_ir_every=0
thermal_packet_every=0
```

## Stock Phone Status

This APK will install on the stock phone, but it will not activate without an
Xposed/LSPosed/root/vendor-injected environment. It exists so that once the
in-process route is available, the actual frame forwarding path is already
implemented and uses the same Jetson UDP receiver as the IJPEG fallback.
