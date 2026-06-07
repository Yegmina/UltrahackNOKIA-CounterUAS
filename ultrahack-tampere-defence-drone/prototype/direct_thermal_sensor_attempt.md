# Direct Thermal Sensor Access Attempt

Updated: 7 June 2026

## Question

Can we access the Ulefone Armor 28 Ultra Thermal sensor directly, instead of capturing the ThermoVue app screen?

## Short Answer

Not through public Android app APIs on the tested phone state.

I built and installed a custom Android probe app that enumerates the normal app-accessible sensor surfaces:

- Android `SensorManager`
- Android `UsbManager`
- Android Camera2

It successfully listed sensors, checked USB devices, and captured frames from all public Camera2 camera IDs. None of those paths exposed thermal frames.

The thermal camera appears to be handled by Ulefone/ThermoVue vendor code using a private native USB/UVC/IR stack.

## What Was Tested

Custom probe app:

```text
package: com.yegmina.sensorprobe
```

Build script:

```powershell
prototype\build_sensor_probe.ps1
```

Probe source:

```text
prototype/android_sensor_probe/
```

The app:

- Enumerates Android hardware sensors.
- Enumerates app-visible USB devices.
- Enumerates Camera2 camera IDs.
- Logs characteristics, output formats, sensor sizes, and vendor keys.
- Opens each exposed camera ID.
- Captures YUV/JPEG frames where available.
- Captures RAW_SENSOR frames where available.
- Saves sample frames to app external storage for visual inspection.

## Android SensorManager Results

The app saw 23 Android hardware sensors. They were normal phone sensors, including accelerometer, gyroscope, magnetic field, orientation, light, pressure, proximity, step counter, and rotation/vector sensors.

No Android `SensorManager` entry had a thermal camera, IR imaging, UVC, or temperature-imaging identity.

## Android UsbManager Results

The app-visible USB device list was empty:

```text
usbDeviceCount=0
```

This suggests the thermal module is not exposed to ordinary Android apps as a standard USB host device. It may still be handled internally by vendor/system code, but not through the public `UsbManager` surface tested here.

## Camera2 Results

Android reports:

```text
Number of camera devices: 4
Number of normal camera devices: 4
Number of public camera devices visible to API1: 4
```

The probe saw camera IDs:

```text
0, 1, 2, 3
```

Camera pixel arrays:

```text
camera 0: 4096x3072
camera 1: 8160x6144
camera 2: 8160x6144
camera 3: 4624x3472
```

Output formats exposed by the cameras were normal camera formats:

```text
RAW_SENSOR
PRIVATE
YUV_420_888
HEIC
JPEG
YV12
vendor/custom formats
```

Important missing signs:

- No `640x512` thermal-sized sensor appeared.
- No `Y16` thermal format appeared.
- No `DEPTH16`-like thermal stream appeared.
- No Camera2 camera produced thermal palette or radiometric-looking output.

The captured JPEG/YUV/RAW samples were visually normal RGB/monochrome camera data. A checked JPEG sample showed an ordinary visible-light camera view.

## Vendor App Findings

Thermal app:

```text
package: com.energy.tc2c
system apk: /system/app/M190infisens/M190infisens.apk
label: ThermoVue Pro
```

Secondary package:

```text
package: com.energy.tc2c.sop
system apk: /system/app/M190infDlp/M190infDlp.apk
label: Tc2cDLP
```

ThermoVue Pro requests:

- `android.permission.CAMERA`
- `android.permission.RECORD_AUDIO`
- `android.hardware.usb.accessory`
- storage/media permissions
- network permissions

The APK includes native libraries and assets that strongly indicate private vendor thermal handling:

```text
libusbuvccamera020.so
libdualuvccamera020.so
libomniircamera020.so
libircam.so
libircmd020.so
libirutilssdk.so
libadvirtemp.so
libadvirtempac020.so
libadvirparse.so
libadvirprocess.so
libdualcalibration.so
calibration/*.bin
```

String scan found references such as:

```text
com/energy/iruvccamera/utils/IFrameCallback
libusb_control_transfer
ThermoVue
temperature correction
dual calibration
```

This suggests the thermal path is likely:

```text
thermal module
  -> vendor USB/UVC/native IR stack
  -> ThermoVue app rendering/processing
```

not:

```text
thermal module
  -> public Android Camera2 camera ID
```

## Low-Level Device Access

The phone has many `/dev/video*` nodes and `/dev/usb_accessory`, but normal shell access is denied:

```text
/dev/video0: Permission denied
/dev/video1: Permission denied
/dev/video5: Permission denied
/dev/video6: Permission denied
/dev/video100: Permission denied
/dev/usb_accessory: Permission denied
```

That means a normal unprivileged app cannot simply open V4L2 or USB device nodes directly.

## Conclusion

Direct thermal sensor access was attempted and did not work through public Android app APIs or unprivileged device-node access.

Current confirmed options:

1. Use ThermoVue app screen capture for live low-FPS thermal evidence.
2. Use ThermoVue app `screenrecord` MP4 for higher-FPS thermal clips.
3. Use IP Webcam for live RGB/audio.

Required for true direct thermal sensor access:

1. Vendor SDK/API for ThermoVue / TC2C / Infisens.
2. A custom Android app using the vendor `iruvccamera` / `usbuvccamera020` stack, if legally and technically usable.
3. Root/system privileges to access `/dev/video*`/USB nodes directly.
4. Another device/setup where the thermal module exposes itself as a public UVC/Camera2 source.

## Practical Hackathon Decision

For the hackathon MVP:

```text
live detector: RGB + audio via IP Webcam
thermal evidence: ThermoVue screen capture or recorded MP4 clips
```

For a later product:

```text
investigate vendor SDK or build a custom Android integration around com.energy.iruvccamera / native UVC IR libraries
```
