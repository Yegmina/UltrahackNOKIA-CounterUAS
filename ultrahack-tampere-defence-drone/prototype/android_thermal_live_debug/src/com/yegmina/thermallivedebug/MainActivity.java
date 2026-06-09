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
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbManager;
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

import java.io.File;
import java.io.FileOutputStream;
import java.io.FileWriter;
import java.io.IOException;
import java.io.InputStream;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

import dalvik.system.DexClassLoader;

public class MainActivity extends Activity {
    private static final String TAG = "ThermalLiveDebug";
    private static final String THERMOVUE_PACKAGE = "com.energy.tc2c";
    private static final String ACTION_USB_PERMISSION =
            "com.yegmina.thermallivedebug.USB_PERMISSION";
    private static final int THERMAL_VENDOR_ID = 0x3474;
    private static final int THERMAL_PRODUCT_ID = 0x4321;
    private static final int THERMAL_WIDTH = 256;
    private static final int THERMAL_HEIGHT = 192;
    private static final int THERMAL_U16_BYTES = THERMAL_WIDTH * THERMAL_HEIGHT * 2;
    private static final int THERMAL_PACKET_TEMP_OFFSET = THERMAL_U16_BYTES + 1024;
    private static final int REQUEST_PERMISSIONS = 41;
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
        append("uid=" + Process.myUid() + " package=" + getPackageName());
        append("device=" + Build.MANUFACTURER + " " + Build.MODEL +
                " sdk=" + Build.VERSION.SDK_INT);
        requestAppPermissions();
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
        buttons.addView(button("Dump Classes", view -> startThread(this::dumpClassesOnly)));
        buttons.addView(button("Request USB", view -> requestThermalUsb()));
        buttons.addView(button("Power Try", view -> startThread(this::tryPowerThermal)));
        buttons.addView(button("Launch TVue", view -> launchThermoVue()));
        buttons.addView(button("Start SDK", view -> startSdkLive()));
        buttons.addView(button("Stop", view -> stopSdkLive()));
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

    private void requestAppPermissions() {
        List<String> missing = new ArrayList<>();
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.CAMERA);
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.RECORD_AUDIO);
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
                " audio=" + hasPermission(Manifest.permission.RECORD_AUDIO));
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
                if (isThermal) {
                    thermal = device;
                }
            }
        } catch (Throwable t) {
            append("usb scan FAIL " + formatThrowable(t));
        }
        return thermal;
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

    private void startSdkLive() {
        if (running) {
            append("SDK live already running");
            return;
        }
        running = true;
        setStatus("starting SDK live");
        startThread(this::runSdkLive);
    }

    private void stopSdkLive() {
        running = false;
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

            Class<?> usbMonitorManagerClass = Class.forName(
                    "com.energy.dualmodule.sdk.uvc.USBMonitorManager", true, loader);
            usbMonitorManager = usbMonitorManagerClass.getMethod("getInstance").invoke(null);
            liveUsbMonitorManager = usbMonitorManager;
            tryInvoke(usbMonitorManager, "init");
            tryInvoke(usbMonitorManager, "registerMonitor");

            Class<?> deviceControlManagerClass = Class.forName(
                    "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualDeviceControlManager",
                    true,
                    loader);
            deviceControlManager = deviceControlManagerClass.getMethod("getInstance").invoke(null);
            liveDeviceControlManager = deviceControlManager;
            tryInvoke(deviceControlManager, "init");

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
                            now - sdkStartedAt > 8000) {
                        monitorClosedToStopDialogLoop = true;
                        fallbackAction = true;
                        setStatus("closing USB monitor after timeout");
                        append("fallback: closing USB monitor after 8s without frames to stop repeated permission dialogs");
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
            dumpImportantClasses(getThermoVueClassLoader(), true);
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
                    if (count >= 24) {
                        append("classMethod " + name + " truncated");
                        break;
                    }
                }
            } catch (Throwable t) {
                append("classLoad FAIL " + name + " " + formatThrowable(t));
            }
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

    private void renderThermal(byte[] frame, String label) {
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

    @SuppressWarnings({"rawtypes", "unchecked"})
    private Object enumValue(Class<?> enumClass, String value) {
        return Enum.valueOf((Class<? extends Enum>) enumClass.asSubclass(Enum.class), value);
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

    private boolean isThermalDevice(UsbDevice device) {
        return device.getVendorId() == THERMAL_VENDOR_ID &&
                device.getProductId() == THERMAL_PRODUCT_ID;
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

    private String describeObject(Object object) {
        if (object == null) {
            return "null";
        }
        if (object instanceof byte[]) {
            return describeBytes((byte[]) object);
        }
        return object.getClass().getName() + ":" + object;
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
