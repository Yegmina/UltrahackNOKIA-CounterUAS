# ThermoVue Native Live Access Map

## Status

The high-FPS native live path is mapped, but not yet accessible from a normal
side-loaded app on the stock phone. ThermoVue succeeds because it can power the
Tiny2C thermal module and open the internal USB device in a platform/system
context. Our current non-root bridge can extract real raw frames from IJPEG
captures, but native live access still requires a valid `UsbControlBlock` and
the same native startup path ThermoVue uses.

## Java Startup Chain

ThermoVue's dual thermal preview goes through:

```text
UvcNativeCamDualFusionPreviewManager.initHandleEngine(ctrlBlock, readCal)
  DualUvcHandleParam.setCtrlBlock(ctrlBlock)
  IrcamEngine.Builder()
    .setLogLevel(SDK_LOG_DEBUG)
    .setStreamWidth(irStreamWidth)
    .setStreamHeight(irStreamHeight)
    .setDriverType(USB_DUAL_NATIVE_CAM)
    .setDualUvcHandleParam(dualUvcHandleParam)
    .build()
  IrcamEngine.initHandle(...)

UvcNativeCamDualFusionPreviewManager.startPreview()
  IrcamEngine.setIrFrameCallback(mIrFrameCallback)
  IrcamEngine.startVideoStream()
```

Key callback:

```java
com.energy.ac020library.bean.IIrFrameCallback.onFrame(byte[] frame, int length)
```

Driver enum values from `CommonParams`:

```text
USB = 0
SPI = 1
MIPI_I2C = 2
USB_DUAL = 3
MIPI_I2C_DUAL = 4
USB_DUAL_NATIVE_CAM = 5
USB_OMNI = 10
```

Frame output enum values:

```text
YUYV_IMAGE_OUTPUT = 0
NV12_IMAGE_OUTPUT = 1
NV12_AND_TEMP_OUTPUT = 2
YUYV_AND_TEMP_OUTPUT = 3
```

## Native Libraries

Generate a local report without NDK tools:

```powershell
py -3 prototype\native_elf_report.py prototype\logs\thermal_live_debug_20260609_111936\vendor_dump\thermovue_pro\base.apk --out prototype\logs\native_libs\thermovue_native_elf_report.md
```

Important native symbols found:

```text
libAC020sdk.so
  native_init_handle_engine(...)
  native_set_ir_frame_callback(...)
  native_video_stream_start()
  native_video_stream_stop()
  dual_common_ir_frame_callback(...)

libdualuvccamera020.so
  dual_iruvc_camera_open
  dual_iruvc_camera_start_stream
  dual_iruvc_camera_stop_stream
  dual_iruvc_camera_set_frame_callback
  dual_frame_callback
  libusb_get_device_with_fd
  libusb_control_transfer

libusbuvccamera020.so
  iruvc_camera_open
  iruvc_camera_start_stream
  iruvc_camera_set_frame_callback
  uvc_get_device_with_fd
  UVCCamera::setFrameCallback(...)
  UVCPreview::set_ir_frame_callback(...)

libircmd020.so
  basic_frame_temp_info_get
  basic_video_stream_pause
  basic_video_stream_continue
  adv_show_frame_temp
  adv_stream_source_mode_get/set
  adv_stream_mid_mode_get/set
```

## Required Control Block

`DualUvcHandleParam.setCtrlBlock()` clones ThermoVue's
`USBMonitor.UsbControlBlock` and extracts:

```text
venderId
productId
fileDescriptor
busNum
devNum
usbFSName
ctrlBlock
```

ThermoVue then adds:

```text
irFps
bandwidth
vlWidth
vlHeight
vlFps
```

`UsbControlBlock` itself is created by:

```text
UsbManager.openDevice(UsbDevice)
UsbDeviceConnection.getFileDescriptor()
UsbDevice.getDeviceName() -> /dev/bus/usb/<bus>/<dev>
```

The native libraries use FD-based libusb/uvc entry points such as
`libusb_get_device_with_fd` and `uvc_get_device_with_fd`, so a live clone cannot
skip Android USB permission and the open device FD.

## Current Non-Root Boundary

Verified on the stock phone:

- `com.energy.tc2c` runs as `u:r:platform_app:s0:c512,c768`.
- Side-loaded apps run as `u:r:untrusted_app:s0`.
- Shell cannot read ThermoVue process maps: `/proc/<pid>/maps: Permission denied`.
- Shell/app_process cannot call `vendor.yft.hardware.changenode@1.0::IChangeNode`
  because SELinux denies `hal_changenode_hwservice`.
- FactoryMode can power/open the module, but its thermal activity is not
  exported and the stream cannot be reused by our side-loaded app.
- When `Thermal Live Debug` is foreground, ThermoVue loses foreground and the
  internal thermal USB device disappears from normal app view
  (`usbDeviceCount=0`).
- When ThermoVue stays foreground, the same USB device remains visible
  (`/dev/bus/usb/001/002`, VID/PID `0x3474:0x4321`), but Android rejects the
  side-loaded app's background USB permission request:
  `usbPermissionBroadcast granted=false`.
- Hidden `UsbManager` grant attempts fail because `MANAGE_USB` is not granted to
  the side-loaded APK, and `cmd usb` exposes no shell permission command on this
  build.

## Best Next Live Routes

1. **Vendor/platform bridge:** get a platform-signed or privileged APK from
   Ulefone/InfiSense. Use the existing exact-startup bridge, then verify that
   `UsbControlBlock` is non-null and `IIrFrameCallback.onFrame` increments.
2. **In-process hook:** root/Frida/Xposed/LSPosed module inside
   `com.energy.tc2c`, hook `UvcNativeCamDualFusionPreviewManager$3.onFrame`,
   and forward the raw frame bytes.
3. **Native FD bridge:** if an official permission path can open the internal
   USB device for our UID, pass the resulting FD/bus/dev/usbfs values through
   `USB_DUAL_NATIVE_CAM` and call `startVideoStream()`.

The IJPEG bridge remains the working non-root fallback for real raw thermal
frames while one of these routes is arranged.
