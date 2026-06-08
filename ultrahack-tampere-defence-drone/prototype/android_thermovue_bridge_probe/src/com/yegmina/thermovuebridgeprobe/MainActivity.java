package com.yegmina.thermovuebridgeprobe;

import android.Manifest;
import android.app.Activity;
import android.app.PendingIntent;
import android.content.ComponentName;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.ContextWrapper;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.content.pm.PackageInfo;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Process;
import android.util.Log;
import android.view.Gravity;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.File;
import java.io.FileOutputStream;
import java.io.FileWriter;
import java.io.IOException;
import java.io.InputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
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
    private static final String TAG = "ThermoVueBridgeProbe";
    private static final String THERMOVUE_PACKAGE = "com.energy.tc2c";
    private static final String ACTION_USB_PERMISSION =
            "com.yegmina.thermovuebridgeprobe.USB_PERMISSION";
    private static final int THERMAL_VENDOR_ID = 0x3474;
    private static final int THERMAL_PRODUCT_ID = 0x4321;
    private static final int REQUEST_APP_PERMISSIONS = 42;
    private static final String TINY2C_USB_MODE_PATH =
            "/sys/devices/platform/yft_tiny2c_usb/tiny2c_usb_mode";
    private static final String TINY2C_EXTCON_MODE_PATH =
            "/sys/class/yft_extcon/tiny2c_mode";

    private TextView text;
    private File runDir;
    private File logFile;
    private boolean probeStarted;
    private boolean manualMode;
    private boolean privilegedMode;
    private boolean watchUsbMode;
    private boolean keepStreaming;
    private String jetsonHost;
    private int jetsonPort;
    private int udpMaxFrames;
    private int streamSeconds;
    private boolean dumpedRawTemp;
    private boolean dumpedRemapTemp;
    private int udpSentFrames;
    private DexClassLoader thermoVueLoader;
    private boolean loggedUsbManagerPermissionMethods;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        manualMode = getIntent().getBooleanExtra("manual", false);
        privilegedMode = getIntent().getBooleanExtra("privileged", false);
        watchUsbMode = getIntent().getBooleanExtra("watchUsb", false);
        keepStreaming = getIntent().getBooleanExtra("keepStreaming", false);
        jetsonHost = getIntent().getStringExtra("jetsonHost");
        jetsonPort = getIntent().getIntExtra("jetsonPort", 25000);
        udpMaxFrames = getIntent().getIntExtra("udpMaxFrames", 25);
        streamSeconds = getIntent().getIntExtra("streamSeconds", 3600);

        text = new TextView(this);
        text.setGravity(Gravity.START);
        text.setTextSize(12);
        text.setPadding(20, 20, 20, 20);
        text.setText("ThermoVue bridge probe starting...\n");

        ScrollView scrollView = new ScrollView(this);
        scrollView.addView(text);

        LinearLayout controls = new LinearLayout(this);
        controls.setOrientation(LinearLayout.HORIZONTAL);
        controls.setPadding(8, 120, 8, 0);

        Button launchButton = new Button(this);
        launchButton.setText("Launch ThermoVue");
        launchButton.setOnClickListener(view -> launchThermoVue());
        controls.addView(launchButton, new LinearLayout.LayoutParams(0, -2, 1));

        Button usbButton = new Button(this);
        usbButton.setText("Request USB");
        usbButton.setOnClickListener(view -> requestThermalUsbFromButton());
        controls.addView(usbButton, new LinearLayout.LayoutParams(0, -2, 1));

        Button probeButton = new Button(this);
        probeButton.setText("Run Probe");
        probeButton.setOnClickListener(view -> startProbeThread());
        controls.addView(probeButton, new LinearLayout.LayoutParams(0, -2, 1));

        Button privilegedButton = new Button(this);
        privilegedButton.setText("Privileged");
        privilegedButton.setOnClickListener(view -> startPrivilegedBridgeThread());
        controls.addView(privilegedButton, new LinearLayout.LayoutParams(0, -2, 1));

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.addView(controls, new LinearLayout.LayoutParams(-1, -2));
        root.addView(scrollView, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);

        runDir = new File(getExternalFilesDir(null), "bridge_probe_" + stamp());
        //noinspection ResultOfMethodCallIgnored
        runDir.mkdirs();
        logFile = new File(runDir, "thermovue_bridge_probe.log");

        append("logFile=" + logFile.getAbsolutePath());
        append("manualMode=" + manualMode);
        append("privilegedMode=" + privilegedMode);
        append("watchUsbMode=" + watchUsbMode);
        append("keepStreaming=" + keepStreaming + " streamSeconds=" + streamSeconds);
        append("jetsonUdp=" + (jetsonHost == null ? "disabled" : jetsonHost + ":" + jetsonPort));
        append("udpMaxFrames=" + (udpMaxFrames <= 0 ? "unlimited" : String.valueOf(udpMaxFrames)));
        inspectUsbAttachIntent("onCreate", getIntent());
        boolean usbAttachLaunch = isUsbAttachIntent(getIntent());
        if (usbAttachLaunch && !manualMode && !privilegedMode && !watchUsbMode) {
            append("usb attach launch: moving task to back before fast bridge path");
            moveTaskToBack(true);
        }
        if (hasAppPermissions()) {
            if (privilegedMode) {
                startPrivilegedBridgeThread();
            } else if (watchUsbMode) {
                startUsbGrantWatchThread();
            } else if (manualMode) {
                append("manual controls ready; launch ThermoVue, request USB, then run probe");
            } else if (usbAttachLaunch) {
                startUsbAttachBridgeThread();
            } else {
                startProbeThread();
            }
        } else {
            append("requesting app CAMERA/RECORD_AUDIO permissions");
            requestPermissions(
                    new String[]{Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO},
                    REQUEST_APP_PERMISSIONS);
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        inspectUsbAttachIntent("onNewIntent", intent);
        if (hasAppPermissions() && isUsbAttachIntent(intent) &&
                !manualMode && !privilegedMode && !watchUsbMode) {
            append("onNewIntent usb attach: starting fast bridge path");
            startUsbAttachBridgeThread();
        }
    }

    @Override
    public void onRequestPermissionsResult(
            int requestCode,
            String[] permissions,
            int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != REQUEST_APP_PERMISSIONS) {
            return;
        }
        append("appPermissionResult camera=" + hasPermission(Manifest.permission.CAMERA) +
                " audio=" + hasPermission(Manifest.permission.RECORD_AUDIO));
        if (privilegedMode) {
            startPrivilegedBridgeThread();
        } else if (watchUsbMode) {
            startUsbGrantWatchThread();
        } else if (manualMode) {
            append("manual controls ready after app permission grant");
        } else if (isUsbAttachIntent(getIntent())) {
            startUsbAttachBridgeThread();
        } else {
            startProbeThread();
        }
    }

    private boolean hasAppPermissions() {
        return hasPermission(Manifest.permission.CAMERA) &&
                hasPermission(Manifest.permission.RECORD_AUDIO);
    }

    private boolean hasPermission(String permission) {
        return checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED;
    }

    private void startProbeThread() {
        if (!hasAppPermissions()) {
            append("probe start delayed: requesting app CAMERA/RECORD_AUDIO permissions");
            requestPermissions(
                    new String[]{Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO},
                    REQUEST_APP_PERMISSIONS);
            return;
        }
        if (probeStarted) {
            append("probe already running");
            return;
        }
        probeStarted = true;
        new Thread(this::runProbe, "thermovue-bridge-probe").start();
    }

    private void startPrivilegedBridgeThread() {
        if (!hasAppPermissions()) {
            append("privileged bridge delayed: requesting app CAMERA/RECORD_AUDIO permissions");
            requestPermissions(
                    new String[]{Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO},
                    REQUEST_APP_PERMISSIONS);
            return;
        }
        if (probeStarted) {
            append("probe already running");
            return;
        }
        probeStarted = true;
        new Thread(this::runPrivilegedBridge, "thermovue-privileged-bridge").start();
    }

    private void startUsbGrantWatchThread() {
        if (!hasAppPermissions()) {
            append("USB grant watch delayed: requesting app CAMERA/RECORD_AUDIO permissions");
            requestPermissions(
                    new String[]{Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO},
                    REQUEST_APP_PERMISSIONS);
            return;
        }
        if (probeStarted) {
            append("probe already running");
            return;
        }
        probeStarted = true;
        new Thread(this::runUsbGrantWatch, "thermovue-usb-grant-watch").start();
    }

    private void startUsbAttachBridgeThread() {
        if (!hasAppPermissions()) {
            append("USB attach bridge delayed: requesting app CAMERA/RECORD_AUDIO permissions");
            requestPermissions(
                    new String[]{Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO},
                    REQUEST_APP_PERMISSIONS);
            return;
        }
        if (probeStarted) {
            append("probe already running");
            return;
        }
        probeStarted = true;
        new Thread(this::runUsbAttachBridge, "thermovue-usb-attach-bridge").start();
    }

    private void requestThermalUsbFromButton() {
        if (!hasAppPermissions()) {
            append("USB request delayed: requesting app CAMERA/RECORD_AUDIO permissions");
            requestPermissions(
                    new String[]{Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO},
                    REQUEST_APP_PERMISSIONS);
            return;
        }
        new Thread(() -> {
            append("===== Manual USB permission request =====");
            powerThermalModuleViaSysfs("manual request");
            powerThermalModuleViaVendor("manual request");
            sleepMs(1500);
            UsbManager usbManager = (UsbManager) getSystemService(USB_SERVICE);
            Map<String, UsbDevice> devices = usbManager.getDeviceList();
            append("manualUsbDeviceCount=" + devices.size());
            boolean found = false;
            for (UsbDevice device : devices.values()) {
                append("manualUsbDevice " + describeUsbDevice(device) +
                        " hasPermission=" + usbManager.hasPermission(device));
                if (!isThermalDevice(device)) {
                    continue;
                }
                found = true;
                attemptHiddenUsbGrant(usbManager, device);
                boolean granted = requestUsbPermissionAndWait(usbManager, device);
                append("manualThermalUsbPermissionAfterRequest=" + granted);
            }
            if (!found) {
                append("manual thermal USB device not found; launch ThermoVue first to power it");
            }
        }, "manual-usb-request").start();
    }

    private void runProbe() {
        append("device=" + Build.MANUFACTURER + " " + Build.MODEL +
                " sdk=" + Build.VERSION.SDK_INT);
        DexClassLoader loader = null;
        try {
            boolean initialThermalReady = probeAndroidUsb("initial");
            loader = getThermoVueClassLoader();
            probeClassLoading(loader);
            initializeMmkv(loader);
            initializeBlankjUtils(loader);
            initializeRxBaseApplication(loader);
            probeVendorUsbMonitor(loader, "initial");
            if (initialThermalReady) {
                probeTiny2cProxy(loader, "initial USB-attached permission path");
                probeTiny2cDeviceControlPath(loader);
            } else {
                append("Tiny2C initial skipped: thermal USB is not ready for this app yet.");
            }

            launchThermoVue();
            sleepMs(7000);

            probeAndroidUsb("after ThermoVue launch");
            probeVendorUsbMonitor(loader, "after ThermoVue launch");
            probeTiny2cProxy(loader, "after ThermoVue launch");
            probeTiny2cDeviceControlPath(loader);
        } catch (Throwable t) {
            append("FATAL " + formatThrowable(t));
        }
        append("probe finished");
    }

    private void runPrivilegedBridge() {
        append("device=" + Build.MANUFACTURER + " " + Build.MODEL +
                " sdk=" + Build.VERSION.SDK_INT);
        append("===== Privileged raw thermal bridge candidate =====");
        try {
            DexClassLoader loader = getThermoVueClassLoader();
            probeClassLoading(loader);
            initializeMmkv(loader);
            initializeBlankjUtils(loader);
            initializeRxBaseApplication(loader);

            attemptSetFixedUsbHandler("privileged bridge");
            powerThermalModuleViaSysfs("privileged bridge");
            powerThermalModuleViaVendor("privileged bridge");
            UsbDevice thermalDevice = waitForThermalUsb(10000);
            if (thermalDevice == null) {
                append("privileged bridge FAIL thermal USB did not appear after power-up");
            } else {
                UsbManager usbManager = (UsbManager) getSystemService(USB_SERVICE);
                append("privileged bridge thermalUsb=" + describeUsbDevice(thermalDevice) +
                        " hasPermissionBeforeGrant=" + usbManager.hasPermission(thermalDevice));
                attemptHiddenUsbGrant(usbManager, thermalDevice);
                append("privileged bridge hasPermissionAfterGrant=" +
                        usbManager.hasPermission(thermalDevice));
            }

            probeAndroidUsb("privileged bridge after power-up");
            probeVendorUsbMonitor(loader, "privileged bridge after power-up");
            probeTiny2cDeviceControlPath(loader);
        } catch (Throwable t) {
            append("PRIVILEGED FATAL " + formatThrowable(t));
        }
        append("privileged bridge finished");
    }

    private void runUsbAttachBridge() {
        append("device=" + Build.MANUFACTURER + " " + Build.MODEL +
                " sdk=" + Build.VERSION.SDK_INT);
        append("===== USB attach fast bridge path =====");
        try {
            DexClassLoader loader = getThermoVueClassLoader();
            probeClassLoading(loader);
            initializeMmkv(loader);
            initializeBlankjUtils(loader);
            initializeRxBaseApplication(loader);
            probeAndroidUsb("USB attach fast path");
            probeVendorUsbMonitor(loader, "USB attach fast path");
            probeTiny2cProxy(loader, "USB attach fast path");
            probeTiny2cDeviceControlPath(loader);
        } catch (Throwable t) {
            append("USB ATTACH FATAL " + formatThrowable(t));
        }
        append("usb attach bridge finished");
    }

    private void runUsbGrantWatch() {
        append("device=" + Build.MANUFACTURER + " " + Build.MODEL +
                " sdk=" + Build.VERSION.SDK_INT);
        append("===== USB permission grant watch path =====");
        try {
            DexClassLoader loader = getThermoVueClassLoader();
            probeClassLoading(loader);
            initializeMmkv(loader);
            initializeBlankjUtils(loader);
            initializeRxBaseApplication(loader);
            UsbDevice thermalDevice = waitForThermalUsbWithPermission(60000);
            if (thermalDevice == null) {
                append("USB grant watch FAIL no permitted thermal device appeared");
            } else {
                append("USB grant watch ready thermalUsb=" + describeUsbDevice(thermalDevice));
                probeAndroidUsb("USB grant watch ready");
                probeTiny2cDeviceControlPath(loader);
            }
        } catch (Throwable t) {
            append("USB GRANT WATCH FATAL " + formatThrowable(t));
        }
        append("usb grant watch finished");
    }

    private synchronized DexClassLoader getThermoVueClassLoader() throws Exception {
        if (thermoVueLoader == null) {
            thermoVueLoader = buildThermoVueClassLoader();
        }
        return thermoVueLoader;
    }

    private void powerThermalModuleViaSysfs(String label) {
        append("===== Direct sysfs power-up " + label + " =====");
        writeSysfsControl(TINY2C_USB_MODE_PATH, "1\n");
        writeSysfsControl(TINY2C_EXTCON_MODE_PATH, "1\n");
    }

    private void writeSysfsControl(String path, String value) {
        try (FileWriter writer = new FileWriter(path)) {
            writer.write(value);
            append("sysfsWrite OK path=" + path + " value=" + value.trim());
        } catch (Throwable t) {
            append("sysfsWrite FAIL path=" + path + " value=" + value.trim() +
                    " " + formatThrowable(t));
        }
    }

    private void attemptSetFixedUsbHandler(String label) {
        append("===== Fixed USB handler " + label + " =====");
        UsbManager manager = (UsbManager) getSystemService(USB_SERVICE);
        ComponentName component = new ComponentName(getPackageName(), MainActivity.class.getName());
        boolean invoked = false;
        for (Method method : UsbManager.class.getDeclaredMethods()) {
            if (!method.getName().equals("setUsbDeviceConnectionHandler")) {
                continue;
            }
            try {
                method.setAccessible(true);
                method.invoke(manager, component);
                invoked = true;
                append("fixedUsbHandler OK method=" + describeMethod(method) +
                        " component=" + component.flattenToShortString());
            } catch (Throwable t) {
                append("fixedUsbHandler FAIL method=" + describeMethod(method) +
                        " component=" + component.flattenToShortString() +
                        " " + formatThrowable(t));
            }
        }
        if (!invoked) {
            append("fixedUsbHandler method not found on UsbManager");
        }
        attemptSetFixedUsbHandlerViaBinder(component);
    }

    private void attemptSetFixedUsbHandlerViaBinder(ComponentName component) {
        try {
            Class<?> serviceManagerClass = Class.forName("android.os.ServiceManager");
            Object binder = serviceManagerClass
                    .getMethod("getService", String.class)
                    .invoke(null, "usb");
            if (binder == null) {
                append("fixedUsbHandlerBinder FAIL usb service binder=null");
                return;
            }
            Class<?> iUsbManagerStubClass =
                    Class.forName("android.hardware.usb.IUsbManager$Stub");
            Method asInterface = null;
            for (Method method : iUsbManagerStubClass.getDeclaredMethods()) {
                if ("asInterface".equals(method.getName()) &&
                        method.getParameterTypes().length == 1) {
                    asInterface = method;
                    break;
                }
            }
            if (asInterface == null) {
                append("fixedUsbHandlerBinder FAIL asInterface method not found");
                return;
            }
            asInterface.setAccessible(true);
            Object usbService = asInterface.invoke(null, binder);
            Class<?> iUsbManagerClass = Class.forName("android.hardware.usb.IUsbManager");
            Method method = null;
            for (Method candidate : iUsbManagerClass.getMethods()) {
                if ("setUsbDeviceConnectionHandler".equals(candidate.getName()) &&
                        candidate.getParameterTypes().length == 1) {
                    method = candidate;
                    break;
                }
            }
            if (method == null) {
                append("fixedUsbHandlerBinder FAIL setUsbDeviceConnectionHandler method not found");
                return;
            }
            method.invoke(usbService, component);
            append("fixedUsbHandlerBinder OK component=" + component.flattenToShortString());
        } catch (Throwable t) {
            append("fixedUsbHandlerBinder FAIL component=" +
                    component.flattenToShortString() + " " + formatThrowable(t));
        }
    }

    private void powerThermalModuleViaVendor(String label) {
        append("===== Vendor GPIO power-up " + label + " =====");
        try {
            DexClassLoader loader = getThermoVueClassLoader();
            initializeBlankjUtils(loader);
            Class<?> gpioClass = Class.forName(
                    "com.energy.dualmodule.sdk.util.GPIOUtils", true, loader);
            Method powerUp = gpioClass.getMethod("powerUpControl");
            powerUp.invoke(null);
            append("GPIOUtils.powerUpControl invoked");
        } catch (Throwable t) {
            append("GPIOUtils.powerUpControl FAIL " + formatThrowable(t));
        }
    }

    private void inspectUsbAttachIntent(String label, Intent intent) {
        if (intent == null) {
            append(label + " intent=null");
            return;
        }
        append(label + " intentAction=" + intent.getAction());
        if (!UsbManager.ACTION_USB_DEVICE_ATTACHED.equals(intent.getAction())) {
            return;
        }
        UsbDevice device = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
        UsbManager manager = (UsbManager) getSystemService(USB_SERVICE);
        append(label + " usbAttachDevice=" + describeUsbDevice(device) +
                " hasPermission=" + (device != null && manager.hasPermission(device)));
    }

    private boolean isUsbAttachIntent(Intent intent) {
        return intent != null &&
                UsbManager.ACTION_USB_DEVICE_ATTACHED.equals(intent.getAction());
    }

    private DexClassLoader buildThermoVueClassLoader() throws Exception {
        PackageInfo packageInfo = getPackageManager().getPackageInfo(THERMOVUE_PACKAGE, 0);
        ApplicationInfo appInfo = packageInfo.applicationInfo;
        String sourceDir = appInfo.sourceDir;
        String nativeLibDir = appInfo.nativeLibraryDir;
        append("ThermoVue sourceDir=" + sourceDir);
        append("ThermoVue nativeLibraryDir=" + nativeLibDir);
        append("ThermoVue versionName=" + packageInfo.versionName +
                " versionCode=" + getLongVersionCode(packageInfo));

        File libDir = new File(getCodeCacheDir(), "thermovue_libs");
        File dexDir = new File(getCodeCacheDir(), "thermovue_dex");
        recreateDir(libDir);
        recreateDir(dexDir);
        int libs = extractNativeLibraries(sourceDir, libDir);
        append("extractedNativeLibs=" + libs + " to " + libDir.getAbsolutePath());

        return new DexClassLoader(
                sourceDir,
                dexDir.getAbsolutePath(),
                libDir.getAbsolutePath(),
                getClassLoader());
    }

    private void probeClassLoading(ClassLoader loader) {
        String[] classes = new String[]{
                "com.energy.dualmodule.sdk.Tiny2CDualFusionProxy",
                "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualFusionPreviewManager",
                "com.energy.dualmodule.sdk.uvc.USBMonitorManager",
                "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualDeviceControlManager",
                "com.energy.dualmodule.sdk.service.task.DualPreviewMode",
                "com.energy.iruvccamera.usb.USBMonitor",
                "com.energy.iruvccamera.usb.OnDeviceConnectListener",
                "com.energy.ac020library.IrcamEngine",
                "com.energy.ac020library.IrcamEngineBuilder",
                "com.energy.ac020library.bean.DualUvcHandleParam",
                "com.energy.ac020library.bean.IIrFrameCallback",
                "com.tencent.mmkv.MMKV"
        };
        for (String name : classes) {
            try {
                Class<?> loaded = Class.forName(name, false, loader);
                append("classLoad OK " + name);
                dumpClassShape(loaded);
            } catch (Throwable t) {
                append("classLoad FAIL " + name + " " + formatThrowable(t));
            }
        }
        try {
            Class.forName("com.energy.ac020library.IrcamEngine", true, loader);
            append("IrcamEngine clinit OK");
        } catch (Throwable t) {
            append("IrcamEngine clinit FAIL " + formatThrowable(t));
        }
    }

    private void initializeMmkv(ClassLoader loader) {
        append("===== MMKV initialize =====");
        try {
            Class<?> mmkvClass = Class.forName("com.tencent.mmkv.MMKV", true, loader);
            File mmkvDir = new File(getFilesDir(), "thermovue_mmkv");
            //noinspection ResultOfMethodCallIgnored
            mmkvDir.mkdirs();
            Method initialize = mmkvClass.getMethod("initialize", String.class);
            Object result = initialize.invoke(null, mmkvDir.getAbsolutePath());
            append("MMKV initialize OK result=" + describeObject(result));
        } catch (Throwable t) {
            append("MMKV initialize FAIL " + formatThrowable(t));
        }
    }

    private void initializeBlankjUtils(ClassLoader loader) {
        append("===== Blankj Utils initialize =====");
        CountDownLatch latch = new CountDownLatch(1);
        String[] message = new String[1];
        runOnUiThread(() -> {
            try {
                Class<?> utilsClass = Class.forName("com.blankj.utilcode.util.Utils", true, loader);
                Method initialize = utilsClass.getMethod("init", android.app.Application.class);
                initialize.invoke(null, getApplication());
                Object app = utilsClass.getMethod("getApp").invoke(null);
                message[0] = "Blankj Utils init OK app=" + describeObject(app);
            } catch (Throwable t) {
                message[0] = "Blankj Utils init FAIL " + formatThrowable(t);
            } finally {
                latch.countDown();
            }
        });
        try {
            if (!latch.await(5, TimeUnit.SECONDS)) {
                append("Blankj Utils init timeout");
                return;
            }
            append(message[0]);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            append("Blankj Utils init interrupted");
        }
    }

    private void initializeRxBaseApplication(ClassLoader loader) {
        append("===== RXBaseApplication bootstrap =====");
        try {
            Class<?> rxBaseClass = Class.forName(
                    "com.energy.baselibrary.base.RXBaseApplication", true, loader);
            Class<?> baseClass = Class.forName(
                    "com.zzk.rxmvvmbase.base.BaseApplication", true, loader);
            Object rxApp = rxBaseClass.getConstructor().newInstance();

            Method attachBaseContext = ContextWrapper.class.getDeclaredMethod(
                    "attachBaseContext", Context.class);
            attachBaseContext.setAccessible(true);
            attachBaseContext.invoke(rxApp, getApplicationContext());

            setStaticField(rxBaseClass, "sInstance", rxApp);
            setStaticField(baseClass, "sInstance", rxApp);
            setField(baseClass, rxApp, "context", getApplicationContext());

            File docs = getExternalFilesDir(Environment.DIRECTORY_DOCUMENTS);
            File pictures = getExternalFilesDir(Environment.DIRECTORY_PICTURES);
            File dcim = getExternalFilesDir(Environment.DIRECTORY_DCIM);
            if (docs == null || pictures == null || dcim == null) {
                append("RXBaseApplication bootstrap FAIL externalFilesDir returned null");
                return;
            }
            File deviceDir = new File(docs, "deviceData");
            File calibrationDir = new File(deviceDir, "calibration");
            File commonDataDir = new File(dcim, "eco160dlp");
            File commonCalibrationDir = new File(commonDataDir, "common_calibration_data");
            //noinspection ResultOfMethodCallIgnored
            deviceDir.mkdirs();
            //noinspection ResultOfMethodCallIgnored
            calibrationDir.mkdirs();
            //noinspection ResultOfMethodCallIgnored
            pictures.mkdirs();

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
            setField(rxBaseClass, rxApp, "ISP_SWITCH_PATH",
                    new File(deviceDir, "isp_switch.json").getAbsolutePath());
            setField(rxBaseClass, rxApp, "ISP_STATIC_LIB_PATH",
                    new File(deviceDir, "isp_static_lib.json").getAbsolutePath());
            setField(rxBaseClass, rxApp, "ISP_H_PATH",
                    new File(deviceDir, "isp_H.json").getAbsolutePath());
            setField(rxBaseClass, rxApp, "ISP_L_PATH",
                    new File(deviceDir, "isp_L.json").getAbsolutePath());
            setField(rxBaseClass, rxApp, "DATA_FILE_SAVE_PATH", pictures.getAbsolutePath());
            setField(rxBaseClass, rxApp, "isNeedReadCalData", false);

            Object instance = rxBaseClass.getMethod("getInstance").invoke(null);
            append("RXBaseApplication bootstrap OK instance=" + describeObject(instance) +
                    " pictures=" + pictures.getAbsolutePath());
        } catch (Throwable t) {
            append("RXBaseApplication bootstrap FAIL " + formatThrowable(t));
        }
    }

    private boolean probeAndroidUsb(String label) {
        append("===== Android USB " + label + " =====");
        UsbManager usbManager = (UsbManager) getSystemService(USB_SERVICE);
        Map<String, UsbDevice> devices = usbManager.getDeviceList();
        append("usbDeviceCount=" + devices.size());
        boolean thermalGranted = false;
        for (UsbDevice device : devices.values()) {
            append("usbDevice name=" + device.getDeviceName() +
                    " vendor=0x" + Integer.toHexString(device.getVendorId()) +
                    " product=0x" + Integer.toHexString(device.getProductId()) +
                    " class=" + device.getDeviceClass() +
                    " interfaces=" + device.getInterfaceCount() +
                    " hasPermission=" + usbManager.hasPermission(device));
            if (isThermalDevice(device)) {
                attemptHiddenUsbGrant(usbManager, device);
                boolean granted = requestUsbPermissionAndWait(usbManager, device);
                append("thermalUsbPermissionAfterRequest=" + granted);
                thermalGranted = thermalGranted || granted || usbManager.hasPermission(device);
            }
        }
        return thermalGranted;
    }

    private void attemptHiddenUsbGrant(UsbManager manager, UsbDevice device) {
        logUsbManagerPermissionMethods();
        boolean invoked = false;
        for (Method method : UsbManager.class.getDeclaredMethods()) {
            if (!method.getName().equals("grantPermission")) {
                continue;
            }
            Class<?>[] types = method.getParameterTypes();
            try {
                method.setAccessible(true);
                if (types.length == 1 && types[0] == UsbDevice.class) {
                    method.invoke(manager, device);
                } else if (types.length == 2 && types[0] == UsbDevice.class &&
                        types[1] == int.class) {
                    method.invoke(manager, device, Process.myUid());
                } else if (types.length == 2 && types[0] == UsbDevice.class &&
                        types[1] == String.class) {
                    method.invoke(manager, device, getPackageName());
                } else {
                    append("hiddenUsbGrant skip unsupported signature " + describeMethod(method));
                    continue;
                }
                invoked = true;
                append("hiddenUsbGrant invoked " + describeMethod(method) +
                        " uid=" + Process.myUid() +
                        " hasPermission=" + manager.hasPermission(device));
            } catch (Throwable t) {
                append("hiddenUsbGrant FAIL " + describeMethod(method) + " " + formatThrowable(t));
            }
        }
        if (invoked) {
            return;
        }
        try {
            Method grantPermission = UsbManager.class.getDeclaredMethod(
                    "grantPermission", UsbDevice.class, int.class);
            grantPermission.setAccessible(true);
            grantPermission.invoke(manager, device, Process.myUid());
            append("hiddenUsbGrant invoked uid=" + Process.myUid() +
                    " hasPermission=" + manager.hasPermission(device));
        } catch (Throwable t) {
            append("hiddenUsbGrant FAIL " + formatThrowable(t));
        }
    }

    private void logUsbManagerPermissionMethods() {
        if (loggedUsbManagerPermissionMethods) {
            return;
        }
        loggedUsbManagerPermissionMethods = true;
        for (Method method : UsbManager.class.getDeclaredMethods()) {
            String name = method.getName().toLowerCase(Locale.US);
            if (name.contains("permission") || name.contains("grant") ||
                    name.contains("connectionhandler")) {
                append("UsbManager method " + describeMethod(method));
            }
        }
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

    private String describeField(Field field) {
        return field.getType().getName() + " " + field.getName();
    }

    private void dumpClassShape(Class<?> cls) {
        append("classShape " + cls.getName() +
                " methods=" + cls.getDeclaredMethods().length +
                " fields=" + cls.getDeclaredFields().length);
        int methodCount = 0;
        for (Method method : cls.getDeclaredMethods()) {
            append("classMethod " + cls.getName() + " " + describeMethod(method));
            methodCount++;
            if (methodCount >= 80) {
                append("classMethod " + cls.getName() + " truncated");
                break;
            }
        }
        int fieldCount = 0;
        for (Field field : cls.getDeclaredFields()) {
            append("classField " + cls.getName() + " " + describeField(field));
            fieldCount++;
            if (fieldCount >= 80) {
                append("classField " + cls.getName() + " truncated");
                break;
            }
        }
    }

    private void probeVendorUsbMonitor(ClassLoader loader, String label) {
        append("===== Vendor USBMonitor " + label + " =====");
        Object monitor = null;
        try {
            Class<?> listenerClass = Class.forName(
                    "com.energy.iruvccamera.usb.OnDeviceConnectListener", false, loader);
            Class<?> monitorClass = Class.forName(
                    "com.energy.iruvccamera.usb.USBMonitor", true, loader);
            Object listener = Proxy.newProxyInstance(
                    loader,
                    new Class[]{listenerClass},
                    new LoggingInvocationHandler("USBMonitorListener"));

            Constructor<?> constructor = monitorClass.getConstructor(
                    Context.class, boolean.class, listenerClass);
            monitor = constructor.newInstance(this, false, listener);
            invoke(monitor, "register");
            sleepMs(1000);

            Object count = invoke(monitor, "getDeviceCount");
            append("USBMonitor deviceCount=" + count);
            Object listObj = invoke(monitor, "getDeviceList");
            if (listObj instanceof List) {
                List<?> devices = (List<?>) listObj;
                append("USBMonitor deviceListSize=" + devices.size());
                for (Object object : devices) {
                    if (object instanceof UsbDevice) {
                        UsbDevice device = (UsbDevice) object;
                        append("USBMonitor device " + describeUsbDevice(device));
                        boolean hasPermission = (Boolean) invoke(
                                monitor, "hasPermission", new Class[]{UsbDevice.class}, device);
                        append("USBMonitor hasPermission=" + hasPermission);
                        if (isThermalDevice(device)) {
                            if (hasPermission) {
                                Object ctrlBlock = invoke(
                                        monitor, "openDevice", new Class[]{UsbDevice.class}, device);
                                append("USBMonitor openDevice result=" + describeObject(ctrlBlock));
                                sleepMs(1000);
                            } else {
                                Object requestResult = invoke(
                                        monitor, "requestPermission", new Class[]{UsbDevice.class}, device);
                                append("USBMonitor requestPermission result=" + requestResult);
                                sleepMs(10000);
                                boolean afterRequest = (Boolean) invoke(
                                        monitor, "hasPermission", new Class[]{UsbDevice.class}, device);
                                append("USBMonitor hasPermissionAfterRequest=" + afterRequest);
                                if (afterRequest) {
                                    Object ctrlBlock = invoke(
                                            monitor, "openDevice", new Class[]{UsbDevice.class}, device);
                                    append("USBMonitor openDevice result=" + describeObject(ctrlBlock));
                                }
                            }
                        }
                    } else {
                        append("USBMonitor nonUsbDevice=" + describeObject(object));
                    }
                }
            } else {
                append("USBMonitor getDeviceList returned " + describeObject(listObj));
            }
        } catch (Throwable t) {
            append("USBMonitor FAIL " + formatThrowable(t));
        } finally {
            if (monitor != null) {
                try {
                    invoke(monitor, "destroy");
                } catch (Throwable t) {
                    append("USBMonitor destroy FAIL " + formatThrowable(t));
                }
            }
        }
    }

    private void probeTiny2cProxy(ClassLoader loader, String label) {
        append("===== Tiny2CDualFusionProxy " + label + " =====");
        Object proxy = null;
        try {
            Class<?> proxyClass = Class.forName(
                    "com.energy.dualmodule.sdk.Tiny2CDualFusionProxy", true, loader);
            proxy = proxyClass.getMethod("getInstance").invoke(null);
            append("Tiny2C instance=" + describeObject(proxy));

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
            tryInvoke(proxy, "startPreview");
            pollTiny2c(proxy);
        } catch (Throwable t) {
            append("Tiny2C FAIL " + formatThrowable(t));
        } finally {
            if (proxy != null) {
                tryInvoke(proxy, "stopPreview");
                tryInvoke(proxy, "releaseSource");
            }
        }
    }

    private boolean pollTiny2c(Object proxy) {
        boolean sawFrame = false;
        for (int i = 0; i < 20; i++) {
            sleepMs(500);
            try {
                Object frameCount = tryInvoke(proxy, "getFrameCount");
                byte[] rawTemp = (byte[]) tryInvoke(proxy, "getRawTempData");
                byte[] remapTemp = (byte[]) tryInvoke(proxy, "getRemapTempData");
                Object firstFrame = tryInvoke(proxy, "getFirstFrameFlag");
                append("Tiny2C poll " + i +
                        " frameCount=" + frameCount +
                        " firstFrame=" + firstFrame +
                        " rawTemp=" + describeBytes(rawTemp) +
                        " remapTemp=" + describeBytes(remapTemp));
                boolean hasFrame = hasFrame(frameCount, firstFrame, rawTemp, remapTemp);
                sawFrame = sawFrame || hasFrame;
                if (hasFrame) {
                    maybeDumpFrame("raw_temp", rawTemp);
                    maybeDumpFrame("remap_temp", remapTemp);
                    maybeSendThermalUdp(frameCount, rawTemp);
                }
            } catch (Throwable t) {
                append("Tiny2C poll FAIL " + formatThrowable(t));
                return sawFrame;
            }
        }
        return sawFrame;
    }

    private UsbDevice waitForThermalUsb(long timeoutMs) {
        long deadline = System.currentTimeMillis() + timeoutMs;
        UsbManager usbManager = (UsbManager) getSystemService(USB_SERVICE);
        while (System.currentTimeMillis() < deadline) {
            Map<String, UsbDevice> devices = usbManager.getDeviceList();
            for (UsbDevice device : devices.values()) {
                if (isThermalDevice(device)) {
                    append("waitForThermalUsb found " + describeUsbDevice(device));
                    return device;
                }
            }
            sleepMs(250);
        }
        append("waitForThermalUsb timeout afterMs=" + timeoutMs);
        return null;
    }

    private UsbDevice waitForThermalUsbWithPermission(long timeoutMs) {
        long deadline = System.currentTimeMillis() + timeoutMs;
        long nextStatusLog = 0;
        UsbManager usbManager = (UsbManager) getSystemService(USB_SERVICE);
        while (System.currentTimeMillis() < deadline) {
            Map<String, UsbDevice> devices = usbManager.getDeviceList();
            for (UsbDevice device : devices.values()) {
                if (!isThermalDevice(device)) {
                    continue;
                }
                boolean hasPermission = usbManager.hasPermission(device);
                if (System.currentTimeMillis() >= nextStatusLog) {
                    append("waitForThermalUsbWithPermission thermalUsb=" +
                            describeUsbDevice(device) +
                            " hasPermission=" + hasPermission);
                    nextStatusLog = System.currentTimeMillis() + 2000;
                }
                if (hasPermission) {
                    return device;
                }
            }
            sleepMs(100);
        }
        append("waitForThermalUsbWithPermission timeout afterMs=" + timeoutMs);
        return null;
    }

    private void maybeDumpFrame(String label, byte[] bytes) {
        if (bytes == null || bytes.length == 0 || runDir == null) {
            return;
        }
        if ("raw_temp".equals(label)) {
            if (dumpedRawTemp) {
                return;
            }
            dumpedRawTemp = true;
        } else if ("remap_temp".equals(label)) {
            if (dumpedRemapTemp) {
                return;
            }
            dumpedRemapTemp = true;
        }
        File out = new File(runDir, label + "_" + stamp() + ".bin");
        try (FileOutputStream output = new FileOutputStream(out)) {
            output.write(bytes);
            append("frameDump " + label + " path=" + out.getAbsolutePath() +
                    " " + describeBytes(bytes));
        } catch (IOException e) {
            append("frameDump FAIL " + label + " " + formatThrowable(e));
        }
    }

    private void maybeSendThermalUdp(Object frameCount, byte[] rawTemp) {
        if (jetsonHost == null || jetsonHost.length() == 0 ||
                rawTemp == null || rawTemp.length == 0 ||
                (udpMaxFrames > 0 && udpSentFrames >= udpMaxFrames)) {
            return;
        }
        final int chunkBytes = 1200;
        int chunks = (rawTemp.length + chunkBytes - 1) / chunkBytes;
        try (DatagramSocket socket = new DatagramSocket()) {
            InetAddress address = InetAddress.getByName(jetsonHost);
            for (int chunk = 0; chunk < chunks; chunk++) {
                int offset = chunk * chunkBytes;
                int length = Math.min(chunkBytes, rawTemp.length - offset);
                byte[] header = ("YEGMINA_THERMAL_RAW_V1 frame=" + frameCount +
                        " chunk=" + chunk +
                        " chunks=" + chunks +
                        " offset=" + offset +
                        " total=" + rawTemp.length + "\n").getBytes();
                byte[] payload = new byte[header.length + length];
                System.arraycopy(header, 0, payload, 0, header.length);
                System.arraycopy(rawTemp, offset, payload, header.length, length);
                DatagramPacket packet = new DatagramPacket(
                        payload,
                        payload.length,
                        address,
                        jetsonPort);
                socket.send(packet);
            }
            udpSentFrames++;
            append("udpThermalFrame sent count=" + udpSentFrames +
                    " host=" + jetsonHost + ":" + jetsonPort +
                    " rawBytes=" + rawTemp.length +
                    " chunks=" + chunks);
        } catch (Throwable t) {
            append("udpThermalFrame FAIL " + formatThrowable(t));
        }
    }

    private void probeTiny2cDeviceControlPath(ClassLoader loader) {
        append("===== Tiny2C vendor device-control path =====");
        Object proxy = null;
        Object usbMonitorManager = null;
        Object deviceControlManager = null;
        try {
            Class<?> proxyClass = Class.forName(
                    "com.energy.dualmodule.sdk.Tiny2CDualFusionProxy", true, loader);
            proxy = proxyClass.getMethod("getInstance").invoke(null);
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
            append("DeviceControl Tiny2C init invoked");
            tryInvoke(proxy, "initData");
            tryInvokeBoolean(proxy, "setHasAPPKilled", false);
            tryInvokeBoolean(proxy, "setHasPreviewSurfaceDestroy", false);
            tryInvokeBoolean(proxy, "setPausePreviewEnable", false);
            tryInvoke(proxy, "resetIsFirstFrame");

            Class<?> usbMonitorManagerClass = Class.forName(
                    "com.energy.dualmodule.sdk.uvc.USBMonitorManager", true, loader);
            usbMonitorManager = usbMonitorManagerClass.getMethod("getInstance").invoke(null);
            tryInvoke(usbMonitorManager, "init");
            tryInvoke(usbMonitorManager, "registerMonitor");

            Class<?> deviceControlManagerClass = Class.forName(
                    "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualDeviceControlManager",
                    true,
                    loader);
            deviceControlManager = deviceControlManagerClass.getMethod("getInstance").invoke(null);
            tryInvoke(deviceControlManager, "init");

            Class<?> modeClass = Class.forName(
                    "com.energy.dualmodule.sdk.service.task.DualPreviewMode", true, loader);
            Object mode = enumValue(modeClass, "MODE_DUAL_FUSION");
            Method handleStartPreview = proxyClass.getMethod("handleStartPreview", modeClass);
            handleStartPreview.invoke(proxy, mode);
            append("DeviceControl handleStartPreview MODE_DUAL_FUSION invoked; waiting for vendor worker");
            sleepMs(12000);

            Object worker = tryGetFieldValue(
                    deviceControlManagerClass,
                    deviceControlManager,
                    "mDeviceControlWorker");
            append("DeviceControl worker=" + describeObject(worker));
            if (worker != null) {
                Object workerState = tryInvoke(worker, "getDeviceState");
                append("DeviceControl workerStateAfterVendorTask=" + describeObject(workerState));
            }

            Object connected = tryInvoke(usbMonitorManager, "isDeviceConnected");
            Object ctrlBlock = tryInvoke(usbMonitorManager, "getCtrlBlock");
            append("DeviceControl USB state connected=" + connected +
                    " ctrlBlock=" + describeObject(ctrlBlock));

            boolean sawFrame = pollTiny2c(proxy);
            append("DeviceControl vendor worker frameSeen=" + sawFrame);
            if (!sawFrame && ctrlBlock != null) {
                append("DeviceControl fallback: explicit initData/initHandleEngine");
                tryInvoke(proxy, "initData");
                try {
                    Class<?> ctrlBlockClass = Class.forName(
                            "com.energy.iruvccamera.usb.USBMonitor$UsbControlBlock",
                            false,
                            loader);
                    Object initHandleResult = invoke(
                            proxy,
                            "initHandleEngine",
                            new Class[]{ctrlBlockClass, boolean.class},
                            ctrlBlock,
                            true);
                    append("DeviceControl initHandleEngine result=" +
                            describeObject(initHandleResult));
                } catch (Throwable t) {
                    append("DeviceControl initHandleEngine FAIL " + formatThrowable(t));
                }
                sleepMs(1500);
                sawFrame = pollTiny2c(proxy);
                append("DeviceControl explicit initHandleEngine frameSeen=" + sawFrame);
            } else if (!sawFrame) {
                append("DeviceControl fallback skipped: ctrlBlock=null");
            }

            if (!sawFrame) {
                append("DeviceControl fallback: explicit startPreview");
                tryInvoke(proxy, "startPreview");
                sawFrame = pollTiny2c(proxy);
                append("DeviceControl explicit startPreview frameSeen=" + sawFrame);
            }
            if (keepStreaming) {
                streamTiny2c(proxy);
            }
        } catch (Throwable t) {
            append("Tiny2C device-control path FAIL " + formatThrowable(t));
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
        }
    }

    private void streamTiny2c(Object proxy) {
        append("streamTiny2c start seconds=" + streamSeconds +
                " udpMaxFrames=" + (udpMaxFrames <= 0 ? "unlimited" : String.valueOf(udpMaxFrames)));
        long deadline = System.currentTimeMillis() + Math.max(1, streamSeconds) * 1000L;
        String lastFrameId = null;
        int polls = 0;
        while (System.currentTimeMillis() < deadline &&
                (udpMaxFrames <= 0 || udpSentFrames < udpMaxFrames)) {
            sleepMs(200);
            polls++;
            try {
                Object frameCount = invoke(proxy, "getFrameCount");
                byte[] rawTemp = (byte[]) invoke(proxy, "getRawTempData");
                byte[] remapTemp = (byte[]) invoke(proxy, "getRemapTempData");
                Object firstFrame = invoke(proxy, "getFirstFrameFlag");
                String frameId = String.valueOf(frameCount);
                boolean freshFrame = !frameId.equals(lastFrameId) || frameCount == null;
                if (hasFrame(frameCount, firstFrame, rawTemp, remapTemp) && freshFrame) {
                    append("streamTiny2c frame poll=" + polls +
                            " frameCount=" + frameCount +
                            " firstFrame=" + firstFrame +
                            " rawTemp=" + describeBytes(rawTemp) +
                            " remapTemp=" + describeBytes(remapTemp));
                    maybeDumpFrame("raw_temp", rawTemp);
                    maybeDumpFrame("remap_temp", remapTemp);
                    maybeSendThermalUdp(frameCount, rawTemp);
                    lastFrameId = frameId;
                } else if (polls % 25 == 0) {
                    append("streamTiny2c heartbeat poll=" + polls +
                            " frameCount=" + frameCount +
                            " firstFrame=" + firstFrame +
                            " rawTemp=" + describeBytes(rawTemp) +
                            " udpSentFrames=" + udpSentFrames);
                }
            } catch (Throwable t) {
                append("streamTiny2c FAIL " + formatThrowable(t));
                return;
            }
        }
        append("streamTiny2c finished polls=" + polls + " udpSentFrames=" + udpSentFrames);
    }

    private void launchThermoVue() {
        append("launching ThermoVue package=" + THERMOVUE_PACKAGE);
        try {
            Intent intent = getPackageManager().getLaunchIntentForPackage(THERMOVUE_PACKAGE);
            if (intent == null) {
                append("ThermoVue launch intent=null");
                return;
            }
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(intent);
            append("ThermoVue launch requested");
        } catch (Throwable t) {
            append("ThermoVue launch FAIL " + formatThrowable(t));
        }
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
                    UsbDevice received = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
                    boolean wasGranted = intent.getBooleanExtra(
                            UsbManager.EXTRA_PERMISSION_GRANTED, false);
                    append("usbPermissionBroadcast device=" + describeObject(received) +
                            " granted=" + wasGranted);
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
            PendingIntent pendingIntent = PendingIntent.getBroadcast(this, 0, intent, flags);
            manager.requestPermission(device, pendingIntent);
            if (!latch.await(30, TimeUnit.SECONDS)) {
                append("usbPermissionBroadcast timeout");
            }
        } catch (Throwable t) {
            append("requestUsbPermission FAIL " + formatThrowable(t));
        } finally {
            try {
                unregisterReceiver(receiver);
            } catch (Throwable ignored) {
                // Receiver may not have registered if requestUsbPermission failed early.
            }
        }
        return granted[0] || manager.hasPermission(device);
    }

    private int extractNativeLibraries(String apkPath, File libDir) throws IOException {
        int count = 0;
        try (ZipFile zip = new ZipFile(apkPath)) {
            List<? extends ZipEntry> entries = Collections.list(zip.entries());
            for (ZipEntry entry : entries) {
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
                // Native libraries must be readable/executable by the app process.
                //noinspection ResultOfMethodCallIgnored
                out.setReadable(true, true);
                //noinspection ResultOfMethodCallIgnored
                out.setExecutable(true, true);
                //noinspection ResultOfMethodCallIgnored
                out.setWritable(false, true);
                count++;
            }
        }
        return count;
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

    private Object getFieldValue(Class<?> owner, Object target, String name) throws Exception {
        Field field = owner.getDeclaredField(name);
        field.setAccessible(true);
        return field.get(target);
    }

    private Object tryGetFieldValue(Class<?> owner, Object target, String name) {
        try {
            return getFieldValue(owner, target, name);
        } catch (Throwable t) {
            append("getFieldValue FAIL " + owner.getName() + "." + name +
                    " " + formatThrowable(t));
            return null;
        }
    }

    private boolean hasFrame(
            Object frameCount,
            Object firstFrame,
            byte[] rawTemp,
            byte[] remapTemp) {
        if (frameCount instanceof Number) {
            if (((Number) frameCount).longValue() > 0) {
                return true;
            }
        }
        if (frameCount != null) {
            try {
                if (Long.parseLong(String.valueOf(frameCount)) > 0) {
                    return true;
                }
            } catch (NumberFormatException ignored) {
                // Continue with first-frame/temp checks.
            }
        }
        if (firstFrame instanceof Number && ((Number) firstFrame).longValue() > 0) {
            return true;
        }
        if (firstFrame instanceof Boolean && ((Boolean) firstFrame)) {
            return true;
        }
        boolean rawLooksReal = rawTemp != null && rawTemp.length > 0 && checksum(rawTemp, 1024) != 0;
        boolean remapLooksReal =
                remapTemp != null && remapTemp.length > 0 && checksum(remapTemp, 1024) != 0;
        return rawLooksReal || remapLooksReal;
    }

    private int checksum(byte[] bytes, int limitBytes) {
        int checksum = 0;
        int limit = Math.min(bytes.length, limitBytes);
        for (int i = 0; i < limit; i++) {
            checksum = (checksum + (bytes[i] & 0xff)) & 0xffff;
        }
        return checksum;
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
        return object.getClass().getName() + ":" + String.valueOf(object);
    }

    private String describeBytes(byte[] bytes) {
        if (bytes == null) {
            return "null";
        }
        int checksum = 0;
        int limit = Math.min(bytes.length, 256);
        for (int i = 0; i < limit; i++) {
            checksum = (checksum + (bytes[i] & 0xff)) & 0xffff;
        }
        return "len=" + bytes.length + " checksum256=0x" + Integer.toHexString(checksum);
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
            if (t instanceof ExceptionInInitializerError &&
                    ((ExceptionInInitializerError) t).getException() != null) {
                t = ((ExceptionInInitializerError) t).getException();
            } else {
                t = t.getCause();
            }
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

    private void sleepMs(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private String stamp() {
        return new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date());
    }

    private void append(String line) {
        String stamped = new SimpleDateFormat("HH:mm:ss.SSS", Locale.US).format(new Date()) +
                " " + line;
        Log.i(TAG, stamped);
        runOnUiThread(() -> text.append(stamped + "\n"));
        if (logFile != null) {
            try (FileWriter writer = new FileWriter(logFile, true)) {
                writer.write(stamped);
                writer.write('\n');
            } catch (IOException e) {
                Log.e(TAG, "log write failed", e);
            }
        }
    }

    private final class LoggingInvocationHandler implements InvocationHandler {
        private final String prefix;

        private LoggingInvocationHandler(String prefix) {
            this.prefix = prefix;
        }

        @Override
        public Object invoke(Object proxy, Method method, Object[] args) {
            List<String> parts = new ArrayList<>();
            if (args != null) {
                for (Object arg : args) {
                    parts.add(describeObject(arg));
                }
            }
            append(prefix + "." + method.getName() + "(" + parts + ")");
            return null;
        }
    }
}
