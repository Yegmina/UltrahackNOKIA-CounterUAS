package com.yegmina.thermallivedebug;

import android.Manifest;
import android.app.Activity;
import android.app.Application;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.ContextWrapper;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.hardware.usb.UsbConstants;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbEndpoint;
import android.hardware.usb.UsbInterface;
import android.hardware.usb.UsbManager;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Looper;
import android.os.Process;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.FileWriter;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.lang.reflect.Proxy;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.ServerSocket;
import java.net.Socket;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.Enumeration;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

import dalvik.system.DexClassLoader;
import dalvik.system.DexFile;

public class MainActivity extends Activity {
    private static final String TAG = "ThermalLiveDebug";
    private static final String BUILD_MARKER =
            "thermal-live-debug 2026-06-09 usb-attach-endpoint-capture";
    private static final String THERMOVUE_PACKAGE = "com.energy.tc2c";
    private static final String ACTION_USB_PERMISSION =
            "com.yegmina.thermallivedebug.USB_PERMISSION";
    private static final int THERMAL_VENDOR_ID = 0x3474;
    private static final int THERMAL_PRODUCT_ID = 0x4321;
    private static final int ALT_THERMAL_VENDOR_ID = 0x0ecb;
    private static final int ALT_THERMAL_PRODUCT_ID = 0x20f6;
    private static final int THERMAL_WIDTH = 256;
    private static final int THERMAL_HEIGHT = 192;
    private static final int THERMAL_U16_BYTES = THERMAL_WIDTH * THERMAL_HEIGHT * 2;
    private static final int THERMAL_PACKET_TEMP_OFFSET = THERMAL_U16_BYTES + 1024;
    private static final int HTTP_PORT = 8088;
    private static final int REQUEST_PERMISSIONS = 41;
    private static final int REQUEST_MEDIA_PROJECTION = 42;
    private static final String TINY2C_USB_MODE_PATH =
            "/sys/devices/platform/yft_tiny2c_usb/tiny2c_usb_mode";
    private static final String TINY2C_EXTCON_MODE_PATH =
            "/sys/class/yft_extcon/tiny2c_mode";

    private ThermalPreviewView preview;
    private TextView statusText;
    private TextView logText;
    private ScrollView logScroll;
    private final StringBuilder logBuffer = new StringBuilder();
    private File logFile;
    private volatile boolean running;
    private volatile Object liveProxy;
    private volatile Object liveUsbMonitorManager;
    private volatile Object liveDeviceControlManager;
    private volatile boolean foregroundThermoVueTest;
    private volatile byte[] latestThermalFrame;
    private volatile String latestThermalLabel = "none";
    private volatile long latestThermalFrameAt;
    private volatile boolean httpServerRunning;
    private volatile ServerSocket httpServerSocket;
    private DexClassLoader thermoVueLoader;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        buildUi();
        File runDir = new File(getExternalFilesDir(null), "thermal_live_debug_" + stamp());
        //noinspection ResultOfMethodCallIgnored
        runDir.mkdirs();
        logFile = new File(runDir, "thermal_live_debug.log");
        append("logFile=" + logFile.getAbsolutePath());
        append("build=" + BUILD_MARKER);
        append("uid=" + Process.myUid() + " package=" + getPackageName());
        appendSelfPackageInfo();
        append("device=" + Build.MANUFACTURER + " " + Build.MODEL +
                " sdk=" + Build.VERSION.SDK_INT);
        requestAppPermissions();
        startHttpServer();
        handleUsbAttachIntent(getIntent(), "onCreate");
    }

    private void appendSelfPackageInfo() {
        try {
            PackageInfo info = getPackageManager().getPackageInfo(getPackageName(), 0);
            append("selfPackage versionCode=" + getLongVersionCode(info) +
                    " firstInstall=" + formatTime(info.firstInstallTime) +
                    " lastUpdate=" + formatTime(info.lastUpdateTime));
        } catch (Throwable t) {
            append("selfPackage info FAIL " + formatThrowable(t));
        }
    }

    private String formatTime(long millis) {
        return new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(new Date(millis));
    }

    private void buildUi() {
        preview = new ThermalPreviewView(this);
        statusText = new TextView(this);
        statusText.setTextSize(13);
        statusText.setTextColor(Color.WHITE);
        statusText.setGravity(Gravity.CENTER_VERTICAL);
        statusText.setPadding(dp(10), dp(8), dp(10), dp(8));
        statusText.setBackgroundColor(Color.rgb(18, 24, 28));
        statusText.setText("idle");

        logText = new TextView(this);
        logText.setTextSize(11);
        logText.setTextColor(Color.rgb(220, 220, 220));
        logText.setBackgroundColor(Color.rgb(20, 20, 20));
        logText.setPadding(dp(8), dp(8), dp(8), dp(8));
        logScroll = new ScrollView(this);
        logScroll.addView(logText);

        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.HORIZONTAL);
        buttons.setPadding(dp(6), dp(6), dp(6), dp(6));
        buttons.addView(button("Self Test", view -> showSyntheticFrame()));
        buttons.addView(button("Scan", view -> scanAll()));
        buttons.addView(button("USB Probe", view -> startThread(this::probeThermalUsbEndpoints)));
        buttons.addView(button("Dump Classes", view -> startThread(this::dumpClassesOnly)));
        buttons.addView(button("Request USB", view -> requestThermalUsb()));
        buttons.addView(button("Power Try", view -> startThread(this::tryPowerThermal)));
        buttons.addView(button("Launch TVue", view -> launchThermoVue()));
        buttons.addView(button("Start SDK", view -> startSdkLive()));
        buttons.addView(button("Native Auto", view -> startNativeAutoTest()));
        buttons.addView(button("Engine Probe", view -> startEngineProbe()));
        buttons.addView(button("TVue FG Test", view -> startThermoVueForegroundTest()));
        buttons.addView(button("Cap TVue", view -> requestThermoVueScreenCapture()));
        buttons.addView(button("Load Cap", view -> showLatestScreenCapture()));
        buttons.addView(button("Stop Cap", view -> stopThermoVueScreenCapture()));
        buttons.addView(button("Stop", view -> stopSdkLive()));
        buttons.addView(button("HTTP", view -> startHttpServer()));
        buttons.addView(button("Share Log", view -> shareLog()));

        HorizontalScrollView buttonScroll = new HorizontalScrollView(this);
        buttonScroll.addView(buttons);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.BLACK);
        root.setPadding(0, dp(72), 0, 0);
        root.addView(buttonScroll, new LinearLayout.LayoutParams(-1, -2));
        root.addView(statusText, new LinearLayout.LayoutParams(-1, -2));
        root.addView(preview, new LinearLayout.LayoutParams(-1, 0, 1.0f));
        root.addView(logScroll, new LinearLayout.LayoutParams(-1, dp(230)));
        setContentView(root);
    }

    private Button button(String label, View.OnClickListener listener) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setMinWidth(dp(96));
        button.setOnClickListener(listener);
        return button;
    }

    @Override
    protected void onDestroy() {
        stopHttpServer();
        super.onDestroy();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleUsbAttachIntent(intent, "onNewIntent");
    }

    private void handleUsbAttachIntent(Intent intent, String source) {
        if (intent == null) {
            return;
        }
        String action = intent.getAction();
        if (!UsbManager.ACTION_USB_DEVICE_ATTACHED.equals(action)) {
            append(source + " intent action=" + action);
            return;
        }
        UsbDevice device = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
        append(source + " USB_DEVICE_ATTACHED " + describeUsbDevice(device) +
                " thermal=" + (device != null && isThermalDevice(device)));
        if (device != null && isThermalDevice(device)) {
            startThread(this::probeThermalUsbEndpoints);
        }
    }

    private void requestAppPermissions() {
        List<String> missing = new ArrayList<>();
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.CAMERA);
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.RECORD_AUDIO);
        }
        if (Build.VERSION.SDK_INT >= 33 &&
                checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
                        PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (!missing.isEmpty()) {
            append("requesting runtime permissions " + missing);
            requestPermissions(missing.toArray(new String[0]), REQUEST_PERMISSIONS);
        } else {
            append("runtime permissions already granted");
        }
    }

    @Override
    public void onRequestPermissionsResult(
            int requestCode,
            String[] permissions,
            int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != REQUEST_PERMISSIONS) {
            return;
        }
        append("runtime permission result camera=" +
                hasPermission(Manifest.permission.CAMERA) +
                " audio=" + hasPermission(Manifest.permission.RECORD_AUDIO) +
                " notifications=" + (Build.VERSION.SDK_INT < 33 ||
                hasPermission(Manifest.permission.POST_NOTIFICATIONS)));
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQUEST_MEDIA_PROJECTION) {
            return;
        }
        if (resultCode != RESULT_OK || data == null) {
            append("screen capture permission denied resultCode=" + resultCode);
            return;
        }
        append("screen capture permission granted; starting foreground service");
        Intent intent = new Intent(this, ThermalScreenCaptureService.class);
        intent.setAction(ThermalScreenCaptureService.ACTION_START);
        intent.putExtra(ThermalScreenCaptureService.EXTRA_RESULT_CODE, resultCode);
        intent.putExtra(ThermalScreenCaptureService.EXTRA_RESULT_DATA, data);
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(intent);
        } else {
            startService(intent);
        }
        launchThermoVue();
    }

    private boolean hasPermission(String permission) {
        return checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED;
    }

    private void showSyntheticFrame() {
        byte[] frame = new byte[THERMAL_U16_BYTES];
        for (int y = 0; y < THERMAL_HEIGHT; y++) {
            for (int x = 0; x < THERMAL_WIDTH; x++) {
                int dx = x - THERMAL_WIDTH / 2;
                int dy = y - THERMAL_HEIGHT / 2;
                int value = 11500 + x * 8 + y * 5;
                if (dx * dx + dy * dy < 32 * 32) {
                    value += 4500;
                }
                int index = (y * THERMAL_WIDTH + x) * 2;
                frame[index] = (byte) (value & 0xff);
                frame[index + 1] = (byte) ((value >> 8) & 0xff);
            }
        }
        renderThermal(frame, "synthetic");
        append("synthetic preview frame rendered");
    }

    private void scanAll() {
        append("===== scan =====");
        inspectCameras();
        inspectUsb();
        inspectThermoVuePackage();
    }

    private void inspectCameras() {
        try {
            CameraManager manager = (CameraManager) getSystemService(CAMERA_SERVICE);
            for (String id : manager.getCameraIdList()) {
                CameraCharacteristics characteristics = manager.getCameraCharacteristics(id);
                Integer facing = characteristics.get(CameraCharacteristics.LENS_FACING);
                append("camera id=" + id + " facing=" + facing);
            }
        } catch (Throwable t) {
            append("camera scan FAIL " + formatThrowable(t));
        }
    }

    private UsbDevice inspectUsb() {
        UsbDevice thermal = null;
        try {
            UsbManager manager = (UsbManager) getSystemService(USB_SERVICE);
            Map<String, UsbDevice> devices = manager.getDeviceList();
            append("usbDeviceCount=" + devices.size());
            for (UsbDevice device : devices.values()) {
                boolean isThermal = isThermalDevice(device);
                append("usb " + describeUsbDevice(device) +
                        " hasPermission=" + manager.hasPermission(device) +
                        " thermal=" + isThermal);
                for (int i = 0; i < device.getInterfaceCount(); i++) {
                    UsbInterface iface = device.getInterface(i);
                    append("usbInterface index=" + i +
                            " class=" + iface.getInterfaceClass() +
                            " subclass=" + iface.getInterfaceSubclass() +
                            " protocol=" + iface.getInterfaceProtocol() +
                            " endpoints=" + iface.getEndpointCount());
                    for (int endpointIndex = 0;
                         endpointIndex < iface.getEndpointCount();
                         endpointIndex++) {
                        append("usbEndpoint iface=" + i +
                                " endpoint=" + endpointIndex +
                                " " + describeUsbEndpoint(iface.getEndpoint(endpointIndex)));
                    }
                }
                if (isThermal) {
                    thermal = device;
                }
            }
        } catch (Throwable t) {
            append("usb scan FAIL " + formatThrowable(t));
        }
        return thermal;
    }

    private boolean probeThermalUsbEndpoints() {
        append("===== direct USB endpoint probe =====");
        UsbDevice thermal = inspectUsb();
        if (thermal == null) {
            append("USB probe: no candidate thermal USB device visible");
            setStatus("USB probe no device");
            return false;
        }
        UsbManager manager = (UsbManager) getSystemService(USB_SERVICE);
        try {
            attemptHiddenUsbGrant(manager, thermal);
        } catch (Throwable t) {
            append("USB probe hidden grant attempt FAIL " + formatThrowable(t));
        }
        if (!manager.hasPermission(thermal)) {
            append("USB probe requesting permission for " + describeUsbDevice(thermal));
            requestUsbPermissionAndWait(manager, thermal);
        }
        append("USB probe hasPermission=" + manager.hasPermission(thermal));
        if (!manager.hasPermission(thermal)) {
            append("USB probe cannot continue without Android USB permission");
            setStatus("USB probe no permission");
            return false;
        }

        boolean sawBytes = false;
        UsbDeviceConnection connection = null;
        try {
            connection = manager.openDevice(thermal);
            if (connection == null) {
                append("USB probe openDevice returned null");
                setStatus("USB probe open failed");
                return false;
            }
            byte[] rawDescriptors = connection.getRawDescriptors();
            append("USB probe rawDescriptors " + describeBytes(rawDescriptors) +
                    " head=" + hexPrefix(rawDescriptors, 96));

            for (int i = 0; i < thermal.getInterfaceCount(); i++) {
                UsbInterface iface = thermal.getInterface(i);
                append("USB probe interface=" + i +
                        " class=" + iface.getInterfaceClass() +
                        " subclass=" + iface.getInterfaceSubclass() +
                        " protocol=" + iface.getInterfaceProtocol() +
                        " endpoints=" + iface.getEndpointCount());
                boolean claimed = connection.claimInterface(iface, false);
                if (!claimed) {
                    append("USB probe claimInterface(false) failed; trying force=true interface=" + i);
                    claimed = connection.claimInterface(iface, true);
                }
                append("USB probe claim interface=" + i + " claimed=" + claimed);
                if (!claimed) {
                    continue;
                }
                try {
                    for (int endpointIndex = 0;
                         endpointIndex < iface.getEndpointCount();
                         endpointIndex++) {
                        UsbEndpoint endpoint = iface.getEndpoint(endpointIndex);
                        append("USB probe endpoint iface=" + i +
                                " endpoint=" + endpointIndex +
                                " " + describeUsbEndpoint(endpoint));
                        if (endpoint.getDirection() != UsbConstants.USB_DIR_IN) {
                            append("USB probe skip OUT endpoint iface=" + i +
                                    " endpoint=" + endpointIndex);
                            continue;
                        }
                        int type = endpoint.getType();
                        if (type == UsbConstants.USB_ENDPOINT_XFER_BULK ||
                                type == UsbConstants.USB_ENDPOINT_XFER_INT) {
                            sawBytes = probeReadableEndpoint(connection, endpoint, i, endpointIndex) ||
                                    sawBytes;
                        } else if (type == UsbConstants.USB_ENDPOINT_XFER_ISOC) {
                            append("USB probe skip ISO IN endpoint iface=" + i +
                                    " endpoint=" + endpointIndex +
                                    " because Android Java USB host has no isochronous read API");
                        } else {
                            append("USB probe skip unsupported IN endpoint type=" +
                                    endpointTypeName(type));
                        }
                    }
                } finally {
                    try {
                        connection.releaseInterface(iface);
                    } catch (Throwable t) {
                        append("USB probe releaseInterface FAIL interface=" + i +
                                " " + formatThrowable(t));
                    }
                }
            }
        } catch (Throwable t) {
            append("USB probe FAIL " + formatThrowable(t));
        } finally {
            if (connection != null) {
                try {
                    connection.close();
                } catch (Throwable ignored) {
                    // Nothing useful to do after a close failure in a debug probe.
                }
            }
        }
        append("USB probe finished sawBytes=" + sawBytes);
        setStatus(sawBytes ? "USB probe saw bytes" : "USB probe no bytes");
        return sawBytes;
    }

    private boolean probeReadableEndpoint(
            UsbDeviceConnection connection,
            UsbEndpoint endpoint,
            int interfaceIndex,
            int endpointIndex) {
        int maxPacket = Math.max(64, endpoint.getMaxPacketSize());
        int bufferLength = Math.max(64, Math.min(64 * 1024, maxPacket * 128));
        byte[] buffer = new byte[bufferLength];
        ByteArrayOutputStream captured = new ByteArrayOutputStream();
        boolean sawBytes = false;
        for (int attempt = 1; attempt <= 8; attempt++) {
            int read;
            try {
                read = connection.bulkTransfer(endpoint, buffer, buffer.length, 180);
            } catch (Throwable t) {
                append("USB probe read FAIL iface=" + interfaceIndex +
                        " endpoint=" + endpointIndex +
                        " attempt=" + attempt +
                        " " + formatThrowable(t));
                break;
            }
            append("USB probe read iface=" + interfaceIndex +
                    " endpoint=" + endpointIndex +
                    " attempt=" + attempt +
                    " read=" + read +
                    " buffer=" + buffer.length);
            if (read > 0) {
                sawBytes = true;
                captured.write(buffer, 0, read);
                append("USB probe data iface=" + interfaceIndex +
                        " endpoint=" + endpointIndex +
                        " read=" + read +
                        " checksum=" + checksumHex(buffer, read) +
                        " head=" + hexPrefix(buffer, Math.min(read, 96)));
                if (read >= THERMAL_U16_BYTES) {
                    byte[] frame = new byte[THERMAL_U16_BYTES];
                    System.arraycopy(buffer, 0, frame, 0, THERMAL_U16_BYTES);
                    renderThermal(frame, "usb endpoint sample");
                }
            }
            sleepMs(80);
        }
        if (sawBytes) {
            byte[] data = captured.toByteArray();
            saveUsbProbeBytes(interfaceIndex, endpointIndex, data);
            byte[] frame = chooseUsbProbeFrame(data);
            if (frame != null) {
                renderThermal(frame, "usb endpoint collected");
            }
        }
        return sawBytes;
    }

    private void saveUsbProbeBytes(int interfaceIndex, int endpointIndex, byte[] data) {
        if (data == null || data.length == 0) {
            return;
        }
        try {
            File dir = new File(logFile.getParentFile(), "usb_probe");
            //noinspection ResultOfMethodCallIgnored
            dir.mkdirs();
            String name = "iface" + interfaceIndex + "_ep" + endpointIndex + "_" +
                    System.currentTimeMillis() + ".bin";
            File file = new File(dir, name);
            try (FileOutputStream output = new FileOutputStream(file)) {
                output.write(data);
            }
            append("USB probe saved bytes path=" + file.getAbsolutePath() +
                    " bytes=" + data.length +
                    " checksum=" + checksumHex(data, data.length));
        } catch (Throwable t) {
            append("USB probe save bytes FAIL " + formatThrowable(t));
        }
    }

    private byte[] chooseUsbProbeFrame(byte[] data) {
        if (data == null || data.length < THERMAL_U16_BYTES) {
            return null;
        }
        int[] starts = new int[]{
                0,
                THERMAL_PACKET_TEMP_OFFSET,
                Math.max(0, data.length - THERMAL_U16_BYTES)
        };
        for (int start : starts) {
            byte[] frame = copyThermalWindowIfUseful(data, start);
            if (frame != null) {
                append("USB probe render candidate start=" + start);
                return frame;
            }
        }
        int maxStart = data.length - THERMAL_U16_BYTES;
        int step = Math.max(512, THERMAL_U16_BYTES / 16);
        for (int start = 0; start <= maxStart; start += step) {
            byte[] frame = copyThermalWindowIfUseful(data, start);
            if (frame != null) {
                append("USB probe render scanned start=" + start);
                return frame;
            }
        }
        append("USB probe no renderable collected frame bytes=" + data.length);
        return null;
    }

    private byte[] copyThermalWindowIfUseful(byte[] data, int start) {
        if (start < 0 || data == null || start + THERMAL_U16_BYTES > data.length) {
            return null;
        }
        int min = Integer.MAX_VALUE;
        int max = Integer.MIN_VALUE;
        int stride = 97;
        int count = THERMAL_WIDTH * THERMAL_HEIGHT;
        for (int i = 0; i < count; i += stride) {
            int byteIndex = start + i * 2;
            int value = (data[byteIndex] & 0xff) | ((data[byteIndex + 1] & 0xff) << 8);
            min = Math.min(min, value);
            max = Math.max(max, value);
        }
        if (max - min < 8) {
            return null;
        }
        byte[] frame = new byte[THERMAL_U16_BYTES];
        System.arraycopy(data, start, frame, 0, frame.length);
        return frame;
    }

    private void inspectThermoVuePackage() {
        try {
            PackageInfo packageInfo = getPackageManager().getPackageInfo(THERMOVUE_PACKAGE, 0);
            ApplicationInfo appInfo = packageInfo.applicationInfo;
            append("ThermoVue installed sourceDir=" + appInfo.sourceDir);
            append("ThermoVue nativeLibraryDir=" + appInfo.nativeLibraryDir);
            append("ThermoVue versionName=" + packageInfo.versionName +
                    " versionCode=" + getLongVersionCode(packageInfo));
        } catch (Throwable t) {
            append("ThermoVue package scan FAIL " + formatThrowable(t));
        }
    }

    private void requestThermalUsb() {
        startThread(() -> {
            append("===== request thermal USB =====");
            UsbDevice thermal = inspectUsb();
            if (thermal == null) {
                append("thermal USB not visible; try Launch TVue or Power Try first");
                return;
            }
            UsbManager manager = (UsbManager) getSystemService(USB_SERVICE);
            append("thermal before request hasPermission=" + manager.hasPermission(thermal));
            attemptHiddenUsbGrant(manager, thermal);
            boolean granted = requestUsbPermissionAndWait(manager, thermal);
            append("thermal after request granted=" + granted +
                    " hasPermission=" + manager.hasPermission(thermal));
        });
    }

    private void tryPowerThermal() {
        append("===== power attempts =====");
        writeSysfs(TINY2C_USB_MODE_PATH, "1\n");
        writeSysfs(TINY2C_EXTCON_MODE_PATH, "1\n");
        try {
            DexClassLoader loader = getThermoVueClassLoader();
            Class<?> gpio = Class.forName("com.energy.ac020library.utils.GPIOUtils", true, loader);
            for (Method method : gpio.getDeclaredMethods()) {
                if (method.getParameterTypes().length == 0 &&
                        method.getName().toLowerCase(Locale.US).contains("power")) {
                    try {
                        method.setAccessible(true);
                        Object result = method.invoke(null);
                        append("GPIOUtils " + describeMethod(method) +
                                " OK result=" + describeObject(result));
                    } catch (Throwable t) {
                        append("GPIOUtils " + describeMethod(method) +
                                " FAIL " + formatThrowable(t));
                    }
                }
            }
        } catch (Throwable t) {
            append("vendor power FAIL " + formatThrowable(t));
        }
        inspectUsb();
    }

    private void writeSysfs(String path, String value) {
        try (FileWriter writer = new FileWriter(path)) {
            writer.write(value);
            append("sysfsWrite OK path=" + path + " value=" + value.trim());
        } catch (Throwable t) {
            append("sysfsWrite FAIL path=" + path + " value=" + value.trim() +
                    " " + formatThrowable(t));
        }
    }

    private void launchThermoVue() {
        append("launch ThermoVue package=" + THERMOVUE_PACKAGE);
        try {
            Intent intent = getPackageManager().getLaunchIntentForPackage(THERMOVUE_PACKAGE);
            if (intent == null) {
                append("ThermoVue launch intent=null");
                return;
            }
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(intent);
            append("ThermoVue launch requested; return here and press Start SDK");
        } catch (Throwable t) {
            append("ThermoVue launch FAIL " + formatThrowable(t));
        }
    }

    private void requestThermoVueScreenCapture() {
        append("===== ThermoVue screen capture fallback =====");
        append("This captures ThermoVue's foreground screen, not raw sensor bytes.");
        try {
            MediaProjectionManager manager =
                    (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
            startActivityForResult(
                    manager.createScreenCaptureIntent(),
                    REQUEST_MEDIA_PROJECTION);
        } catch (Throwable t) {
            append("screen capture request FAIL " + formatThrowable(t));
        }
    }

    private void stopThermoVueScreenCapture() {
        append("stop ThermoVue screen capture service");
        Intent intent = new Intent(this, ThermalScreenCaptureService.class);
        intent.setAction(ThermalScreenCaptureService.ACTION_STOP);
        startService(intent);
    }

    private void showLatestScreenCapture() {
        File latest = new File(
                new File(getExternalFilesDir(null), "screen_capture"),
                "latest_thermovue_screen.jpg");
        if (!latest.exists()) {
            append("latest screen capture not found: " + latest.getAbsolutePath());
            setStatus("no screen capture yet");
            return;
        }
        Bitmap bitmap = BitmapFactory.decodeFile(latest.getAbsolutePath());
        if (bitmap == null) {
            append("latest screen capture decode failed: " + latest.getAbsolutePath());
            setStatus("capture decode failed");
            return;
        }
        runOnUiThread(() -> {
            preview.setBitmap(bitmap);
            statusText.setText("loaded ThermoVue screen capture " +
                    bitmap.getWidth() + "x" + bitmap.getHeight());
        });
        append("loaded screen capture " + latest.getAbsolutePath() +
                " bytes=" + latest.length());
    }

    private void startSdkLive() {
        if (running) {
            append("SDK live already running");
            return;
        }
        running = true;
        setStatus("starting SDK live");
        startThread(this::runSdkLive);
    }

    private void startThermoVueForegroundTest() {
        if (running) {
            append("foreground ThermoVue test already running");
            return;
        }
        foregroundThermoVueTest = true;
        running = true;
        setStatus("starting ThermoVue foreground test");
        append("===== ThermoVue foreground hypothesis test =====");
        append("This starts SDK polling, then launches ThermoVue so ThermoVue remains foreground.");
        startThread(this::runSdkLive);
    }

    private void startNativeAutoTest() {
        if (running) {
            append("native autotest already running");
            return;
        }
        foregroundThermoVueTest = false;
        running = true;
        setStatus("native autotest running");
        startThread(this::runNativeAutoTest);
    }

    private void startEngineProbe() {
        if (running) {
            append("engine probe already running");
            return;
        }
        foregroundThermoVueTest = false;
        running = true;
        setStatus("engine probe running");
        startThread(this::runStandaloneEngineProbe);
    }

    private void stopSdkLive() {
        running = false;
        foregroundThermoVueTest = false;
        setStatus("stopping and closing USB monitor");
        Object proxy = liveProxy;
        if (proxy != null) {
            tryInvoke(proxy, "stopPreview");
            tryInvoke(proxy, "releaseSource");
        }
        Object deviceControl = liveDeviceControlManager;
        if (deviceControl != null) {
            tryInvoke(deviceControl, "release");
        }
        Object monitor = liveUsbMonitorManager;
        if (monitor != null) {
            tryInvoke(monitor, "unregisterMonitor");
            tryInvoke(monitor, "destroyMonitor");
        }
    }

    private void runSdkLive() {
        append("===== SDK live start =====");
        Object proxy = null;
        Object usbMonitorManager = null;
        Object deviceControlManager = null;
        long firstFrameAt = 0;
        int renderedFrames = 0;
        boolean explicitInitTried = false;
        boolean explicitStartTried = false;
        boolean monitorClosedToStopDialogLoop = false;
        long sdkStartedAt = System.currentTimeMillis();
        boolean shouldLaunchThermoVueForeground = foregroundThermoVueTest;
        long noFrameTimeoutMs = shouldLaunchThermoVueForeground ? 20000 : 8000;
        try {
            DexClassLoader loader = getThermoVueClassLoader();
            initializeMmkv(loader);
            initializeBlankjUtils(loader);
            initializeRxBaseApplication(loader);
            dumpImportantClasses(loader, false);

            UsbDevice thermal = inspectUsb();
            if (thermal != null) {
                UsbManager usb = (UsbManager) getSystemService(USB_SERVICE);
                append("thermal visible before SDK hasPermission=" + usb.hasPermission(thermal));
            }

            Class<?> proxyClass = Class.forName(
                    "com.energy.dualmodule.sdk.Tiny2CDualFusionProxy", true, loader);
            proxy = proxyClass.getMethod("getInstance").invoke(null);
            liveProxy = proxy;
            Method init = proxyClass.getMethod(
                    "init",
                    Context.class,
                    int.class,
                    int.class,
                    float.class,
                    int.class,
                    String.class,
                    int.class,
                    int.class,
                    int.class);
            init.invoke(proxy, this, 256, 386, 1.0f, 25, "0", 1440, 1080, 25);
            append("Tiny2C init invoked");
            tryInvoke(proxy, "initData");
            tryInvokeBoolean(proxy, "setHasAPPKilled", false);
            tryInvokeBoolean(proxy, "setHasPreviewSurfaceDestroy", false);
            tryInvokeBoolean(proxy, "setPausePreviewEnable", false);
            tryInvoke(proxy, "resetIsFirstFrame");
            Object frameCallback = createIrFrameCallback(loader, "SDK");
            tryInstallFrameCallback(proxy, frameCallback, "SDK proxy");

            Class<?> usbMonitorManagerClass = Class.forName(
                    "com.energy.dualmodule.sdk.uvc.USBMonitorManager", true, loader);
            usbMonitorManager = usbMonitorManagerClass.getMethod("getInstance").invoke(null);
            liveUsbMonitorManager = usbMonitorManager;
            tryInvoke(usbMonitorManager, "init");
            tryInvoke(usbMonitorManager, "registerMonitor");
            if (shouldLaunchThermoVueForeground) {
                append("foreground test: launching ThermoVue now; return after 15-20s and share log");
                runOnUiThread(this::launchThermoVue);
            }

            Class<?> deviceControlManagerClass = Class.forName(
                    "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualDeviceControlManager",
                    true,
                    loader);
            deviceControlManager = deviceControlManagerClass.getMethod("getInstance").invoke(null);
            liveDeviceControlManager = deviceControlManager;
            tryInvoke(deviceControlManager, "init");
            tryInstallFrameCallback(deviceControlManager, frameCallback, "SDK deviceControl");
            installFrameCallbacksOnKnownSingletons(loader, frameCallback, "SDK singleton");

            Class<?> modeClass = Class.forName(
                    "com.energy.dualmodule.sdk.service.task.DualPreviewMode", true, loader);
            Object mode = enumValue(modeClass, "MODE_DUAL_FUSION");
            proxyClass.getMethod("handleStartPreview", modeClass).invoke(proxy, mode);
            append("handleStartPreview MODE_DUAL_FUSION invoked");
            setStatus("waiting for vendor worker");
            append("SDK poll loop entering");

            long lastLogAt = 0;
            while (running) {
                sleepMs(200);
                Object frameCount = tryInvokeQuiet(proxy, "getFrameCount");
                Object firstFrame = tryInvokeQuiet(proxy, "getFirstFrameFlag");
                byte[] rawTemp = (byte[]) tryInvokeQuiet(proxy, "getRawTempData");
                byte[] remapTemp = (byte[]) tryInvokeQuiet(proxy, "getRemapTempData");
                byte[] frame = chooseRenderableFrame(rawTemp, remapTemp);
                if (frame != null) {
                    if (firstFrameAt == 0) {
                        firstFrameAt = System.currentTimeMillis();
                        append("FIRST THERMAL FRAME frameCount=" + frameCount +
                                " firstFrame=" + firstFrame +
                                " rawTemp=" + describeBytes(rawTemp) +
                                " remapTemp=" + describeBytes(remapTemp));
                    }
                    renderedFrames++;
                    double fps = renderedFrames * 1000.0 /
                            Math.max(1, System.currentTimeMillis() - firstFrameAt);
                    renderThermal(frame, "sdk frame=" + frameCount + " fps=" +
                            String.format(Locale.US, "%.1f", fps));
                }
                long now = System.currentTimeMillis();
                if (now - lastLogAt > 2000) {
                    lastLogAt = now;
                    Object connected = usbMonitorManager == null
                            ? null : tryInvokeQuiet(usbMonitorManager, "isDeviceConnected");
                    Object ctrlBlock = usbMonitorManager == null
                            ? null : tryInvokeQuiet(usbMonitorManager, "getCtrlBlock");
                    append("poll frameCount=" + frameCount +
                            " firstFrame=" + firstFrame +
                            " rawTemp=" + describeBytes(rawTemp) +
                            " remapTemp=" + describeBytes(remapTemp) +
                            " connected=" + describeObject(connected) +
                            " ctrlBlock=" + describeObject(ctrlBlock));
                    boolean fallbackAction = false;
                    if (frame == null && ctrlBlock != null && !explicitInitTried) {
                        explicitInitTried = true;
                        fallbackAction = true;
                        setStatus("trying explicit engine init");
                        append("fallback: explicit initData/initHandleEngine because ctrlBlock exists but no frames");
                        tryInvoke(proxy, "initData");
                        try {
                            Class<?> ctrlBlockClass = Class.forName(
                                    "com.energy.iruvccamera.usb.USBMonitor$UsbControlBlock",
                                    false,
                                    loader);
                            Object result = invoke(
                                    proxy,
                                    "initHandleEngine",
                                    new Class[]{ctrlBlockClass, boolean.class},
                                    ctrlBlock,
                                    true);
                            append("fallback initHandleEngine OK result=" + describeObject(result));
                        } catch (Throwable t) {
                            append("fallback initHandleEngine FAIL " + formatThrowable(t));
                        }
                    } else if (frame == null && explicitInitTried && !explicitStartTried) {
                        explicitStartTried = true;
                        fallbackAction = true;
                        setStatus("trying explicit startPreview");
                        append("fallback: explicit startPreview because engine init still has no frames");
                        tryInvoke(proxy, "startPreview");
                    } else if (frame == null && explicitStartTried &&
                            ctrlBlock != null && !monitorClosedToStopDialogLoop) {
                        monitorClosedToStopDialogLoop = true;
                        fallbackAction = true;
                        setStatus("closing USB monitor to stop permission loop");
                        append("fallback: closing USB monitor after ctrlBlock/no-frames state to stop repeated permission dialogs");
                        tryInvoke(usbMonitorManager, "unregisterMonitor");
                        tryInvoke(usbMonitorManager, "destroyMonitor");
                    } else if (frame == null && !monitorClosedToStopDialogLoop &&
                            now - sdkStartedAt > noFrameTimeoutMs) {
                        monitorClosedToStopDialogLoop = true;
                        fallbackAction = true;
                        setStatus("closing USB monitor after timeout");
                        append("fallback: closing USB monitor after " + noFrameTimeoutMs +
                                "ms without frames to stop repeated permission dialogs");
                        tryInvoke(usbMonitorManager, "unregisterMonitor");
                        tryInvoke(usbMonitorManager, "destroyMonitor");
                    }
                    if (frame == null && !fallbackAction) {
                        setStatus("no thermal frames yet");
                    }
                }
            }
        } catch (Throwable t) {
            append("SDK LIVE FAIL " + formatThrowable(t));
            setStatus("SDK failed");
        } finally {
            running = false;
            foregroundThermoVueTest = false;
            liveProxy = null;
            liveUsbMonitorManager = null;
            liveDeviceControlManager = null;
            if (proxy != null) {
                tryInvoke(proxy, "stopPreview");
                tryInvoke(proxy, "releaseSource");
            }
            if (deviceControlManager != null) {
                tryInvoke(deviceControlManager, "release");
            }
            if (usbMonitorManager != null) {
                tryInvoke(usbMonitorManager, "unregisterMonitor");
                tryInvoke(usbMonitorManager, "destroyMonitor");
            }
            append("SDK live stopped");
        }
    }

    private void runNativeAutoTest() {
        append("===== native clone autotest start =====");
        append("Goal: reproduce ThermoVue startup without using ThermoVue's foreground feed.");
        boolean anyFrame = false;
        try {
            DexClassLoader loader = getThermoVueClassLoader();
            initializeMmkv(loader);
            initializeBlankjUtils(loader);
            initializeRxBaseApplication(loader);
            dumpImportantClasses(loader, false);
            dumpTargetedReverseEngineeringHints(loader);
            dumpThermoVueClassIndex(loader);
            inspectUsb();
            probeThermalUsbEndpoints();

            Class<?> modeClass = Class.forName(
                    "com.energy.dualmodule.sdk.service.task.DualPreviewMode", true, loader);
            Object[] modes = modeClass.getEnumConstants();
            if (modes == null || modes.length == 0) {
                append("AUTO FAIL no DualPreviewMode enum constants");
                return;
            }
            List<String> cameraIds = getCameraIdCandidates();
            String[] strategies = new String[]{
                    "deviceControlHandle",
                    "proxyHandle",
                    "explicitEngine",
                    "deviceControlThenExplicitStart",
                    "directEngineProbe"
            };
            int scenario = 0;
            for (String cameraId : cameraIds) {
                for (Object mode : modes) {
                    String modeName = enumName(mode);
                    if (!modeName.toLowerCase(Locale.US).contains("fusion") &&
                            !modeName.toLowerCase(Locale.US).contains("dual")) {
                        append("AUTO skip mode=" + modeName);
                        continue;
                    }
                    for (String strategy : strategies) {
                        if (!running) {
                            append("AUTO stopped by user");
                            return;
                        }
                        scenario++;
                        boolean saw = runNativeScenario(
                                loader,
                                scenario,
                                cameraId,
                                modeClass,
                                mode,
                                modeName,
                                strategy);
                        anyFrame = anyFrame || saw;
                        if (saw) {
                            append("AUTO SUCCESS strategy=" + strategy +
                                    " cameraId=" + cameraId +
                                    " mode=" + modeName);
                            setStatus("native autotest got frame");
                            return;
                        }
                    }
                }
            }
            append("AUTO RESULT no raw frames in tested native clone matrix");
            setStatus("native autotest no frames");
        } catch (Throwable t) {
            append("AUTO FATAL " + formatThrowable(t));
            setStatus("native autotest failed");
        } finally {
            running = false;
            foregroundThermoVueTest = false;
            cleanupLiveObjects();
            append("===== native clone autotest finished anyFrame=" + anyFrame + " =====");
        }
    }

    private void runStandaloneEngineProbe() {
        append("===== direct engine standalone probe start =====");
        Object proxy = null;
        Object usbMonitorManager = null;
        Object deviceControlManager = null;
        boolean sawFrame = false;
        try {
            DexClassLoader loader = getThermoVueClassLoader();
            initializeMmkv(loader);
            initializeBlankjUtils(loader);
            initializeRxBaseApplication(loader);
            dumpTargetedReverseEngineeringHints(loader);
            inspectUsb();

            Class<?> proxyClass = Class.forName(
                    "com.energy.dualmodule.sdk.Tiny2CDualFusionProxy", true, loader);
            proxy = proxyClass.getMethod("getInstance").invoke(null);
            liveProxy = proxy;
            tryInvoke(proxy, "stopPreview");
            tryInvoke(proxy, "releaseSource");
            Method init = proxyClass.getMethod(
                    "init",
                    Context.class,
                    int.class,
                    int.class,
                    float.class,
                    int.class,
                    String.class,
                    int.class,
                    int.class,
                    int.class);
            init.invoke(proxy, this, 256, 386, 1.0f, 25, "0", 1440, 1080, 25);
            append("ENGINE proxy init OK");
            tryInvoke(proxy, "initData");
            tryInvokeBoolean(proxy, "setHasAPPKilled", false);
            tryInvokeBoolean(proxy, "setHasPreviewSurfaceDestroy", false);
            tryInvokeBoolean(proxy, "setPausePreviewEnable", false);
            tryInvoke(proxy, "resetIsFirstFrame");

            Class<?> usbMonitorManagerClass = Class.forName(
                    "com.energy.dualmodule.sdk.uvc.USBMonitorManager", true, loader);
            usbMonitorManager = usbMonitorManagerClass.getMethod("getInstance").invoke(null);
            liveUsbMonitorManager = usbMonitorManager;
            tryInvoke(usbMonitorManager, "destroyMonitor");
            tryInvoke(usbMonitorManager, "init");
            tryInvoke(usbMonitorManager, "registerMonitor");

            Class<?> deviceControlManagerClass = Class.forName(
                    "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualDeviceControlManager",
                    true,
                    loader);
            deviceControlManager = deviceControlManagerClass.getMethod("getInstance").invoke(null);
            liveDeviceControlManager = deviceControlManager;
            tryInvoke(deviceControlManager, "release");
            tryInvoke(deviceControlManager, "init");

            Object callback = createIrFrameCallback(loader, "ENGINE");
            tryInstallFrameCallback(proxy, callback, "ENGINE proxy");
            tryInstallFrameCallback(deviceControlManager, callback, "ENGINE deviceControl");
            installFrameCallbacksOnKnownSingletons(loader, callback, "ENGINE singleton");

            Object connected = waitForUsbConnected(usbMonitorManager, 4000);
            Object ctrlBlock = tryInvokeQuiet(usbMonitorManager, "getCtrlBlock");
            append("ENGINE USB connected=" + describeObject(connected) +
                    " ctrlBlock=" + describeObject(ctrlBlock));

            sawFrame = runDirectEngineProbe(
                    loader,
                    proxy,
                    usbMonitorManager,
                    deviceControlManager,
                    ctrlBlock,
                    callback,
                    "ENGINE");
            if (!sawFrame) {
                tryInvoke(proxy, "startPreview");
                sawFrame = pollNativeScenario(proxy, "ENGINE proxy after direct", 6000);
            }
            setStatus(sawFrame ? "engine probe got frame" : "engine probe no frames");
        } catch (Throwable t) {
            append("ENGINE FATAL " + formatThrowable(t));
            setStatus("engine probe failed");
        } finally {
            running = false;
            if (proxy != null) {
                tryInvoke(proxy, "stopPreview");
                tryInvoke(proxy, "releaseSource");
            }
            if (deviceControlManager != null) {
                tryInvoke(deviceControlManager, "release");
            }
            if (usbMonitorManager != null) {
                tryInvoke(usbMonitorManager, "unregisterMonitor");
                tryInvoke(usbMonitorManager, "destroyMonitor");
            }
            liveProxy = null;
            liveUsbMonitorManager = null;
            liveDeviceControlManager = null;
            append("===== direct engine standalone probe finished sawFrame=" +
                    sawFrame + " =====");
        }
    }

    private boolean runNativeScenario(
            ClassLoader loader,
            int scenario,
            String cameraId,
            Class<?> modeClass,
            Object mode,
            String modeName,
            String strategy) {
        append("AUTO scenario " + scenario +
                " cameraId=" + cameraId +
                " mode=" + modeName +
                " strategy=" + strategy);
        Object proxy = null;
        Object usbMonitorManager = null;
        Object deviceControlManager = null;
        try {
            Class<?> proxyClass = Class.forName(
                    "com.energy.dualmodule.sdk.Tiny2CDualFusionProxy", true, loader);
            proxy = proxyClass.getMethod("getInstance").invoke(null);
            liveProxy = proxy;
            tryInvoke(proxy, "stopPreview");
            tryInvoke(proxy, "releaseSource");

            Method init = proxyClass.getMethod(
                    "init",
                    Context.class,
                    int.class,
                    int.class,
                    float.class,
                    int.class,
                    String.class,
                    int.class,
                    int.class,
                    int.class);
            init.invoke(proxy, this, 256, 386, 1.0f, 25, cameraId, 1440, 1080, 25);
            append("AUTO init OK cameraId=" + cameraId);
            tryInvoke(proxy, "cancelRestartTimer");
            tryInvoke(proxy, "startRestartTimer");
            tryInvoke(proxy, "initData");
            tryInvokeBoolean(proxy, "setHasAPPKilled", false);
            tryInvokeBoolean(proxy, "setHasPreviewSurfaceDestroy", false);
            tryInvokeBoolean(proxy, "setPausePreviewEnable", false);
            tryInvoke(proxy, "resetIsFirstFrame");
            Object frameCallback = createIrFrameCallback(loader, "AUTO scenario " + scenario);
            tryInstallFrameCallback(proxy, frameCallback, "AUTO proxy");

            Class<?> usbMonitorManagerClass = Class.forName(
                    "com.energy.dualmodule.sdk.uvc.USBMonitorManager", true, loader);
            usbMonitorManager = usbMonitorManagerClass.getMethod("getInstance").invoke(null);
            liveUsbMonitorManager = usbMonitorManager;
            tryInvoke(usbMonitorManager, "destroyMonitor");
            tryInvoke(usbMonitorManager, "init");
            tryInvoke(usbMonitorManager, "registerMonitor");

            Class<?> deviceControlManagerClass = Class.forName(
                    "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualDeviceControlManager",
                    true,
                    loader);
            deviceControlManager = deviceControlManagerClass.getMethod("getInstance").invoke(null);
            liveDeviceControlManager = deviceControlManager;
            tryInvoke(deviceControlManager, "release");
            tryInvoke(deviceControlManager, "init");
            tryInstallFrameCallback(deviceControlManager, frameCallback, "AUTO deviceControl");
            installFrameCallbacksOnKnownSingletons(loader, frameCallback, "AUTO singleton");

            Object connected = waitForUsbConnected(usbMonitorManager, 3500);
            Object ctrlBlock = tryInvokeQuiet(usbMonitorManager, "getCtrlBlock");
            append("AUTO USB connected=" + describeObject(connected) +
                    " ctrlBlock=" + describeObject(ctrlBlock));
            dumpObjectState(proxy, "AUTO proxy state");
            dumpObjectState(usbMonitorManager, "AUTO usbMonitor state");
            dumpObjectState(deviceControlManager, "AUTO deviceControl state");

            if ("deviceControlHandle".equals(strategy)) {
                invokeWithLog(
                        "AUTO deviceControl.handleStartPreview",
                        deviceControlManager,
                        "handleStartPreview",
                        new Class[]{modeClass},
                        mode);
            } else if ("proxyHandle".equals(strategy)) {
                invokeWithLog(
                        "AUTO proxy.handleStartPreview",
                        proxy,
                        "handleStartPreview",
                        new Class[]{modeClass},
                        mode);
            } else if ("explicitEngine".equals(strategy)) {
                explicitInitAndStart(loader, proxy, ctrlBlock);
            } else if ("deviceControlThenExplicitStart".equals(strategy)) {
                invokeWithLog(
                        "AUTO deviceControl.handleStartPreview",
                        deviceControlManager,
                        "handleStartPreview",
                        new Class[]{modeClass},
                        mode);
                sleepMs(1500);
                tryInvoke(proxy, "startPreview");
            } else if ("directEngineProbe".equals(strategy)) {
                runDirectEngineProbe(
                        loader,
                        proxy,
                        usbMonitorManager,
                        deviceControlManager,
                        ctrlBlock,
                        frameCallback,
                        "AUTO direct scenario " + scenario);
            }

            boolean sawFrame = pollNativeScenario(proxy, "AUTO scenario " + scenario, 5000);
            append("AUTO scenario " + scenario + " result sawFrame=" + sawFrame);
            return sawFrame;
        } catch (Throwable t) {
            append("AUTO scenario " + scenario + " FAIL " + formatThrowable(t));
            return false;
        } finally {
            if (proxy != null) {
                tryInvoke(proxy, "stopPreview");
                tryInvoke(proxy, "releaseSource");
            }
            if (deviceControlManager != null) {
                tryInvoke(deviceControlManager, "release");
            }
            if (usbMonitorManager != null) {
                tryInvoke(usbMonitorManager, "unregisterMonitor");
                tryInvoke(usbMonitorManager, "destroyMonitor");
            }
            liveProxy = null;
            liveDeviceControlManager = null;
            liveUsbMonitorManager = null;
            sleepMs(800);
        }
    }

    private void explicitInitAndStart(ClassLoader loader, Object proxy, Object ctrlBlock) {
        if (ctrlBlock == null) {
            append("AUTO explicit init skipped ctrlBlock=null");
            return;
        }
        tryInvoke(proxy, "initData");
        try {
            Class<?> ctrlBlockClass = Class.forName(
                    "com.energy.iruvccamera.usb.USBMonitor$UsbControlBlock",
                    false,
                    loader);
            Object result = invoke(
                    proxy,
                    "initHandleEngine",
                    new Class[]{ctrlBlockClass, boolean.class},
                    ctrlBlock,
                    true);
            append("AUTO initHandleEngine(true) OK result=" + describeObject(result));
        } catch (Throwable t) {
            append("AUTO initHandleEngine(true) FAIL " + formatThrowable(t));
        }
        sleepMs(800);
        tryInvoke(proxy, "startPreview");
    }

    private boolean runDirectEngineProbe(
            ClassLoader loader,
            Object proxy,
            Object usbMonitorManager,
            Object deviceControlManager,
            Object ctrlBlock,
            Object callback,
            String label) {
        append(label + " direct engine probe start");
        List<Object> targets = new ArrayList<>();
        addProbeTarget(targets, proxy, label + " proxy");
        addProbeTarget(targets, usbMonitorManager, label + " usbMonitor");
        addProbeTarget(targets, deviceControlManager, label + " deviceControl");

        Object handleParam = createProbeObject(
                loader,
                "com.energy.ac020library.bean.DualUvcHandleParam",
                label);
        addProbeTarget(targets, handleParam, label + " dualUvcHandleParam");

        Object builder = createProbeObject(
                loader,
                "com.energy.ac020library.IrcamEngineBuilder",
                label);
        addProbeTarget(targets, builder, label + " ircamBuilder");

        Object engine = createProbeObject(
                loader,
                "com.energy.ac020library.IrcamEngine",
                label);
        addProbeTarget(targets, engine, label + " ircamEngine");

        for (int i = 0; i < targets.size() && i < 40; i++) {
            Object target = targets.get(i);
            populateProbeFields(target, ctrlBlock, handleParam, callback, "0", label);
            tryInstallFrameCallback(target, callback, label + " direct callback");
            dumpObjectState(target, label + " direct state");
            List<Object> fieldObjects = collectInterestingFieldObjects(target, label);
            for (Object fieldObject : fieldObjects) {
                addProbeTarget(targets, fieldObject, label + " fieldObject");
            }
        }

        int initialCount = targets.size();
        for (int i = 0; i < initialCount && i < targets.size(); i++) {
            Object target = targets.get(i);
            List<Object> products = invokeLikelyEngineMethods(
                    target,
                    ctrlBlock,
                    handleParam,
                    callback,
                    "0",
                    label + " configure");
            for (Object product : products) {
                addProbeTarget(targets, product, label + " product");
            }
        }

        for (int i = 0; i < targets.size(); i++) {
            Object target = targets.get(i);
            populateProbeFields(target, ctrlBlock, handleParam, callback, "0", label);
            tryInstallFrameCallback(target, callback, label + " product callback");
        }

        for (Object target : new ArrayList<>(targets)) {
            invokeLikelyEngineStartMethods(
                    target,
                    ctrlBlock,
                    handleParam,
                    callback,
                    "0",
                    label + " start");
        }

        boolean sawFrame = pollObjectsForFrames(targets, label + " direct objects", 6500);
        append(label + " direct engine probe result sawFrame=" + sawFrame +
                " targets=" + targets.size());
        return sawFrame;
    }

    private Object createProbeObject(ClassLoader loader, String className, String label) {
        try {
            Class<?> cls = Class.forName(className, true, loader);
            for (Method method : cls.getDeclaredMethods()) {
                int modifiers = method.getModifiers();
                if (!Modifier.isStatic(modifiers) || method.getParameterTypes().length != 0) {
                    continue;
                }
                if (!cls.isAssignableFrom(method.getReturnType())) {
                    continue;
                }
                String lower = method.getName().toLowerCase(Locale.US);
                if (!lower.equals("getinstance") &&
                        !lower.contains("instance") &&
                        !lower.contains("create") &&
                        !lower.contains("build")) {
                    continue;
                }
                try {
                    method.setAccessible(true);
                    Object result = method.invoke(null);
                    append(label + " createProbeObject static " + className +
                            "." + describeMethod(method) +
                            " result=" + describeObject(result));
                    if (result != null) {
                        return result;
                    }
                } catch (Throwable t) {
                    append(label + " createProbeObject static FAIL " + className +
                            "." + describeMethod(method) + " " + formatThrowable(t));
                }
            }
            for (Constructor<?> constructor : cls.getDeclaredConstructors()) {
                if (constructor.getParameterTypes().length != 0) {
                    continue;
                }
                try {
                    constructor.setAccessible(true);
                    Object result = constructor.newInstance();
                    append(label + " createProbeObject ctor " + className +
                            " result=" + describeObject(result));
                    return result;
                } catch (Throwable t) {
                    append(label + " createProbeObject ctor FAIL " + className +
                            " " + formatThrowable(t));
                }
            }
            append(label + " createProbeObject no no-arg path " + className);
        } catch (Throwable t) {
            append(label + " createProbeObject FAIL " + className + " " + formatThrowable(t));
        }
        return null;
    }

    private void addProbeTarget(List<Object> targets, Object target, String label) {
        if (target == null || !isInterestingProbeObject(target)) {
            return;
        }
        if (targets.size() >= 60) {
            append(label + " target skip cap target=" + describeObject(target));
            return;
        }
        for (Object existing : targets) {
            if (existing == target) {
                return;
            }
        }
        targets.add(target);
        append(label + " target add " + describeObject(target));
    }

    private boolean isInterestingProbeObject(Object object) {
        if (object == null) {
            return false;
        }
        Class<?> cls = object.getClass();
        if (cls.isArray() || cls.isPrimitive()) {
            return false;
        }
        String name = cls.getName();
        if (name.startsWith("java.") ||
                name.startsWith("android.") ||
                name.startsWith("kotlin.") ||
                name.startsWith("dalvik.")) {
            return false;
        }
        String lower = name.toLowerCase(Locale.US);
        return lower.contains("energy") ||
                lower.contains("ircam") ||
                lower.contains("uvc") ||
                lower.contains("thermal") ||
                lower.contains("tiny2c") ||
                lower.contains("fusion") ||
                lower.contains("preview") ||
                lower.contains("handle") ||
                lower.contains("engine");
    }

    private void populateProbeFields(
            Object target,
            Object ctrlBlock,
            Object handleParam,
            Object callback,
            String cameraId,
            String label) {
        if (target == null) {
            return;
        }
        Class<?> cls = target.getClass();
        int writes = 0;
        for (Field field : cls.getDeclaredFields()) {
            int modifiers = field.getModifiers();
            if (Modifier.isStatic(modifiers) || Modifier.isFinal(modifiers)) {
                continue;
            }
            Object value = probeValueForType(
                    field.getType(),
                    field.getName(),
                    ctrlBlock,
                    handleParam,
                    callback,
                    cameraId);
            if (value == null) {
                continue;
            }
            try {
                field.setAccessible(true);
                Object current = field.get(target);
                if (current != null && !isPrimitiveOrBoxed(field.getType())) {
                    continue;
                }
                field.set(target, value);
                writes++;
                append(label + " populateField OK " + cls.getName() +
                        "." + describeField(field) +
                        "=" + describeObject(value));
                if (writes >= 28) {
                    append(label + " populateField truncated " + cls.getName());
                    return;
                }
            } catch (Throwable t) {
                append(label + " populateField FAIL " + cls.getName() +
                        "." + describeField(field) +
                        " " + formatThrowable(t));
            }
        }
    }

    private Object probeValueForType(
            Class<?> type,
            String name,
            Object ctrlBlock,
            Object handleParam,
            Object callback,
            String cameraId) {
        if (ctrlBlock != null && type.isInstance(ctrlBlock)) {
            return ctrlBlock;
        }
        if (handleParam != null && type.isInstance(handleParam)) {
            return handleParam;
        }
        if (callback != null && type.isInstance(callback)) {
            return callback;
        }
        if (type.isInstance(this)) {
            return this;
        }
        if (type == String.class) {
            String lower = name.toLowerCase(Locale.US);
            if (lower.contains("camera") || lower.contains("id")) {
                return cameraId;
            }
            return null;
        }
        if (type == Boolean.TYPE || type == Boolean.class) {
            String lower = name.toLowerCase(Locale.US);
            return !(lower.contains("kill") ||
                    lower.contains("destroy") ||
                    lower.contains("pause") ||
                    lower.contains("stop"));
        }
        if (type == Integer.TYPE || type == Integer.class) {
            return chooseProbeInt(name);
        }
        if (type == Long.TYPE || type == Long.class) {
            return (long) chooseProbeInt(name);
        }
        if (type == Float.TYPE || type == Float.class) {
            return 1.0f;
        }
        if (type == Double.TYPE || type == Double.class) {
            return 1.0d;
        }
        if (type.isEnum()) {
            return chooseEnumConstant(type);
        }
        return null;
    }

    private int chooseProbeInt(String name) {
        String lower = name.toLowerCase(Locale.US);
        if (lower.contains("thermal") && lower.contains("height")) {
            return THERMAL_HEIGHT;
        }
        if (lower.contains("thermal") && lower.contains("width")) {
            return THERMAL_WIDTH;
        }
        if (lower.contains("ir") && lower.contains("height")) {
            return 386;
        }
        if (lower.contains("ir") && lower.contains("width")) {
            return THERMAL_WIDTH;
        }
        if (lower.contains("visible") && lower.contains("height")) {
            return 1080;
        }
        if (lower.contains("visible") && lower.contains("width")) {
            return 1440;
        }
        if (lower.contains("height")) {
            return THERMAL_HEIGHT;
        }
        if (lower.contains("width")) {
            return THERMAL_WIDTH;
        }
        if (lower.contains("fps") || lower.contains("rate")) {
            return 25;
        }
        if (lower.contains("mode")) {
            return 1;
        }
        if (lower.contains("format")) {
            return 0;
        }
        return 1;
    }

    private Object chooseEnumConstant(Class<?> enumType) {
        Object[] constants = enumType.getEnumConstants();
        if (constants == null || constants.length == 0) {
            return null;
        }
        for (Object constant : constants) {
            String lower = enumName(constant).toLowerCase(Locale.US);
            if (lower.contains("dual") || lower.contains("fusion") || lower.contains("tiny2c")) {
                return constant;
            }
        }
        return constants[0];
    }

    private void dumpObjectState(Object target, String label) {
        if (target == null) {
            append(label + " objectState null");
            return;
        }
        Class<?> cls = target.getClass();
        append(label + " objectState class=" + cls.getName());
        int count = 0;
        for (Field field : cls.getDeclaredFields()) {
            if (Modifier.isStatic(field.getModifiers())) {
                continue;
            }
            String lower = field.getName().toLowerCase(Locale.US) + " " +
                    field.getType().getName().toLowerCase(Locale.US);
            if (!lower.contains("frame") &&
                    !lower.contains("temp") &&
                    !lower.contains("data") &&
                    !lower.contains("preview") &&
                    !lower.contains("callback") &&
                    !lower.contains("handle") &&
                    !lower.contains("engine") &&
                    !lower.contains("uvc") &&
                    !lower.contains("usb") &&
                    !lower.contains("ctrl") &&
                    !lower.contains("surface") &&
                    !lower.contains("width") &&
                    !lower.contains("height") &&
                    !lower.contains("fps")) {
                continue;
            }
            try {
                field.setAccessible(true);
                Object value = field.get(target);
                append(label + " field " + describeField(field) +
                        "=" + describeProbeValue(value));
                count++;
                if (count >= 40) {
                    append(label + " objectState truncated");
                    return;
                }
            } catch (Throwable t) {
                append(label + " field FAIL " + describeField(field) +
                        " " + formatThrowable(t));
            }
        }
    }

    private List<Object> collectInterestingFieldObjects(Object target, String label) {
        List<Object> objects = new ArrayList<>();
        if (target == null) {
            return objects;
        }
        for (Field field : target.getClass().getDeclaredFields()) {
            if (Modifier.isStatic(field.getModifiers())) {
                continue;
            }
            try {
                field.setAccessible(true);
                Object value = field.get(target);
                if (isInterestingProbeObject(value)) {
                    objects.add(value);
                    append(label + " collectFieldObject " +
                            target.getClass().getName() + "." + field.getName() +
                            "=" + describeObject(value));
                }
            } catch (Throwable ignored) {
                // Some vendor fields are not safely readable; keep probing others.
            }
            if (objects.size() >= 16) {
                append(label + " collectFieldObject truncated");
                break;
            }
        }
        return objects;
    }

    private List<Object> invokeLikelyEngineMethods(
            Object target,
            Object ctrlBlock,
            Object handleParam,
            Object callback,
            String cameraId,
            String label) {
        return invokeLikelyEngineMethodsInternal(
                target,
                ctrlBlock,
                handleParam,
                callback,
                cameraId,
                label,
                false);
    }

    private List<Object> invokeLikelyEngineStartMethods(
            Object target,
            Object ctrlBlock,
            Object handleParam,
            Object callback,
            String cameraId,
            String label) {
        return invokeLikelyEngineMethodsInternal(
                target,
                ctrlBlock,
                handleParam,
                callback,
                cameraId,
                label,
                true);
    }

    private List<Object> invokeLikelyEngineMethodsInternal(
            Object target,
            Object ctrlBlock,
            Object handleParam,
            Object callback,
            String cameraId,
            String label,
            boolean startPhase) {
        List<Object> products = new ArrayList<>();
        if (target == null) {
            return products;
        }
        Set<String> seen = new HashSet<>();
        Method[] declared = target.getClass().getDeclaredMethods();
        Method[] publicMethods = target.getClass().getMethods();
        int invoked = 0;
        for (Method method : concatMethods(declared, publicMethods)) {
            String key = describeMethod(method);
            if (!seen.add(key)) {
                continue;
            }
            if (!isLikelyEngineProbeMethod(method, startPhase)) {
                continue;
            }
            Object[] args = buildProbeArgs(method, ctrlBlock, handleParam, callback, cameraId);
            if (args == null) {
                continue;
            }
            try {
                method.setAccessible(true);
                Object result = method.invoke(target, args);
                invoked++;
                append(label + " invoke OK " + target.getClass().getName() +
                        "." + describeMethod(method) +
                        " args=" + describeArgs(args) +
                        " result=" + describeObject(result));
                if (isInterestingProbeObject(result)) {
                    products.add(result);
                }
                if (invoked >= 55) {
                    append(label + " invoke truncated " + target.getClass().getName());
                    break;
                }
            } catch (Throwable t) {
                invoked++;
                append(label + " invoke FAIL " + target.getClass().getName() +
                        "." + describeMethod(method) +
                        " args=" + describeArgs(args) +
                        " " + formatThrowable(t));
                if (invoked >= 55) {
                    append(label + " invoke truncated " + target.getClass().getName());
                    break;
                }
            }
        }
        return products;
    }

    private Method[] concatMethods(Method[] a, Method[] b) {
        Method[] out = new Method[a.length + b.length];
        System.arraycopy(a, 0, out, 0, a.length);
        System.arraycopy(b, 0, out, a.length, b.length);
        return out;
    }

    private boolean isLikelyEngineProbeMethod(Method method, boolean startPhase) {
        String lower = method.getName().toLowerCase(Locale.US);
        if (lower.equals("wait") ||
                lower.equals("equals") ||
                lower.equals("hashcode") ||
                lower.equals("tostring") ||
                lower.contains("stop") ||
                lower.contains("release") ||
                lower.contains("destroy") ||
                lower.contains("unregister") ||
                lower.contains("close") ||
                lower.contains("cancel") ||
                lower.contains("pause") ||
                lower.contains("free")) {
            return false;
        }
        if (method.getParameterTypes().length > 4) {
            return false;
        }
        if (startPhase) {
            return lower.contains("start") ||
                    lower.contains("open") ||
                    lower.contains("preview") ||
                    lower.contains("stream") ||
                    lower.contains("run") ||
                    lower.contains("handle");
        }
        return lower.contains("set") ||
                lower.contains("init") ||
                lower.contains("build") ||
                lower.contains("create") ||
                lower.contains("callback") ||
                lower.contains("listener") ||
                lower.contains("param") ||
                lower.contains("handle") ||
                lower.contains("engine") ||
                lower.contains("uvc") ||
                lower.contains("data") ||
                lower.contains("frame") ||
                lower.contains("temp") ||
                lower.contains("config") ||
                lower.contains("register");
    }

    private Object[] buildProbeArgs(
            Method method,
            Object ctrlBlock,
            Object handleParam,
            Object callback,
            String cameraId) {
        Class<?>[] types = method.getParameterTypes();
        Object[] args = new Object[types.length];
        for (int i = 0; i < types.length; i++) {
            Object value = probeValueForType(
                    types[i],
                    method.getName() + "_" + i,
                    ctrlBlock,
                    handleParam,
                    callback,
                    cameraId);
            if (value == null) {
                return null;
            }
            args[i] = value;
        }
        return args;
    }

    private boolean pollObjectsForFrames(List<Object> targets, String label, long timeoutMs) {
        long deadline = System.currentTimeMillis() + timeoutMs;
        long lastLogAt = 0;
        while (System.currentTimeMillis() < deadline && running) {
            for (Object target : targets) {
                byte[] frame = findFrameInObject(target, label);
                if (frame != null) {
                    append(label + " FRAME target=" + describeObject(target));
                    renderThermal(frame, label + " frame");
                    return true;
                }
            }
            long now = System.currentTimeMillis();
            if (now - lastLogAt > 1500) {
                lastLogAt = now;
                append(label + " poll no frame targets=" + targets.size());
            }
            sleepMs(250);
        }
        return false;
    }

    private byte[] findFrameInObject(Object target, String label) {
        if (target == null) {
            return null;
        }
        for (Field field : target.getClass().getDeclaredFields()) {
            String lower = field.getName().toLowerCase(Locale.US);
            if (!lower.contains("frame") &&
                    !lower.contains("temp") &&
                    !lower.contains("raw") &&
                    !lower.contains("remap") &&
                    !lower.contains("data")) {
                continue;
            }
            try {
                field.setAccessible(true);
                byte[] frame = chooseRenderableFrameFromValue(field.get(target));
                if (frame != null) {
                    append(label + " frameFromField " + target.getClass().getName() +
                            "." + describeField(field));
                    return frame;
                }
            } catch (Throwable ignored) {
                // Keep polling other fields and methods.
            }
        }
        Set<String> seen = new HashSet<>();
        for (Method method : concatMethods(
                target.getClass().getDeclaredMethods(),
                target.getClass().getMethods())) {
            String key = describeMethod(method);
            if (!seen.add(key)) {
                continue;
            }
            if (method.getParameterTypes().length != 0) {
                continue;
            }
            String lower = method.getName().toLowerCase(Locale.US);
            if (!lower.contains("frame") &&
                    !lower.contains("temp") &&
                    !lower.contains("raw") &&
                    !lower.contains("remap") &&
                    !lower.contains("data")) {
                continue;
            }
            try {
                method.setAccessible(true);
                byte[] frame = chooseRenderableFrameFromValue(method.invoke(target));
                if (frame != null) {
                    append(label + " frameFromMethod " + target.getClass().getName() +
                            "." + describeMethod(method));
                    return frame;
                }
            } catch (Throwable ignored) {
                // Some getters require initialized state; keep polling others.
            }
        }
        return null;
    }

    private Object createIrFrameCallback(ClassLoader loader, String label) {
        try {
            Class<?> callbackClass = Class.forName(
                    "com.energy.ac020library.bean.IIrFrameCallback",
                    true,
                    loader);
            Object callback = Proxy.newProxyInstance(
                    callbackClass.getClassLoader(),
                    new Class[]{callbackClass},
                    (proxy, method, args) -> {
                        append(label + " IIrFrameCallback." + method.getName() +
                                " args=" + describeArgs(args));
                        byte[] frame = findRenderableFrameArg(args);
                        if (frame != null) {
                            renderThermal(frame, label + " callback " + method.getName());
                        }
                        return defaultReturnValue(method.getReturnType());
                    });
            append(label + " frameCallbackProxy OK methods=" +
                    callbackClass.getDeclaredMethods().length);
            return callback;
        } catch (Throwable t) {
            append(label + " frameCallbackProxy FAIL " + formatThrowable(t));
            return null;
        }
    }

    private void installFrameCallbacksOnKnownSingletons(
            ClassLoader loader,
            Object callback,
            String label) {
        if (callback == null) {
            return;
        }
        String[] classes = new String[]{
                "com.energy.dualmodule.sdk.Tiny2CDualFusionProxy",
                "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualDeviceControlManager",
                "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualFusionPreviewManager",
                "com.energy.ac020library.IrcamEngine"
        };
        for (String name : classes) {
            try {
                Class<?> cls = Class.forName(name, true, loader);
                Object target = null;
                for (Method method : cls.getMethods()) {
                    if (method.getParameterTypes().length == 0 &&
                            method.getName().equals("getInstance")) {
                        target = method.invoke(null);
                        break;
                    }
                }
                if (target == null) {
                    append(label + " callback singleton skip no getInstance " + name);
                    continue;
                }
                tryInstallFrameCallback(target, callback, label + " " + name);
            } catch (Throwable t) {
                append(label + " callback singleton FAIL " + name +
                        " " + formatThrowable(t));
            }
        }
    }

    private int tryInstallFrameCallback(Object target, Object callback, String label) {
        if (target == null || callback == null) {
            return 0;
        }
        Set<String> seen = new HashSet<>();
        int installed = 0;
        installed += tryInstallFrameCallbackMethods(
                target,
                callback,
                label,
                target.getClass().getMethods(),
                seen);
        installed += tryInstallFrameCallbackMethods(
                target,
                callback,
                label,
                target.getClass().getDeclaredMethods(),
                seen);
        append(label + " callback install attempts=" + installed +
                " target=" + target.getClass().getName());
        return installed;
    }

    private int tryInstallFrameCallbackMethods(
            Object target,
            Object callback,
            String label,
            Method[] methods,
            Set<String> seen) {
        int installed = 0;
        for (Method method : methods) {
            String key = describeMethod(method);
            if (!seen.add(key)) {
                continue;
            }
            Class<?>[] types = method.getParameterTypes();
            if (types.length != 1 || !types[0].isInstance(callback)) {
                continue;
            }
            String lower = method.getName().toLowerCase(Locale.US);
            if (!lower.contains("callback") &&
                    !lower.contains("listener") &&
                    !lower.contains("frame") &&
                    !lower.contains("data")) {
                append(label + " callback candidate non-obvious " + describeMethod(method));
            }
            try {
                method.setAccessible(true);
                Object result = method.invoke(target, callback);
                installed++;
                append(label + " callback install OK " + describeMethod(method) +
                        " result=" + describeObject(result));
            } catch (Throwable t) {
                append(label + " callback install FAIL " + describeMethod(method) +
                        " " + formatThrowable(t));
            }
        }
        return installed;
    }

    private Object waitForUsbConnected(Object usbMonitorManager, long timeoutMs) {
        long deadline = System.currentTimeMillis() + timeoutMs;
        Object connected = null;
        while (System.currentTimeMillis() < deadline) {
            connected = tryInvokeQuiet(usbMonitorManager, "isDeviceConnected");
            Object ctrlBlock = tryInvokeQuiet(usbMonitorManager, "getCtrlBlock");
            if (Boolean.TRUE.equals(connected) && ctrlBlock != null) {
                return connected;
            }
            sleepMs(150);
        }
        return connected;
    }

    private boolean pollNativeScenario(Object proxy, String label, long timeoutMs) {
        long deadline = System.currentTimeMillis() + timeoutMs;
        long lastLogAt = 0;
        while (System.currentTimeMillis() < deadline && running) {
            sleepMs(250);
            Object frameCount = tryInvokeQuiet(proxy, "getFrameCount");
            Object firstFrame = tryInvokeQuiet(proxy, "getFirstFrameFlag");
            byte[] rawTemp = (byte[]) tryInvokeQuiet(proxy, "getRawTempData");
            byte[] remapTemp = (byte[]) tryInvokeQuiet(proxy, "getRemapTempData");
            byte[] frame = chooseRenderableFrame(rawTemp, remapTemp);
            if (frame != null) {
                append(label + " FRAME frameCount=" + frameCount +
                        " firstFrame=" + firstFrame +
                        " rawTemp=" + describeBytes(rawTemp) +
                        " remapTemp=" + describeBytes(remapTemp));
                renderThermal(frame, label + " frame=" + frameCount);
                return true;
            }
            long now = System.currentTimeMillis();
            if (now - lastLogAt > 1200) {
                lastLogAt = now;
                append(label + " poll frameCount=" + frameCount +
                        " firstFrame=" + firstFrame +
                        " rawTemp=" + describeBytes(rawTemp) +
                        " remapTemp=" + describeBytes(remapTemp));
            }
        }
        return false;
    }

    private List<String> getCameraIdCandidates() {
        List<String> ids = new ArrayList<>();
        try {
            CameraManager manager = (CameraManager) getSystemService(CAMERA_SERVICE);
            for (String id : manager.getCameraIdList()) {
                if (!ids.contains(id)) {
                    ids.add(id);
                }
            }
        } catch (Throwable t) {
            append("AUTO camera id list FAIL " + formatThrowable(t));
        }
        for (String fallback : new String[]{"1", "0", "2", "3", ""}) {
            if (!ids.contains(fallback)) {
                ids.add(fallback);
            }
        }
        append("AUTO cameraIdCandidates=" + ids);
        return ids;
    }

    private void cleanupLiveObjects() {
        Object proxy = liveProxy;
        if (proxy != null) {
            tryInvoke(proxy, "stopPreview");
            tryInvoke(proxy, "releaseSource");
        }
        Object deviceControl = liveDeviceControlManager;
        if (deviceControl != null) {
            tryInvoke(deviceControl, "release");
        }
        Object monitor = liveUsbMonitorManager;
        if (monitor != null) {
            tryInvoke(monitor, "unregisterMonitor");
            tryInvoke(monitor, "destroyMonitor");
        }
        liveProxy = null;
        liveDeviceControlManager = null;
        liveUsbMonitorManager = null;
    }

    private synchronized DexClassLoader getThermoVueClassLoader() throws Exception {
        if (thermoVueLoader != null) {
            return thermoVueLoader;
        }
        PackageInfo packageInfo = getPackageManager().getPackageInfo(THERMOVUE_PACKAGE, 0);
        ApplicationInfo appInfo = packageInfo.applicationInfo;
        File libDir = new File(getCodeCacheDir(), "thermovue_libs");
        File dexDir = new File(getCodeCacheDir(), "thermovue_dex");
        recreateDir(libDir);
        recreateDir(dexDir);
        int libs = extractNativeLibraries(appInfo.sourceDir, libDir);
        append("ThermoVue classloader sourceDir=" + appInfo.sourceDir);
        append("ThermoVue extractedNativeLibs=" + libs + " to " + libDir.getAbsolutePath());
        thermoVueLoader = new DexClassLoader(
                appInfo.sourceDir,
                dexDir.getAbsolutePath(),
                libDir.getAbsolutePath(),
                getClassLoader());
        return thermoVueLoader;
    }

    private void initializeMmkv(ClassLoader loader) {
        try {
            Class<?> mmkvClass = Class.forName("com.tencent.mmkv.MMKV", true, loader);
            File mmkvDir = new File(getFilesDir(), "thermovue_mmkv");
            //noinspection ResultOfMethodCallIgnored
            mmkvDir.mkdirs();
            Object result = mmkvClass.getMethod("initialize", String.class)
                    .invoke(null, mmkvDir.getAbsolutePath());
            append("MMKV initialize OK result=" + describeObject(result));
        } catch (Throwable t) {
            append("MMKV initialize FAIL " + formatThrowable(t));
        }
    }

    private void initializeBlankjUtils(ClassLoader loader) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            CountDownLatch latch = new CountDownLatch(1);
            runOnUiThread(() -> {
                try {
                    initializeBlankjUtils(loader);
                } finally {
                    latch.countDown();
                }
            });
            try {
                if (!latch.await(5, TimeUnit.SECONDS)) {
                    append("Blankj Utils init FAIL main-thread timeout");
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                append("Blankj Utils init FAIL interrupted");
            }
            return;
        }
        try {
            Application app = new Application();
            Method attach = ContextWrapper.class.getDeclaredMethod("attachBaseContext", Context.class);
            attach.setAccessible(true);
            attach.invoke(app, this);
            Class<?> utilsClass = Class.forName("com.blankj.utilcode.util.Utils", true, loader);
            utilsClass.getMethod("init", Application.class).invoke(null, app);
            Object result = utilsClass.getMethod("getApp").invoke(null);
            append("Blankj Utils init OK app=" + describeObject(result));
        } catch (Throwable t) {
            append("Blankj Utils init FAIL " + formatThrowable(t));
        }
    }

    private void initializeRxBaseApplication(ClassLoader loader) {
        try {
            Class<?> rxBaseClass = Class.forName(
                    "com.energy.baselibrary.base.RXBaseApplication", true, loader);
            Class<?> baseClass = Class.forName(
                    "com.zzk.rxmvvmbase.base.BaseApplication", true, loader);
            Application rxApp = (Application) rxBaseClass.getConstructor().newInstance();
            Method attach = ContextWrapper.class.getDeclaredMethod("attachBaseContext", Context.class);
            attach.setAccessible(true);
            attach.invoke(rxApp, this);

            File baseDir = getExternalFilesDir(null);
            File pictures = new File(baseDir, "Pictures");
            File dcim = new File(baseDir, "DCIM");
            File deviceDir = new File(baseDir, "deviceData");
            File calibrationDir = new File(deviceDir, "calibration");
            File commonDataDir = new File(dcim, "eco160dlp");
            File commonCalibrationDir = new File(commonDataDir, "common_calibration_data");
            //noinspection ResultOfMethodCallIgnored
            pictures.mkdirs();
            //noinspection ResultOfMethodCallIgnored
            commonCalibrationDir.mkdirs();
            //noinspection ResultOfMethodCallIgnored
            calibrationDir.mkdirs();

            setStaticField(rxBaseClass, "sInstance", rxApp);
            setStaticField(baseClass, "sInstance", rxApp);
            setField(baseClass, rxApp, "context", this);
            setField(rxBaseClass, rxApp, "INFISENSE_DIR", "thermo_tc2c");
            setField(rxBaseClass, rxApp, "INFISENSE_SAVE_DIR",
                    new File(pictures, "thermo_tc2c").getAbsolutePath());
            setField(rxBaseClass, rxApp, "COMMON_DATA_SAVE_DIR", commonDataDir.getAbsolutePath());
            setField(rxBaseClass, rxApp, "CONFIG_FILE_NAME", "config.json");
            setField(rxBaseClass, rxApp, "CONFIG_FILE_PATH", pictures.getAbsolutePath());
            setField(rxBaseClass, rxApp, "DEVICE_DATA_DIR", "deviceData");
            setField(rxBaseClass, rxApp, "DEVICE_DATA_SAVE_DIR", deviceDir.getAbsolutePath());
            setField(rxBaseClass, rxApp, "CALIBRATION_DATA_SAVE_DIR",
                    calibrationDir.getAbsolutePath());
            setField(rxBaseClass, rxApp, "COMMON_CALIBRATION_DATA_PATH",
                    commonCalibrationDir.getAbsolutePath());
            setField(rxBaseClass, rxApp, "CALIBRATION_DATA_EXCEPTION_LOG",
                    new File(calibrationDir, "CalibrationDataLog.txt").getAbsolutePath());
            trySetField(rxBaseClass, rxApp, "ISP_SWITCH_PATH",
                    new File(deviceDir, "isp_switch.json").getAbsolutePath());
            trySetField(rxBaseClass, rxApp, "ISP_STATIC_LIB_PATH",
                    new File(deviceDir, "isp_static_lib.json").getAbsolutePath());
            trySetField(rxBaseClass, rxApp, "ISP_H_PATH",
                    new File(deviceDir, "isp_H.json").getAbsolutePath());
            trySetField(rxBaseClass, rxApp, "ISP_L_PATH",
                    new File(deviceDir, "isp_L.json").getAbsolutePath());
            setField(rxBaseClass, rxApp, "DATA_FILE_SAVE_PATH", pictures.getAbsolutePath());
            setField(rxBaseClass, rxApp, "isNeedReadCalData", false);
            Object result = rxBaseClass.getMethod("getInstance").invoke(null);
            append("RXBaseApplication bootstrap OK instance=" + describeObject(result));
        } catch (Throwable t) {
            append("RXBaseApplication bootstrap FAIL " + formatThrowable(t));
        }
    }

    private void dumpImportantClasses(ClassLoader loader) {
        dumpImportantClasses(loader, true);
    }

    private void dumpClassesOnly() {
        append("===== manual class dump =====");
        try {
            DexClassLoader loader = getThermoVueClassLoader();
            dumpImportantClasses(loader, true);
            dumpTargetedReverseEngineeringHints(loader);
        } catch (Throwable t) {
            append("manual class dump FAIL " + formatThrowable(t));
        }
    }

    private void dumpImportantClasses(ClassLoader loader, boolean verbose) {
        String[] classes = new String[]{
                "com.energy.dualmodule.sdk.Tiny2CDualFusionProxy",
                "com.energy.dualmodule.sdk.uvc.USBMonitorManager",
                "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualDeviceControlManager",
                "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualFusionPreviewManager",
                "com.energy.dualmodule.sdk.service.task.DualPreviewMode",
                "com.energy.iruvccamera.usb.USBMonitor",
                "com.energy.ac020library.IrcamEngine",
                "com.energy.ac020library.bean.IIrFrameCallback"
        };
        for (String name : classes) {
            try {
                Class<?> cls = Class.forName(name, false, loader);
                append("classLoad OK " + name +
                        " methods=" + cls.getDeclaredMethods().length +
                        " fields=" + cls.getDeclaredFields().length);
                if (!verbose) {
                    continue;
                }
                int count = 0;
                for (Method method : cls.getDeclaredMethods()) {
                    append("classMethod " + name + " " + describeMethod(method));
                    count++;
                    if (count >= 500) {
                        append("classMethod " + name + " truncated");
                        break;
                    }
                }
            } catch (Throwable t) {
                append("classLoad FAIL " + name + " " + formatThrowable(t));
            }
        }
    }

    private void dumpTargetedReverseEngineeringHints(ClassLoader loader) {
        append("===== targeted reverse-engineering hints =====");
        String[] classes = new String[]{
                "com.energy.dualmodule.sdk.Tiny2CDualFusionProxy",
                "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualDeviceControlManager",
                "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualFusionPreviewManager",
                "com.energy.ac020library.IrcamEngine",
                "com.energy.ac020library.IrcamEngineBuilder",
                "com.energy.ac020library.bean.DualUvcHandleParam",
                "com.energy.iruvccamera.usb.USBMonitor"
        };
        for (String name : classes) {
            try {
                Class<?> cls = Class.forName(name, false, loader);
                for (Method method : cls.getDeclaredMethods()) {
                    String lower = method.getName().toLowerCase(Locale.US);
                    if (lower.contains("preview") ||
                            lower.contains("frame") ||
                            lower.contains("surface") ||
                            lower.contains("callback") ||
                            lower.contains("handle") ||
                            lower.contains("cal") ||
                            lower.contains("data") ||
                            lower.contains("temp") ||
                            lower.contains("fusion") ||
                            lower.contains("listener") ||
                            lower.contains("restart")) {
                        append("targetMethod " + name + " " + describeMethod(method));
                    }
                }
                for (Field field : cls.getDeclaredFields()) {
                    String lower = field.getName().toLowerCase(Locale.US);
                    if (lower.contains("preview") ||
                            lower.contains("frame") ||
                            lower.contains("surface") ||
                            lower.contains("callback") ||
                            lower.contains("handle") ||
                            lower.contains("cal") ||
                            lower.contains("data") ||
                            lower.contains("temp") ||
                            lower.contains("fusion") ||
                            lower.contains("listener") ||
                            lower.contains("ir") ||
                            lower.contains("uvc")) {
                        append("targetField " + name + " " + describeField(field));
                    }
                }
            } catch (Throwable t) {
                append("targetDump FAIL " + name + " " + formatThrowable(t));
            }
        }
        try {
            Class<?> modeClass = Class.forName(
                    "com.energy.dualmodule.sdk.service.task.DualPreviewMode", false, loader);
            Object[] constants = modeClass.getEnumConstants();
            if (constants != null) {
                for (Object constant : constants) {
                    append("targetEnum DualPreviewMode " + enumName(constant) +
                            " value=" + describeObject(tryInvokeQuiet(constant, "getMode")));
                }
            }
        } catch (Throwable t) {
            append("targetEnum FAIL " + formatThrowable(t));
        }
    }

    private void dumpThermoVueClassIndex(ClassLoader loader) {
        append("===== ThermoVue relevant DEX class index =====");
        DexFile dexFile = null;
        try {
            PackageInfo packageInfo = getPackageManager().getPackageInfo(THERMOVUE_PACKAGE, 0);
            dexFile = new DexFile(packageInfo.applicationInfo.sourceDir);
            Enumeration<String> entries = dexFile.entries();
            int total = 0;
            int relevant = 0;
            int logged = 0;
            Set<String> highSignalClasses = new HashSet<>();
            while (entries.hasMoreElements()) {
                String name = entries.nextElement();
                total++;
                if (!matchesRelevantClassName(name)) {
                    continue;
                }
                relevant++;
                if (isHighSignalClassName(name)) {
                    highSignalClasses.add(name);
                }
                if (logged < 260) {
                    logged++;
                    append("dexClassRelevant " + name);
                }
            }
            append("dexClassIndex total=" + total +
                    " relevant=" + relevant +
                    " logged=" + logged +
                    " highSignal=" + highSignalClasses.size());

            List<String> sorted = new ArrayList<>(highSignalClasses);
            Collections.sort(sorted);
            int signatures = 0;
            for (String name : sorted) {
                if (signatures >= 70) {
                    append("dexSignature truncated remaining=" + (sorted.size() - signatures));
                    break;
                }
                dumpCompactClassSignature(loader, name);
                signatures++;
            }
        } catch (Throwable t) {
            append("dexClassIndex FAIL " + formatThrowable(t));
        } finally {
            if (dexFile != null) {
                try {
                    dexFile.close();
                } catch (Throwable ignored) {
                    // DEX index logging is best-effort only.
                }
            }
        }
    }

    private boolean matchesRelevantClassName(String name) {
        String lower = name.toLowerCase(Locale.US);
        return lower.contains("thermal") ||
                lower.contains("thermo") ||
                lower.contains("ircam") ||
                lower.contains("uvc") ||
                lower.contains("usb") ||
                lower.contains("tiny2c") ||
                lower.contains("dual") ||
                lower.contains("fusion") ||
                lower.contains("frame") ||
                lower.contains("temp") ||
                lower.contains("camera") ||
                lower.contains("preview") ||
                lower.contains("calibration") ||
                lower.contains("pallete") ||
                lower.contains("palette");
    }

    private boolean isHighSignalClassName(String name) {
        String lower = name.toLowerCase(Locale.US);
        return lower.contains("ircam") ||
                lower.contains("iirframe") ||
                lower.contains("uvc") ||
                lower.contains("tiny2c") ||
                lower.contains("dualfusion") ||
                lower.contains("dualmodule") ||
                lower.contains("usbmonitor") ||
                lower.contains("framecallback") ||
                lower.contains("previewmanager") ||
                lower.contains("devicecontrol");
    }

    private void dumpCompactClassSignature(ClassLoader loader, String name) {
        try {
            Class<?> cls = Class.forName(name, false, loader);
            append("dexSignature " + name +
                    " constructors=" + cls.getDeclaredConstructors().length +
                    " methods=" + cls.getDeclaredMethods().length +
                    " fields=" + cls.getDeclaredFields().length);
            int constructorCount = 0;
            for (java.lang.reflect.Constructor<?> constructor : cls.getDeclaredConstructors()) {
                append("dexConstructor " + name + " " + describeConstructor(constructor));
                constructorCount++;
                if (constructorCount >= 5) {
                    append("dexConstructor " + name + " truncated");
                    break;
                }
            }
            int methodCount = 0;
            for (Method method : cls.getDeclaredMethods()) {
                String lower = method.getName().toLowerCase(Locale.US);
                if (lower.contains("preview") ||
                        lower.contains("frame") ||
                        lower.contains("surface") ||
                        lower.contains("callback") ||
                        lower.contains("handle") ||
                        lower.contains("init") ||
                        lower.contains("start") ||
                        lower.contains("stop") ||
                        lower.contains("open") ||
                        lower.contains("close") ||
                        lower.contains("usb") ||
                        lower.contains("uvc") ||
                        lower.contains("data") ||
                        lower.contains("temp")) {
                    append("dexMethod " + name + " " + describeMethod(method));
                    methodCount++;
                    if (methodCount >= 16) {
                        append("dexMethod " + name + " truncated");
                        break;
                    }
                }
            }
        } catch (Throwable t) {
            append("dexSignature FAIL " + name + " " + formatThrowable(t));
        }
    }

    private byte[] chooseRenderableFrame(byte[] rawTemp, byte[] remapTemp) {
        if (rawTemp != null && rawTemp.length == THERMAL_U16_BYTES) {
            return rawTemp;
        }
        if (rawTemp != null && rawTemp.length >= THERMAL_PACKET_TEMP_OFFSET + THERMAL_U16_BYTES) {
            byte[] plane = new byte[THERMAL_U16_BYTES];
            System.arraycopy(rawTemp, THERMAL_PACKET_TEMP_OFFSET, plane, 0, THERMAL_U16_BYTES);
            return plane;
        }
        if (remapTemp != null && remapTemp.length == THERMAL_U16_BYTES) {
            return remapTemp;
        }
        if (remapTemp != null && remapTemp.length >= THERMAL_U16_BYTES) {
            byte[] plane = new byte[THERMAL_U16_BYTES];
            System.arraycopy(remapTemp, 0, plane, 0, THERMAL_U16_BYTES);
            return plane;
        }
        return null;
    }

    private byte[] chooseRenderableFrameFromValue(Object value) {
        if (value instanceof byte[]) {
            return chooseRenderableFrame((byte[]) value, null);
        }
        int count = THERMAL_WIDTH * THERMAL_HEIGHT;
        if (value instanceof short[] && ((short[]) value).length >= count) {
            short[] source = (short[]) value;
            byte[] frame = new byte[THERMAL_U16_BYTES];
            for (int i = 0; i < count; i++) {
                int sample = source[i] & 0xffff;
                frame[i * 2] = (byte) (sample & 0xff);
                frame[i * 2 + 1] = (byte) ((sample >> 8) & 0xff);
            }
            return frame;
        }
        if (value instanceof int[] && ((int[]) value).length >= count) {
            int[] source = (int[]) value;
            byte[] frame = new byte[THERMAL_U16_BYTES];
            for (int i = 0; i < count; i++) {
                int sample = source[i] & 0xffff;
                frame[i * 2] = (byte) (sample & 0xff);
                frame[i * 2 + 1] = (byte) ((sample >> 8) & 0xff);
            }
            return frame;
        }
        if (value instanceof float[] && ((float[]) value).length >= count) {
            float[] source = (float[]) value;
            int min = Integer.MAX_VALUE;
            int max = Integer.MIN_VALUE;
            int[] scaled = new int[count];
            for (int i = 0; i < count; i++) {
                int sample = Math.round(source[i] * 100.0f);
                scaled[i] = sample;
                min = Math.min(min, sample);
                max = Math.max(max, sample);
            }
            int offset = min < 0 ? -min : 0;
            byte[] frame = new byte[THERMAL_U16_BYTES];
            for (int i = 0; i < count; i++) {
                int sample = Math.max(0, Math.min(0xffff, scaled[i] + offset));
                frame[i * 2] = (byte) (sample & 0xff);
                frame[i * 2 + 1] = (byte) ((sample >> 8) & 0xff);
            }
            return frame;
        }
        return null;
    }

    private void renderThermal(byte[] frame, String label) {
        if (frame == null || frame.length < THERMAL_U16_BYTES) {
            append("renderThermal skipped invalid frame " + describeBytes(frame));
            return;
        }
        byte[] stored = new byte[THERMAL_U16_BYTES];
        System.arraycopy(frame, 0, stored, 0, THERMAL_U16_BYTES);
        latestThermalFrame = stored;
        latestThermalLabel = label;
        latestThermalFrameAt = System.currentTimeMillis();

        int count = THERMAL_WIDTH * THERMAL_HEIGHT;
        int[] values = new int[count];
        int min = Integer.MAX_VALUE;
        int max = Integer.MIN_VALUE;
        long sum = 0;
        for (int i = 0; i < count; i++) {
            int byteIndex = i * 2;
            int value = (frame[byteIndex] & 0xff) | ((frame[byteIndex + 1] & 0xff) << 8);
            values[i] = value;
            if (value < min) {
                min = value;
            }
            if (value > max) {
                max = value;
            }
            sum += value;
        }
        int range = Math.max(1, max - min);
        int[] colors = new int[count];
        for (int i = 0; i < count; i++) {
            int normalized = (values[i] - min) * 255 / range;
            colors[i] = heatColor(normalized);
        }
        Bitmap bitmap = Bitmap.createBitmap(colors, THERMAL_WIDTH, THERMAL_HEIGHT, Bitmap.Config.ARGB_8888);
        String status = label + " min=" + min + " max=" + max +
                " mean=" + (sum / count);
        runOnUiThread(() -> {
            preview.setBitmap(bitmap);
            statusText.setText(status);
        });
    }

    private int heatColor(int v) {
        v = Math.max(0, Math.min(255, v));
        int r;
        int g;
        int b;
        if (v < 64) {
            r = 0;
            g = v;
            b = 96 + v * 2;
        } else if (v < 128) {
            int t = v - 64;
            r = t * 3;
            g = 64 + t * 2;
            b = 224 - t;
        } else if (v < 192) {
            int t = v - 128;
            r = 192 + t;
            g = 192 - t / 2;
            b = 64 - t / 2;
        } else {
            int t = v - 192;
            r = 255;
            g = 160 + t;
            b = t * 3;
        }
        return Color.rgb(clamp(r), clamp(g), clamp(b));
    }

    private int clamp(int value) {
        return Math.max(0, Math.min(255, value));
    }

    private boolean requestUsbPermissionAndWait(UsbManager manager, UsbDevice device) {
        CountDownLatch latch = new CountDownLatch(1);
        boolean[] granted = new boolean[]{manager.hasPermission(device)};
        if (granted[0]) {
            return true;
        }
        BroadcastReceiver receiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                if (ACTION_USB_PERMISSION.equals(intent.getAction())) {
                    boolean wasGranted = intent.getBooleanExtra(
                            UsbManager.EXTRA_PERMISSION_GRANTED, false);
                    UsbDevice received = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
                    append("usbPermissionBroadcast granted=" + wasGranted +
                            " device=" + describeObject(received));
                    granted[0] = wasGranted;
                    latch.countDown();
                }
            }
        };
        try {
            IntentFilter filter = new IntentFilter(ACTION_USB_PERMISSION);
            if (Build.VERSION.SDK_INT >= 33) {
                registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED);
            } else {
                registerReceiver(receiver, filter);
            }
            Intent intent = new Intent(ACTION_USB_PERMISSION).setPackage(getPackageName());
            int flags = PendingIntent.FLAG_UPDATE_CURRENT;
            if (Build.VERSION.SDK_INT >= 31) {
                flags |= PendingIntent.FLAG_MUTABLE;
            }
            PendingIntent pending = PendingIntent.getBroadcast(this, 0, intent, flags);
            manager.requestPermission(device, pending);
            if (!latch.await(30, TimeUnit.SECONDS)) {
                append("usbPermissionBroadcast timeout");
            }
        } catch (Throwable t) {
            append("requestUsbPermission FAIL " + formatThrowable(t));
        } finally {
            try {
                unregisterReceiver(receiver);
            } catch (Throwable ignored) {
                // Receiver may not be registered if requestPermission failed early.
            }
        }
        return granted[0] || manager.hasPermission(device);
    }

    private void attemptHiddenUsbGrant(UsbManager manager, UsbDevice device) {
        for (Method method : UsbManager.class.getDeclaredMethods()) {
            String lower = method.getName().toLowerCase(Locale.US);
            if (!lower.contains("grant") && !lower.contains("permission")) {
                continue;
            }
            try {
                method.setAccessible(true);
                Class<?>[] types = method.getParameterTypes();
                Object result;
                if (types.length == 2 && types[0] == UsbDevice.class &&
                        types[1] == String.class) {
                    result = method.invoke(manager, device, getPackageName());
                } else if (types.length == 2 && types[0] == UsbDevice.class &&
                        types[1] == int.class) {
                    result = method.invoke(manager, device, Process.myUid());
                } else {
                    append("hiddenUsbGrant skip " + describeMethod(method));
                    continue;
                }
                append("hiddenUsbGrant " + describeMethod(method) +
                        " OK result=" + describeObject(result));
            } catch (Throwable t) {
                append("hiddenUsbGrant " + describeMethod(method) +
                        " FAIL " + formatThrowable(t));
            }
        }
    }

    private int extractNativeLibraries(String apkPath, File libDir) throws IOException {
        int count = 0;
        try (ZipFile zip = new ZipFile(apkPath)) {
            for (ZipEntry entry : Collections.list(zip.entries())) {
                String name = entry.getName();
                if (!name.startsWith("lib/arm64-v8a/") || !name.endsWith(".so")) {
                    continue;
                }
                File out = new File(libDir, new File(name).getName());
                try (InputStream input = zip.getInputStream(entry);
                     FileOutputStream output = new FileOutputStream(out)) {
                    byte[] buffer = new byte[64 * 1024];
                    int read;
                    while ((read = input.read(buffer)) > 0) {
                        output.write(buffer, 0, read);
                    }
                }
                //noinspection ResultOfMethodCallIgnored
                out.setReadable(true, true);
                //noinspection ResultOfMethodCallIgnored
                out.setExecutable(true, true);
                count++;
            }
        }
        return count;
    }

    private synchronized void startHttpServer() {
        if (httpServerRunning) {
            append("HTTP server already running " + getHttpUrls());
            return;
        }
        httpServerRunning = true;
        Thread thread = new Thread(() -> {
            try (ServerSocket server = new ServerSocket(HTTP_PORT)) {
                httpServerSocket = server;
                append("HTTP server listening " + getHttpUrls());
                while (httpServerRunning) {
                    Socket socket = server.accept();
                    new Thread(() -> handleHttpClient(socket), "thermal-http-client").start();
                }
            } catch (Throwable t) {
                if (httpServerRunning) {
                    append("HTTP server FAIL " + formatThrowable(t));
                }
            } finally {
                httpServerRunning = false;
                httpServerSocket = null;
                append("HTTP server stopped");
            }
        }, "thermal-http-server");
        thread.start();
    }

    private synchronized void stopHttpServer() {
        httpServerRunning = false;
        ServerSocket server = httpServerSocket;
        if (server != null) {
            try {
                server.close();
            } catch (IOException ignored) {
                // Closing best-effort only; app shutdown can continue.
            }
        }
    }

    private void handleHttpClient(Socket socket) {
        try (Socket client = socket;
             BufferedReader reader = new BufferedReader(
                     new InputStreamReader(client.getInputStream()));
             OutputStream output = client.getOutputStream()) {
            String requestLine = reader.readLine();
            if (requestLine == null) {
                return;
            }
            String line;
            while ((line = reader.readLine()) != null && line.length() > 0) {
                // Drain headers; this tiny debug server only uses the path.
            }
            String[] parts = requestLine.split(" ");
            if (parts.length < 2) {
                writeHttpText(output, 400, "Bad Request", "bad request\n", "text/plain");
                return;
            }
            String path = parts[1];
            int queryIndex = path.indexOf('?');
            if (queryIndex >= 0) {
                path = path.substring(0, queryIndex);
            }
            if ("/".equals(path)) {
                writeHttpText(output, 200, "OK", httpIndexHtml(), "text/html; charset=utf-8");
            } else if ("/status".equals(path)) {
                writeHttpText(output, 200, "OK", httpStatusJson(), "application/json");
            } else if ("/log".equals(path)) {
                writeHttpText(output, 200, "OK", currentLogText(), "text/plain; charset=utf-8");
            } else if ("/start-engine".equals(path)) {
                runOnUiThread(this::startEngineProbe);
                writeHttpText(output, 202, "Accepted", "engine probe started\n", "text/plain");
            } else if ("/start-native-auto".equals(path)) {
                runOnUiThread(this::startNativeAutoTest);
                writeHttpText(output, 202, "Accepted", "native auto started\n", "text/plain");
            } else if ("/stop".equals(path)) {
                runOnUiThread(this::stopSdkLive);
                writeHttpText(output, 202, "Accepted", "stop requested\n", "text/plain");
            } else if ("/latest.raw".equals(path)) {
                writeLatestRaw(output);
            } else if ("/latest.pgm".equals(path)) {
                writeLatestPgm(output);
            } else if ("/latest.png".equals(path)) {
                writeLatestPng(output);
            } else {
                writeHttpText(output, 404, "Not Found", "not found\n", "text/plain");
            }
        } catch (Throwable t) {
            append("HTTP client FAIL " + formatThrowable(t));
        }
    }

    private String httpIndexHtml() {
        return "<!doctype html><html><head><meta charset=\"utf-8\">" +
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">" +
                "<title>Thermal Live Debug</title></head><body>" +
                "<h1>Thermal Live Debug</h1>" +
                "<p>Status: <a href=\"/status\">/status</a></p>" +
                "<p>Log: <a href=\"/log\">/log</a></p>" +
                "<p>Latest frame: <a href=\"/latest.raw\">raw</a> " +
                "<a href=\"/latest.pgm\">pgm</a> " +
                "<a href=\"/latest.png\">png</a></p>" +
                "<p><a href=\"/start-engine\">Start Engine Probe</a></p>" +
                "<p><a href=\"/start-native-auto\">Start Native Auto</a></p>" +
                "<p><a href=\"/stop\">Stop</a></p>" +
                "<pre>" + htmlEscape(currentLogText()) + "</pre>" +
                "</body></html>";
    }

    private String httpStatusJson() {
        byte[] frame = latestThermalFrame;
        return "{" +
                "\"running\":" + running + "," +
                "\"httpPort\":" + HTTP_PORT + "," +
                "\"latestFrameBytes\":" + (frame == null ? 0 : frame.length) + "," +
                "\"latestFrameAgeMs\":" + latestFrameAgeMs() + "," +
                "\"latestLabel\":\"" + jsonEscape(latestThermalLabel) + "\"," +
                "\"logFile\":\"" + jsonEscape(logFile == null ? "" : logFile.getAbsolutePath()) + "\"" +
                "}\n";
    }

    private long latestFrameAgeMs() {
        long at = latestThermalFrameAt;
        if (at <= 0) {
            return -1;
        }
        return Math.max(0, System.currentTimeMillis() - at);
    }

    private String currentLogText() {
        synchronized (logBuffer) {
            return logBuffer.toString();
        }
    }

    private void writeLatestRaw(OutputStream output) throws IOException {
        byte[] frame = latestThermalFrame;
        if (frame == null) {
            writeHttpText(output, 404, "Not Found", "no frame\n", "text/plain");
            return;
        }
        writeHttpBytes(output, 200, "OK", "application/octet-stream", frame);
    }

    private void writeLatestPgm(OutputStream output) throws IOException {
        byte[] frame = latestThermalFrame;
        if (frame == null) {
            writeHttpText(output, 404, "Not Found", "no frame\n", "text/plain");
            return;
        }
        ByteArrayOutputStream body = new ByteArrayOutputStream();
        String header = "P5\n" + THERMAL_WIDTH + " " + THERMAL_HEIGHT + "\n65535\n";
        body.write(header.getBytes("US-ASCII"));
        for (int i = 0; i < THERMAL_U16_BYTES; i += 2) {
            int value = (frame[i] & 0xff) | ((frame[i + 1] & 0xff) << 8);
            body.write((value >> 8) & 0xff);
            body.write(value & 0xff);
        }
        writeHttpBytes(output, 200, "OK", "image/x-portable-graymap", body.toByteArray());
    }

    private void writeLatestPng(OutputStream output) throws IOException {
        byte[] frame = latestThermalFrame;
        if (frame == null) {
            writeHttpText(output, 404, "Not Found", "no frame\n", "text/plain");
            return;
        }
        Bitmap bitmap = thermalBitmapFromFrame(frame);
        ByteArrayOutputStream body = new ByteArrayOutputStream();
        bitmap.compress(Bitmap.CompressFormat.PNG, 100, body);
        writeHttpBytes(output, 200, "OK", "image/png", body.toByteArray());
    }

    private Bitmap thermalBitmapFromFrame(byte[] frame) {
        int count = THERMAL_WIDTH * THERMAL_HEIGHT;
        int[] values = new int[count];
        int min = Integer.MAX_VALUE;
        int max = Integer.MIN_VALUE;
        for (int i = 0; i < count; i++) {
            int byteIndex = i * 2;
            int value = (frame[byteIndex] & 0xff) | ((frame[byteIndex + 1] & 0xff) << 8);
            values[i] = value;
            min = Math.min(min, value);
            max = Math.max(max, value);
        }
        int range = Math.max(1, max - min);
        int[] colors = new int[count];
        for (int i = 0; i < count; i++) {
            colors[i] = heatColor((values[i] - min) * 255 / range);
        }
        return Bitmap.createBitmap(colors, THERMAL_WIDTH, THERMAL_HEIGHT, Bitmap.Config.ARGB_8888);
    }

    private void writeHttpText(
            OutputStream output,
            int code,
            String reason,
            String body,
            String contentType) throws IOException {
        writeHttpBytes(output, code, reason, contentType, body.getBytes("UTF-8"));
    }

    private void writeHttpBytes(
            OutputStream output,
            int code,
            String reason,
            String contentType,
            byte[] body) throws IOException {
        String header = "HTTP/1.1 " + code + " " + reason + "\r\n" +
                "Content-Type: " + contentType + "\r\n" +
                "Content-Length: " + body.length + "\r\n" +
                "Access-Control-Allow-Origin: *\r\n" +
                "Connection: close\r\n\r\n";
        output.write(header.getBytes("US-ASCII"));
        output.write(body);
        output.flush();
    }

    private String getHttpUrls() {
        List<String> urls = new ArrayList<>();
        try {
            Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
            while (interfaces.hasMoreElements()) {
                NetworkInterface networkInterface = interfaces.nextElement();
                if (!networkInterface.isUp() || networkInterface.isLoopback()) {
                    continue;
                }
                Enumeration<InetAddress> addresses = networkInterface.getInetAddresses();
                while (addresses.hasMoreElements()) {
                    InetAddress address = addresses.nextElement();
                    if (address instanceof Inet4Address && !address.isLoopbackAddress()) {
                        urls.add("http://" + address.getHostAddress() + ":" + HTTP_PORT + "/");
                    }
                }
            }
        } catch (Throwable t) {
            append("HTTP url enumerate FAIL " + formatThrowable(t));
        }
        if (urls.isEmpty()) {
            urls.add("http://PHONE_IP:" + HTTP_PORT + "/");
        }
        return urls.toString();
    }

    private String htmlEscape(String value) {
        if (value == null) {
            return "";
        }
        return value.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;");
    }

    private String jsonEscape(String value) {
        if (value == null) {
            return "";
        }
        return value.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r");
    }

    private void shareLog() {
        Intent intent = new Intent(Intent.ACTION_SEND);
        intent.setType("text/plain");
        intent.putExtra(Intent.EXTRA_SUBJECT, "Thermal Live Debug log");
        intent.putExtra(Intent.EXTRA_TEXT, logBuffer.toString());
        startActivity(Intent.createChooser(intent, "Share Thermal Live Debug log"));
    }

    private void startThread(Runnable runnable) {
        new Thread(() -> {
            try {
                runnable.run();
            } catch (Throwable t) {
                append("thread FAIL " + formatThrowable(t));
            }
        }, "thermal-live-debug").start();
    }

    private Object invoke(Object target, String name) throws Exception {
        return invoke(target, name, new Class[0]);
    }

    private Object invoke(Object target, String name, Class<?>[] parameterTypes, Object... args)
            throws Exception {
        Method method = target.getClass().getMethod(name, parameterTypes);
        return method.invoke(target, args);
    }

    private Object tryInvoke(Object target, String name) {
        try {
            Object result = invoke(target, name);
            append(name + " OK result=" + describeObject(result));
            return result;
        } catch (Throwable t) {
            append(name + " FAIL " + formatThrowable(t));
            return null;
        }
    }

    private Object tryInvokeQuiet(Object target, String name) {
        try {
            return invoke(target, name);
        } catch (Throwable t) {
            return null;
        }
    }

    private Object tryInvokeBoolean(Object target, String name, boolean value) {
        try {
            Object result = invoke(target, name, new Class[]{boolean.class}, value);
            append(name + "(" + value + ") OK result=" + describeObject(result));
            return result;
        } catch (Throwable t) {
            append(name + "(" + value + ") FAIL " + formatThrowable(t));
            return null;
        }
    }

    private Object invokeWithLog(
            String label,
            Object target,
            String name,
            Class<?>[] parameterTypes,
            Object... args) {
        try {
            Object result = invoke(target, name, parameterTypes, args);
            append(label + " OK result=" + describeObject(result));
            return result;
        } catch (Throwable t) {
            append(label + " FAIL " + formatThrowable(t));
            return null;
        }
    }

    @SuppressWarnings({"rawtypes", "unchecked"})
    private Object enumValue(Class<?> enumClass, String value) {
        return Enum.valueOf((Class<? extends Enum>) enumClass.asSubclass(Enum.class), value);
    }

    private String enumName(Object mode) {
        if (mode instanceof Enum) {
            return ((Enum<?>) mode).name();
        }
        return String.valueOf(mode);
    }

    private void recreateDir(File dir) throws IOException {
        deleteRecursively(dir);
        if (!dir.mkdirs() && !dir.isDirectory()) {
            throw new IOException("mkdir failed: " + dir);
        }
    }

    private void deleteRecursively(File file) throws IOException {
        if (!file.exists()) {
            return;
        }
        if (file.isDirectory()) {
            File[] children = file.listFiles();
            if (children != null) {
                for (File child : children) {
                    deleteRecursively(child);
                }
            }
        }
        if (!file.delete()) {
            throw new IOException("delete failed: " + file);
        }
    }

    private void setStaticField(Class<?> owner, String name, Object value) throws Exception {
        Field field = owner.getDeclaredField(name);
        field.setAccessible(true);
        field.set(null, value);
    }

    private void setField(Class<?> owner, Object target, String name, Object value) throws Exception {
        Field field = owner.getDeclaredField(name);
        field.setAccessible(true);
        field.set(target, value);
    }

    private void trySetField(Class<?> owner, Object target, String name, Object value) {
        try {
            setField(owner, target, name, value);
            append("trySetField OK " + owner.getName() + "." + name +
                    "=" + describeObject(value));
        } catch (Throwable t) {
            append("trySetField skip " + owner.getName() + "." + name +
                    " " + formatThrowable(t));
        }
    }

    private boolean isThermalDevice(UsbDevice device) {
        return (device.getVendorId() == THERMAL_VENDOR_ID &&
                device.getProductId() == THERMAL_PRODUCT_ID) ||
                (device.getVendorId() == ALT_THERMAL_VENDOR_ID &&
                        device.getProductId() == ALT_THERMAL_PRODUCT_ID);
    }

    private String describeUsbDevice(UsbDevice device) {
        if (device == null) {
            return "null";
        }
        return device.getDeviceName() +
                " vendor=0x" + Integer.toHexString(device.getVendorId()) +
                " product=0x" + Integer.toHexString(device.getProductId()) +
                " class=" + device.getDeviceClass() +
                " interfaces=" + device.getInterfaceCount();
    }

    private String describeUsbEndpoint(UsbEndpoint endpoint) {
        if (endpoint == null) {
            return "null";
        }
        return "address=0x" + Integer.toHexString(endpoint.getAddress()) +
                " number=" + endpoint.getEndpointNumber() +
                " direction=" + endpointDirectionName(endpoint.getDirection()) +
                " type=" + endpointTypeName(endpoint.getType()) +
                " maxPacket=" + endpoint.getMaxPacketSize() +
                " interval=" + endpoint.getInterval();
    }

    private String endpointDirectionName(int direction) {
        if (direction == UsbConstants.USB_DIR_IN) {
            return "IN";
        }
        if (direction == UsbConstants.USB_DIR_OUT) {
            return "OUT";
        }
        return "0x" + Integer.toHexString(direction);
    }

    private String endpointTypeName(int type) {
        if (type == UsbConstants.USB_ENDPOINT_XFER_CONTROL) {
            return "CONTROL";
        }
        if (type == UsbConstants.USB_ENDPOINT_XFER_ISOC) {
            return "ISO";
        }
        if (type == UsbConstants.USB_ENDPOINT_XFER_BULK) {
            return "BULK";
        }
        if (type == UsbConstants.USB_ENDPOINT_XFER_INT) {
            return "INT";
        }
        return "0x" + Integer.toHexString(type);
    }

    private String describeObject(Object object) {
        if (object == null) {
            return "null";
        }
        if (object instanceof byte[]) {
            return describeBytes((byte[]) object);
        }
        if (object instanceof short[]) {
            return "short[]{len=" + ((short[]) object).length + "}";
        }
        if (object instanceof int[]) {
            return "int[]{len=" + ((int[]) object).length + "}";
        }
        if (object instanceof float[]) {
            return "float[]{len=" + ((float[]) object).length + "}";
        }
        return object.getClass().getName() + ":" + object;
    }

    private String describeProbeValue(Object object) {
        if (object == null) {
            return "null";
        }
        if (object instanceof byte[] ||
                object instanceof short[] ||
                object instanceof int[] ||
                object instanceof float[]) {
            return describeObject(object);
        }
        if (isPrimitiveOrBoxed(object.getClass()) || object instanceof String) {
            return String.valueOf(object);
        }
        return object.getClass().getName() + "@" +
                Integer.toHexString(System.identityHashCode(object));
    }

    private boolean isPrimitiveOrBoxed(Class<?> type) {
        return type.isPrimitive() ||
                type == Boolean.class ||
                type == Byte.class ||
                type == Short.class ||
                type == Integer.class ||
                type == Long.class ||
                type == Float.class ||
                type == Double.class ||
                type == Character.class;
    }

    private String describeArgs(Object[] args) {
        if (args == null || args.length == 0) {
            return "[]";
        }
        StringBuilder builder = new StringBuilder("[");
        for (int i = 0; i < args.length; i++) {
            if (i > 0) {
                builder.append(", ");
            }
            Object arg = args[i];
            if (arg instanceof byte[]) {
                builder.append("byte[]{").append(describeBytes((byte[]) arg)).append('}');
            } else if (arg instanceof int[]) {
                builder.append("int[]{len=").append(((int[]) arg).length).append('}');
            } else if (arg instanceof short[]) {
                builder.append("short[]{len=").append(((short[]) arg).length).append('}');
            } else if (arg instanceof float[]) {
                builder.append("float[]{len=").append(((float[]) arg).length).append('}');
            } else if (arg instanceof double[]) {
                builder.append("double[]{len=").append(((double[]) arg).length).append('}');
            } else {
                builder.append(describeObject(arg));
            }
        }
        return builder.append(']').toString();
    }

    private byte[] findRenderableFrameArg(Object[] args) {
        if (args == null) {
            return null;
        }
        for (Object arg : args) {
            byte[] frame = chooseRenderableFrameFromValue(arg);
            if (frame != null) {
                return frame;
            }
        }
        return null;
    }

    private Object defaultReturnValue(Class<?> type) {
        if (type == Void.TYPE) {
            return null;
        }
        if (type == Boolean.TYPE) {
            return false;
        }
        if (type == Byte.TYPE) {
            return (byte) 0;
        }
        if (type == Short.TYPE) {
            return (short) 0;
        }
        if (type == Integer.TYPE) {
            return 0;
        }
        if (type == Long.TYPE) {
            return 0L;
        }
        if (type == Float.TYPE) {
            return 0.0f;
        }
        if (type == Double.TYPE) {
            return 0.0d;
        }
        if (type == Character.TYPE) {
            return (char) 0;
        }
        return null;
    }

    private String describeBytes(byte[] bytes) {
        if (bytes == null) {
            return "null";
        }
        int checksum = 0;
        int limit = Math.min(bytes.length, 1024);
        for (int i = 0; i < limit; i++) {
            checksum = (checksum + (bytes[i] & 0xff)) & 0xffff;
        }
        return "len=" + bytes.length + " checksum1024=0x" + Integer.toHexString(checksum);
    }

    private String checksumHex(byte[] bytes, int length) {
        if (bytes == null) {
            return "null";
        }
        int checksum = 0;
        int limit = Math.max(0, Math.min(length, bytes.length));
        for (int i = 0; i < limit; i++) {
            checksum = (checksum + (bytes[i] & 0xff)) & 0xffff;
        }
        return "0x" + Integer.toHexString(checksum);
    }

    private String hexPrefix(byte[] bytes, int length) {
        if (bytes == null) {
            return "null";
        }
        int limit = Math.max(0, Math.min(length, bytes.length));
        StringBuilder builder = new StringBuilder();
        for (int i = 0; i < limit; i++) {
            if (i > 0) {
                builder.append(' ');
            }
            int value = bytes[i] & 0xff;
            if (value < 16) {
                builder.append('0');
            }
            builder.append(Integer.toHexString(value));
        }
        if (limit < bytes.length) {
            builder.append(" ...");
        }
        return builder.toString();
    }

    private String describeMethod(Method method) {
        StringBuilder builder = new StringBuilder(method.getReturnType().getName())
                .append(' ')
                .append(method.getName())
                .append('(');
        Class<?>[] types = method.getParameterTypes();
        for (int i = 0; i < types.length; i++) {
            if (i > 0) {
                builder.append(',');
            }
            builder.append(types[i].getName());
        }
        return builder.append(')').toString();
    }

    private String describeConstructor(java.lang.reflect.Constructor<?> constructor) {
        StringBuilder builder = new StringBuilder(constructor.getName()).append('(');
        Class<?>[] types = constructor.getParameterTypes();
        for (int i = 0; i < types.length; i++) {
            if (i > 0) {
                builder.append(',');
            }
            builder.append(types[i].getName());
        }
        return builder.append(')').toString();
    }

    private String describeField(Field field) {
        return field.getType().getName() + " " + field.getName();
    }

    private String formatThrowable(Throwable throwable) {
        Throwable t = throwable;
        if (t instanceof InvocationTargetException &&
                ((InvocationTargetException) t).getTargetException() != null) {
            t = ((InvocationTargetException) t).getTargetException();
        }
        StringBuilder builder = new StringBuilder();
        int depth = 0;
        while (t != null && depth < 6) {
            if (depth > 0) {
                builder.append(" causedBy ");
            }
            builder.append(t.getClass().getName()).append(": ").append(t.getMessage());
            StackTraceElement[] stack = t.getStackTrace();
            if (stack != null && stack.length > 0) {
                builder.append(" at ").append(stack[0]);
            }
            t = t.getCause();
            depth++;
        }
        return builder.toString();
    }

    private long getLongVersionCode(PackageInfo info) {
        if (Build.VERSION.SDK_INT >= 28) {
            return info.getLongVersionCode();
        }
        return info.versionCode;
    }

    private void setStatus(String status) {
        runOnUiThread(() -> statusText.setText(status));
    }

    private void append(String line) {
        String stamped = new SimpleDateFormat("HH:mm:ss.SSS", Locale.US).format(new Date()) +
                " " + line;
        Log.i(TAG, stamped);
        synchronized (logBuffer) {
            logBuffer.append(stamped).append('\n');
            if (logBuffer.length() > 60000) {
                logBuffer.delete(0, logBuffer.length() - 60000);
            }
        }
        runOnUiThread(() -> {
            logText.setText(logBuffer.toString());
            logScroll.post(() -> logScroll.fullScroll(View.FOCUS_DOWN));
        });
        if (logFile != null) {
            try (FileWriter writer = new FileWriter(logFile, true)) {
                writer.write(stamped);
                writer.write('\n');
            } catch (IOException e) {
                Log.e(TAG, "log write failed", e);
            }
        }
    }

    private void sleepMs(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private String stamp() {
        return new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date());
    }

    private static final class ThermalPreviewView extends View {
        private final Paint paint = new Paint(Paint.FILTER_BITMAP_FLAG);
        private final Paint textPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private Bitmap bitmap;

        private ThermalPreviewView(Context context) {
            super(context);
            textPaint.setColor(Color.rgb(210, 210, 210));
            textPaint.setTextSize(32.0f);
            textPaint.setTextAlign(Paint.Align.CENTER);
            setBackgroundColor(Color.BLACK);
        }

        private void setBitmap(Bitmap bitmap) {
            this.bitmap = bitmap;
            invalidate();
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            if (bitmap == null) {
                canvas.drawText(
                        "No thermal frame",
                        getWidth() / 2.0f,
                        getHeight() / 2.0f,
                        textPaint);
                return;
            }
            float scale = Math.min(
                    getWidth() / (float) bitmap.getWidth(),
                    getHeight() / (float) bitmap.getHeight());
            float drawWidth = bitmap.getWidth() * scale;
            float drawHeight = bitmap.getHeight() * scale;
            float left = (getWidth() - drawWidth) / 2.0f;
            float top = (getHeight() - drawHeight) / 2.0f;
            canvas.drawBitmap(
                    bitmap,
                    null,
                    new android.graphics.RectF(left, top, left + drawWidth, top + drawHeight),
                    paint);
        }
    }
}
