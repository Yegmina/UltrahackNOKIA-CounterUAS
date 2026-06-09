# ThermoVue Xposed Frame Forwarder

This is the in-process route for full live thermal access when the phone can run
LSPosed/Xposed or when Ulefone/InfiSense can load equivalent hook code inside
`com.energy.tc2c`.

The module hooks ThermoVue's real Java IR callbacks:

```text
com.energy.dualmodule.sdk.uvc.UvcNativeCamDualFusionPreviewManager$3.onFrame(byte[], int)
com.energy.tc2c.sop.camera.UvcNativeCamDualCalManager$mIIrFrameCallback$1.onFrame(byte[], int)
```

For full ThermoVue packets it extracts the temperature plane using the mapped
layout:

```text
[ir_u16le 98304][info 1024][temp_u16le 98304][visible/fusion bytes...]
```

It forwards `temp_u16le` as `YEGMINA_THERMAL_RAW_V1` UDP packets, which are
already accepted by:

```powershell
py -3 prototype\thermal_udp_receiver.py --host 0.0.0.0 --port 25000
py -3 prototype\counter_uas_fusion_node.py --thermal-port 25000
```

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
```

Defaults:

```text
host=255.255.255.255
port=25000
every=1
```

## Stock Phone Status

This APK will install on the stock phone, but it will not activate without an
Xposed/LSPosed/root/vendor-injected environment. It exists so that once the
in-process route is available, the actual frame forwarding path is already
implemented and uses the same Jetson UDP receiver as the IJPEG fallback.
