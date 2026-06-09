# ThermoVue IJPEG Raw Thermal Extraction

## Status

Confirmed on 2026-06-09 with the connected Ulefone Armor 28 Ultra Thermal:
ThermoVue Pro captures saved under `/sdcard/Pictures/thermo_tc2c/*.jpg`
contain raw thermal data in vendor IJPEG metadata segments.

This is not a native live callback yet. It is a working bridge for real
256x192 thermal frames using ThermoVue as the privileged capture process.

## Evidence

Captured file:

```text
/sdcard/Pictures/thermo_tc2c/1780999642030.jpg
size=7,415,425 bytes
```

JPEG marker layout:

```text
APP2 IJPEG descriptor at byte 242
APP3 chunks: 98 chunks, total payload 6,417,408 bytes
SOS/JPEG preview starts at byte 6,436,718
```

APP3 payload split from the IJPEG descriptor:

```text
ir_u16le    256x192 uint16 =    98,304 bytes
temp_u16le  256x192 uint16 =    98,304 bytes
rgba       1080x1440 RGBA = 6,220,800 bytes
```

The extracted 16-bit planes render as coherent thermal images. Example stats
from the first confirmed capture:

```text
ir_u16le:   min=32790 max=32863 mean=32834.9546
temp_u16le: min=18964 max=19176 mean=19108.8862
```

The `temp_u16le` values are not yet mapped to Celsius. For detection and
tracking, use the raw relative frame values or normalized contrast until the
ThermoVue temperature calibration formula is cloned.

APP5 metadata is decoded into the extractor summary for calibration context.
In the first confirmed sample it contained:

```text
env_temp=17.0 dist=4.5 ems=1.0 hum=0.5 ref_temp=25.0
center_temp_raw=0 max_temp_raw=0 min_temp_raw=0
```

## Extract One Capture

Pull or copy a ThermoVue JPEG to the laptop, then run:

```powershell
py -3 prototype\thermovue_ijpeg_extract.py prototype\logs\ijpeg_probe\1780999642030.jpg --out-dir prototype\logs\ijpeg_probe\extracted
```

The extractor writes exact raw planes:

```text
*.ir_u16le.256x192.u16le
*.temp_u16le.256x192.u16le
*.rgba.1080x1440.rgba
*.ijpeg_summary.json
```

It also writes PNG previews when Pillow is available.

## Live-ish ADB Bridge

Run ThermoVue in the foreground, trigger its photo button by ADB, pull each new
IJPEG, extract the 16-bit thermal plane, and display or forward it:

```powershell
py -3 prototype\thermovue_ijpeg_live_pull.py --max-frames 2 --no-window --interval 1.0
```

Confirmed test result on 2026-06-09:

```text
frame=1 file=1780999983637.jpg plane=temp_u16le min=18448 max=18780 mean=18607.2 latency=1.76s
frame=2 file=1780999985261.jpg plane=temp_u16le min=18472 max=18804 mean=18628.2 latency=1.75s
```

Fastest repeated capture test on 2026-06-09 with `--interval 0.0`:

```text
frame=1 file=1781001190933.jpg plane=temp_u16le min=17932 max=18608 mean=18094.8 latency=1.68s
frame=2 file=1781001192669.jpg plane=temp_u16le min=17932 max=18608 mean=18098.1 latency=1.89s
frame=3 file=1781001194579.jpg plane=temp_u16le min=18360 max=18980 mean=18499.5 latency=1.95s
frame=4 file=1781001196525.jpg plane=temp_u16le min=18332 max=18960 mean=18478.4 latency=1.87s
frame=5 file=1781001198626.jpg plane=temp_u16le min=18220 max=18884 mean=18390.5 latency=2.15s
```

Forward extracted frames to the existing UDP receiver / Jetson path:

```powershell
py -3 prototype\thermal_udp_receiver.py --host 0.0.0.0 --port 25000 --save-dir prototype\logs\thermal_udp_frames
py -3 prototype\thermovue_ijpeg_live_pull.py --udp-host 127.0.0.1 --udp-port 25000
```

This sends the same `YEGMINA_THERMAL_RAW_V1` packet format used by the native
bridge prototype.

Local UDP reassembly was also verified:

```text
udp_frame=1 total=98304 chunks=76 packets=76
```

## Meaning For The Counter-UAS Prototype

This path proves that the phone can produce real raw thermal frames, and it
gives us an immediately usable thermal input for fusion tests. It is slower
than true live sensor callbacks because each frame goes through ThermoVue photo
capture and ADB pull. The native target remains:

1. Run in ThermoVue's platform context, or obtain a vendor/platform-signed build.
2. Hook or call `UvcNativeCamDualFusionPreviewManager.mIrFrameCallback`.
3. Forward `temp_u16le` frames directly over USB/Wi-Fi/UDP without saving JPEGs.
