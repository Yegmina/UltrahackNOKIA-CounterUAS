# Phone Sensor Capability Check

Date: 2026-06-08  
Device: Ulefone Armor 28 Ultra Thermal  
ADB serial: `5011AF1010013479`

## Goal

Check whether we can get sound, normal camera video, and thermal imaging at the
same time, ideally without needing to launch ThermoVue.

## Result

On the current locked production phone state, we cannot get direct thermal
frames without ThermoVue. We also cannot get all public cameras plus thermal at
the same time from a normal Android app.

## What Worked

- A normal app can enumerate and capture from four public Camera2 camera IDs:
  `0`, `1`, `2`, `3`.
- A normal app can record microphone audio.
- A normal app can capture microphone audio and Camera2 camera `0` at the same
  time.
- Android reports one supported public concurrent camera set:

```text
concurrentCameraIdSets={{0, 1}}
```

This means public Android APIs only advertise camera `0` + camera `1` as a
supported simultaneous pair, not all four cameras.

## Thermal Without ThermoVue

With ThermoVue stopped:

```text
usbDeviceCount=0
```

The public app could not see the thermal USB module. Camera2 still exposed only
the four normal phone cameras and no thermal/Y16/depth thermal camera.

## Thermal With ThermoVue Running

After launching ThermoVue, Android's USB manager sees the internal thermal
module:

```text
name=/dev/bus/usb/001/002
vendor_id=13428
product_id=17185
manufacturer_name=Thermal Cam Co.,Ltd
product_name=Camera
serial_number=202206223
```

The same module appears to a normal app via `UsbManager`:

```text
usbDeviceCount=1
vendorId=0x3474
productId=0x4321
manufacturer="Thermal Cam Co.,Ltd"
product="Camera"
hasPermission=false
```

But Android denies the normal app's USB permission request:

```text
THERMAL_USB permission broadcast action=com.yegmina.sensorprobe.USB_PERMISSION
THERMAL_USB permission broadcast device=/dev/bus/usb/001/002 granted=false
THERMAL_USB permissionAfterRequest=false
```

Shell access is also blocked:

```text
dd: /dev/bus/usb/001/002: Permission denied
cat: /sys/devices/platform/yft_tiny2c_usb/tiny2c_usb_mode: Permission denied
cat: /sys/class/yft_extcon/tiny2c_mode: Permission denied
cat: /sys/devices/platform/yft_tiny2c_usb/sensor_id: Permission denied
```

## ThermoVue Force Stop After Power-On

If ThermoVue is launched to power the module and then force-stopped, the thermal
USB device disappears again:

```text
usbDeviceCount=0
```

So ThermoVue is not only a viewer. It appears to perform the privileged
power/mux/open step required to keep the internal thermal USB module visible.

## Practical Conclusion

For the hackathon MVP on this phone state:

- Use normal Android/IP camera paths for RGB/video/audio.
- Use ThermoVue screen capture for thermal if we need thermal immediately.
- Direct raw thermal requires one of:
  - vendor SDK/API access;
  - root/system privileges;
  - a privileged bridge app;
  - successful platform-signed/root instrumentation of ThermoVue's
    `IIrFrameCallback`.

The currently connected phone is locked production Android, so root/sysfs/direct
USB access and Frida injection are blocked.

## Follow-Up Probe Results

A debug-signed instrumentation APK was built and installed to test whether we
could run inside ThermoVue's target package context. Android rejected it:

```text
Permission Denial: starting instrumentation ... not allowed because package
com.yegmina.thermovueinstrumentationprobe does not have a signature matching
the target com.energy.tc2c
```

ThermoVue itself runs as:

```text
u:r:platform_app:s0:c512,c768 ... com.energy.tc2c
```

ADB root is unavailable and `su` is not installed:

```text
adbd cannot run as root in production builds
/system/bin/sh: su: inaccessible or not found
run-as: package not debuggable: com.energy.tc2c
```

So the realistic direct-sensor route on this exact phone is now narrowed to a
Ulefone/InfiSense SDK or a Ulefone-signed/platform/privileged helper. A normal
side-loaded APK can still be useful for RGB/audio and Jetson streaming, but not
for powering/opening the thermal module directly.

## USB Handler Follow-Up

The bridge was updated to register a static USB device filter for the thermal
module (`0x3474:0x4321`). Android lists it in `dumpsys usb`, but this still does
not grant access.

Runtime reflection found:

```text
UsbManager.grantPermission(UsbDevice, String)
```

Calling it from our side-loaded app fails with:

```text
SecurityException: Access denied, requires: android.permission.MANAGE_USB
```

`pm grant` also rejects `MANAGE_USB` because it is not runtime-changeable.

The ordinary USB permission UI opens, but the internal thermal USB device is
removed while the dialog is active, so the permission broadcast returns
`granted=false`. This confirms that the next real raw-thermal step needs vendor
privilege/signing, not just more UI automation.

Decompiled firmware USB code confirms an OEM rule:

```text
if (usbDevice != null && usbDevice.getProductId() == 17185) {
    Log.d(TAG, "yft ignore YF USB attach notification ---");
    return;
}
```

So static USB activity filters are intentionally ignored for the thermal module
product ID `0x4321`. A Ulefone-side fixed USB host connection handler can bypass
that path because it grants permission before launching the component.
