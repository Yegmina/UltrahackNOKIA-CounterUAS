# ThermoVue Frida Sensor Bridge

This is the real sensor-feed bridge path for ThermoVue.

It hooks ThermoVue's internal Java callback:

```text
com.energy.dualmodule.sdk.uvc.UvcNativeCamDualFusionPreviewManager$3.onFrame(byte[], int)
```

and forwards `4,863,232` byte raw packets to the laptop viewer.

## Important Limitation

This requires a Frida server/gadget that can instrument the ThermoVue process.
On the tested Armor 28 Ultra state, the phone is a locked production build:

```text
ro.debuggable=0
ro.secure=1
ro.adb.secure=1
ro.boot.veritymode=enforcing
su: unavailable
```

That means normal ADB/USB-C cannot inject into ThermoVue. The bridge is ready,
but it needs one of these:

- rooted phone with matching `frida-server` running as root;
- engineering/debug build;
- a legal vendor SDK/API;
- a repackaged/debuggable test copy with Frida Gadget, if allowed.

## Install Laptop Frida Tools

```powershell
py -3 -m pip install frida frida-tools
```

For a cleaner Python environment, use a virtual environment:

```powershell
py -3 -m venv .venv-thermovue
.\.venv-thermovue\Scripts\python.exe -m pip install -r ultrahack-tampere-defence-drone\prototype\requirements-thermovue-bridge.txt
```

## Start Viewer And Bridge

This launches ThermoVue, starts the local laptop viewer, attaches Frida, and
forwards every 5th raw packet:

```powershell
py -3 ultrahack-tampere-defence-drone\prototype\thermovue_frida_bridge.py --launch --start-viewer --every 5
```

Capture five raw packets without opening the viewer:

```powershell
py -3 ultrahack-tampere-defence-drone\prototype\thermovue_frida_bridge.py --launch --no-tcp --frames 5 --save-dir prototype\logs\thermovue_frames
```

If the phone is not rooted/debuggable, the script should fail before packet
capture with a Frida attach/server error. That is expected on the current
locked phone state and confirms that the remaining blocker is instrumentation
permission, not the laptop parser/viewer.

## Tested Locked-Phone Result

With the connected phone on 7 June 2026:

```text
Frida candidate process: pid=17160 name=ThermoVue Pro
Attaching Frida to com.energy.tc2c pid=17160...
Bridge could not attach/run: unable to connect to remote frida-server: closed
```

So the packet viewer and bridge code are ready, but this specific phone state
does not permit the injection needed to read the internal `onFrame` bytes.
