package com.yegmina.thermovuexposed;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;
import de.robv.android.xposed.callbacks.XC_LoadPackage;

public final class ThermoVueFrameHook implements IXposedHookLoadPackage {
    private static final String TAG = "YegminaThermoVueHook";
    private static final String MAGIC = "YEGMINA_THERMAL_RAW_V1 ";
    private static final String MAGIC_V2 = "YEGMINA_THERMAL_FRAME_V2 ";
    private static final int WIDTH = 256;
    private static final int HEIGHT = 192;
    private static final int PLANE_BYTES = WIDTH * HEIGHT * 2;
    private static final int INFO_BYTES = 1024;
    private static final int TEMP_OFFSET = PLANE_BYTES + INFO_BYTES;
    private static final int VISIBLE_RGB_BYTES = 1440 * 1080 * 3;
    private static final int RAW_PACKET_BYTES = TEMP_OFFSET + PLANE_BYTES + VISIBLE_RGB_BYTES;
    private static final int FUSION_RGBA_BYTES = 1080 * 1440 * 4;
    private static final int UDP_CHUNK_BYTES = 1200;
    private static final long RAW_PREFERRED_WINDOW_MS = 2000;

    private static final String[] TARGET_PACKAGES = {
            "com.energy.tc2c",
            "com.energy.tc2c.sop",
    };

    private static final String[] CALLBACK_CLASSES = {
            "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualFusionPreviewManager$3",
            "com.energy.tc2c.sop.camera.UvcNativeCamDualCalManager$mIIrFrameCallback$1",
    };

    private static final String[] TEMP_CALLBACK_CLASSES = {
            "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualFusionPreviewManager$1",
    };

    private static final AtomicInteger FRAME_COUNTER = new AtomicInteger();
    private static final AtomicLong LAST_RAW_FRAME_AT_MS = new AtomicLong(0);
    private static final ExecutorService SENDER = Executors.newSingleThreadExecutor(runnable -> {
        Thread thread = new Thread(runnable, "yegmina-thermal-udp");
        thread.setDaemon(true);
        return thread;
    });

    @Override
    public void handleLoadPackage(XC_LoadPackage.LoadPackageParam lpparam) {
        if (!isTargetPackage(lpparam.packageName)) {
            return;
        }
        log("loaded in " + lpparam.packageName);
        for (String className : CALLBACK_CLASSES) {
            hookRawCallback(lpparam.classLoader, className);
        }
        for (String className : TEMP_CALLBACK_CLASSES) {
            hookTempCallback(lpparam.classLoader, className);
        }
    }

    private static boolean isTargetPackage(String packageName) {
        for (String target : TARGET_PACKAGES) {
            if (target.equals(packageName)) {
                return true;
            }
        }
        return false;
    }

    private static void hookRawCallback(ClassLoader classLoader, String className) {
        try {
            XposedHelpers.findAndHookMethod(
                    className,
                    classLoader,
                    "onFrame",
                    byte[].class,
                    int.class,
                    new XC_MethodHook() {
                        @Override
                        protected void afterHookedMethod(MethodHookParam param) {
                            Object frameObj = param.args[0];
                            Object lengthObj = param.args[1];
                            if (!(frameObj instanceof byte[]) || !(lengthObj instanceof Integer)) {
                                return;
                            }
                            maybeForward(className, (byte[]) frameObj, (Integer) lengthObj, false);
                        }
                    });
            log("hooked " + className + ".onFrame(byte[],int)");
        } catch (Throwable t) {
            log("hook pending/failed for " + className + ": " + t);
        }
    }

    private static void hookTempCallback(ClassLoader classLoader, String className) {
        try {
            XposedHelpers.findAndHookMethod(
                    className,
                    classLoader,
                    "onFrame",
                    byte[].class,
                    new XC_MethodHook() {
                        @Override
                        protected void afterHookedMethod(MethodHookParam param) {
                            Object frameObj = param.args[0];
                            if (!(frameObj instanceof byte[])) {
                                return;
                            }
                            byte[] frame = (byte[]) frameObj;
                            maybeForward(className, frame, frame.length, true);
                        }
                    });
            log("hooked " + className + ".onFrame(byte[])");
        } catch (Throwable t) {
            log("hook pending/failed for " + className + ": " + t);
        }
    }

    private static void maybeForward(String className, byte[] frame, int length, boolean fusionTempLayout) {
        long nowMs = System.currentTimeMillis();
        if (fusionTempLayout && nowMs - LAST_RAW_FRAME_AT_MS.get() < RAW_PREFERRED_WINDOW_MS) {
            return;
        }
        int every = Math.max(1, getIntProperty("debug.yegmina.thermal_every", 1));
        int count = FRAME_COUNTER.incrementAndGet();
        if (count % every != 0) {
            return;
        }

        byte[] plane = fusionTempLayout
                ? extractFusionTempPlane(frame, length)
                : extractRawPacketTempPlane(frame, length);
        if (plane == null) {
            if (count <= 10 || count % 100 == 0) {
                log("skip frame length=" + length + " class=" + className);
            }
            return;
        }
        if (!fusionTempLayout) {
            LAST_RAW_FRAME_AT_MS.set(nowMs);
        }

        String host = getStringProperty("debug.yegmina.thermal_host", "255.255.255.255");
        int port = getIntProperty("debug.yegmina.thermal_port", 25000);
        SENDER.execute(() -> sendPlane(host, port, count, plane));

        if (!fusionTempLayout) {
            maybeForwardRawExtras(host, port, count, className, frame, length);
        }
    }

    private static void maybeForwardRawExtras(
            String host,
            int port,
            int frameId,
            String className,
            byte[] frame,
            int length) {
        int irEvery = getIntProperty("debug.yegmina.thermal_ir_every", 0);
        if (irEvery > 0 && frameId % irEvery == 0) {
            byte[] irPlane = extractIrPlane(frame, length);
            if (irPlane != null) {
                SENDER.execute(() -> sendPayloadV2(
                        host,
                        port,
                        frameId,
                        "ir_u16le",
                        "u16le",
                        WIDTH,
                        HEIGHT,
                        className,
                        irPlane));
            }
        }

        int packetEvery = getIntProperty("debug.yegmina.thermal_packet_every", 0);
        if (packetEvery > 0 && frameId % packetEvery == 0) {
            byte[] packet = extractRawPacket(frame, length);
            if (packet != null) {
                SENDER.execute(() -> sendPayloadV2(
                        host,
                        port,
                        frameId,
                        "thermovue_raw_packet",
                        "thermovue-ir-info-temp-visible",
                        0,
                        0,
                        className,
                        packet));
            }
        }
    }

    private static byte[] extractRawPacketTempPlane(byte[] frame, int length) {
        if (length == PLANE_BYTES && frame.length >= PLANE_BYTES) {
            byte[] out = new byte[PLANE_BYTES];
            System.arraycopy(frame, 0, out, 0, PLANE_BYTES);
            return out;
        }
        if (length >= TEMP_OFFSET + PLANE_BYTES && frame.length >= TEMP_OFFSET + PLANE_BYTES) {
            byte[] out = new byte[PLANE_BYTES];
            System.arraycopy(frame, TEMP_OFFSET, out, 0, PLANE_BYTES);
            return out;
        }
        return null;
    }

    private static byte[] extractIrPlane(byte[] frame, int length) {
        if (length >= PLANE_BYTES && frame.length >= PLANE_BYTES) {
            byte[] out = new byte[PLANE_BYTES];
            System.arraycopy(frame, 0, out, 0, PLANE_BYTES);
            return out;
        }
        return null;
    }

    private static byte[] extractRawPacket(byte[] frame, int length) {
        if (length < TEMP_OFFSET + PLANE_BYTES || frame.length < TEMP_OFFSET + PLANE_BYTES) {
            return null;
        }
        int packetBytes = Math.min(Math.min(length, frame.length), RAW_PACKET_BYTES);
        byte[] out = new byte[packetBytes];
        System.arraycopy(frame, 0, out, 0, packetBytes);
        return out;
    }

    private static byte[] extractFusionTempPlane(byte[] frame, int length) {
        if (length >= FUSION_RGBA_BYTES + PLANE_BYTES &&
                frame.length >= FUSION_RGBA_BYTES + PLANE_BYTES) {
            byte[] out = new byte[PLANE_BYTES];
            System.arraycopy(frame, FUSION_RGBA_BYTES, out, 0, PLANE_BYTES);
            return out;
        }
        return extractRawPacketTempPlane(frame, length);
    }

    private static void sendPlane(String host, int port, int frameId, byte[] plane) {
        try (DatagramSocket socket = new DatagramSocket()) {
            socket.setBroadcast(true);
            InetAddress address = InetAddress.getByName(host);
            int chunks = (plane.length + UDP_CHUNK_BYTES - 1) / UDP_CHUNK_BYTES;
            for (int chunk = 0; chunk < chunks; chunk++) {
                int offset = chunk * UDP_CHUNK_BYTES;
                int length = Math.min(UDP_CHUNK_BYTES, plane.length - offset);
                String header = String.format(
                        Locale.US,
                        "%sframe=%d chunk=%d chunks=%d offset=%d total=%d\n",
                        MAGIC,
                        frameId,
                        chunk,
                        chunks,
                        offset,
                        plane.length);
                byte[] headerBytes = header.getBytes(StandardCharsets.US_ASCII);
                byte[] packet = new byte[headerBytes.length + length];
                System.arraycopy(headerBytes, 0, packet, 0, headerBytes.length);
                System.arraycopy(plane, offset, packet, headerBytes.length, length);
                socket.send(new DatagramPacket(packet, packet.length, address, port));
            }
            if (frameId <= 3 || frameId % 100 == 0) {
                log("forwarded frame=" + frameId + " host=" + host + " port=" + port);
            }
        } catch (Throwable t) {
            log("udp send failed: " + t);
        }
    }

    private static void sendPayloadV2(
            String host,
            int port,
            int frameId,
            String kind,
            String format,
            int width,
            int height,
            String source,
            byte[] payload) {
        try (DatagramSocket socket = new DatagramSocket()) {
            socket.setBroadcast(true);
            InetAddress address = InetAddress.getByName(host);
            int chunks = (payload.length + UDP_CHUNK_BYTES - 1) / UDP_CHUNK_BYTES;
            for (int chunk = 0; chunk < chunks; chunk++) {
                int offset = chunk * UDP_CHUNK_BYTES;
                int length = Math.min(UDP_CHUNK_BYTES, payload.length - offset);
                String header = String.format(
                        Locale.US,
                        "%sframe=%d kind=%s chunk=%d chunks=%d offset=%d total=%d width=%d height=%d format=%s source=%s\n",
                        MAGIC_V2,
                        frameId,
                        kind,
                        chunk,
                        chunks,
                        offset,
                        payload.length,
                        width,
                        height,
                        format,
                        sanitizeHeaderValue(source));
                byte[] headerBytes = header.getBytes(StandardCharsets.US_ASCII);
                byte[] packet = new byte[headerBytes.length + length];
                System.arraycopy(headerBytes, 0, packet, 0, headerBytes.length);
                System.arraycopy(payload, offset, packet, headerBytes.length, length);
                socket.send(new DatagramPacket(packet, packet.length, address, port));
            }
            log("forwarded v2 frame=" + frameId +
                    " kind=" + kind +
                    " bytes=" + payload.length +
                    " host=" + host +
                    " port=" + port);
        } catch (Throwable t) {
            log("udp v2 send failed: " + t);
        }
    }

    private static String sanitizeHeaderValue(String value) {
        if (value == null || value.length() == 0) {
            return "unknown";
        }
        StringBuilder out = new StringBuilder(value.length());
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            if ((ch >= 'a' && ch <= 'z') ||
                    (ch >= 'A' && ch <= 'Z') ||
                    (ch >= '0' && ch <= '9') ||
                    ch == '.' ||
                    ch == '_' ||
                    ch == '-' ||
                    ch == '$') {
                out.append(ch);
            } else {
                out.append('_');
            }
        }
        return out.toString();
    }

    private static String getStringProperty(String key, String fallback) {
        try {
            Class<?> systemProperties = Class.forName("android.os.SystemProperties");
            Object value = systemProperties
                    .getMethod("get", String.class, String.class)
                    .invoke(null, key, fallback);
            return String.valueOf(value);
        } catch (Throwable ignored) {
            return fallback;
        }
    }

    private static int getIntProperty(String key, int fallback) {
        try {
            return Integer.parseInt(getStringProperty(key, String.valueOf(fallback)));
        } catch (Throwable ignored) {
            return fallback;
        }
    }

    private static void log(String message) {
        XposedBridge.log(TAG + ": " + message);
    }
}
