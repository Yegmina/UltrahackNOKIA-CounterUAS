package com.yegmina.usbshellhelper;

import android.app.Application;
import android.content.Context;
import android.content.ContextWrapper;
import android.content.pm.ApplicationInfo;
import android.hardware.usb.UsbDevice;
import android.os.Bundle;
import android.os.Environment;
import android.os.Process;

import java.io.File;
import java.io.FileOutputStream;
import java.io.FileWriter;
import java.io.IOException;
import java.io.InputStream;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.text.SimpleDateFormat;
import java.util.Collections;
import java.util.Date;
import java.util.Locale;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

import dalvik.system.DexClassLoader;

public final class ThermoVueShellBridge {
    private static final String THERMOVUE_PACKAGE = "com.energy.tc2c";
    private static final String SHELL_PACKAGE = "com.android.shell";
    private static final int THERMAL_VENDOR_ID = 0x3474;
    private static final int THERMAL_PRODUCT_ID = 0x4321;
    private static final String TINY2C_USB_MODE_PATH =
            "/sys/devices/platform/yft_tiny2c_usb/tiny2c_usb_mode";
    private static final String TINY2C_EXTCON_MODE_PATH =
            "/sys/class/yft_extcon/tiny2c_mode";

    private String jetsonHost;
    private int jetsonPort = 25000;
    private int udpMaxFrames = 25;
    private int streamSeconds = 120;
    private boolean trySysfsPower = true;
    private int udpSentFrames;
    private File runDir;
    private File logFile;
    private File shellDataDir;

    private ThermoVueShellBridge() {
    }

    public static void main(String[] args) throws Exception {
        new ThermoVueShellBridge().run(args);
    }

    private void run(String[] args) throws Exception {
        parseArgs(args);
        runDir = new File("/data/local/tmp/thermovue_shell_bridge_" + stamp());
        //noinspection ResultOfMethodCallIgnored
        runDir.mkdirs();
        logFile = new File(runDir, "thermovue_shell_bridge.log");
        shellDataDir = new File("/data/local/tmp/thermovue_shell_data");
        //noinspection ResultOfMethodCallIgnored
        shellDataDir.mkdirs();

        append("uid=" + Process.myUid() + " pid=" + Process.myPid());
        append("jetsonUdp=" + (jetsonHost == null ? "disabled" : jetsonHost + ":" + jetsonPort));
        append("udpMaxFrames=" + (udpMaxFrames <= 0 ? "unlimited" : String.valueOf(udpMaxFrames)));
        append("streamSeconds=" + streamSeconds);
        append("trySysfsPower=" + trySysfsPower);

        Context context = createShellContext();
        append("contextPackage=" + context.getPackageName());

        ApplicationInfo thermoVueInfo = getApplicationInfo(THERMOVUE_PACKAGE);
        append("ThermoVue sourceDir=" + thermoVueInfo.sourceDir);
        append("ThermoVue nativeLibraryDir=" + thermoVueInfo.nativeLibraryDir);

        DexClassLoader loader = buildThermoVueClassLoader(thermoVueInfo.sourceDir);
        initializeMmkv(loader);
        initializeBlankjUtils(loader, context);
        initializeRxBaseApplication(loader, context);
        probeClassLoading(loader);

        if (trySysfsPower) {
            powerThermalModuleViaSysfs();
        }
        waitForThermalUsbAndGrantSelf(20000);
        runTiny2cDeviceControlPath(loader, context);
    }

    private void parseArgs(String[] args) {
        for (int i = 0; i < args.length; i++) {
            String arg = args[i];
            if ("--jetson-host".equals(arg) && i + 1 < args.length) {
                jetsonHost = args[++i];
            } else if ("--jetson-port".equals(arg) && i + 1 < args.length) {
                jetsonPort = Integer.parseInt(args[++i]);
            } else if ("--udp-max-frames".equals(arg) && i + 1 < args.length) {
                udpMaxFrames = Integer.parseInt(args[++i]);
            } else if ("--stream-seconds".equals(arg) && i + 1 < args.length) {
                streamSeconds = Integer.parseInt(args[++i]);
            } else if ("--no-sysfs-power".equals(arg)) {
                trySysfsPower = false;
            } else {
                throw new IllegalArgumentException("Unknown/incomplete arg: " + arg);
            }
        }
    }

    private Context createShellContext() throws Exception {
        Class<?> activityThreadClass = Class.forName("android.app.ActivityThread");
        Object activityThread = activityThreadClass
                .getMethod("currentActivityThread")
                .invoke(null);
        if (activityThread == null) {
            activityThread = activityThreadClass.getMethod("systemMain").invoke(null);
        }
        Context systemContext =
                (Context) activityThreadClass.getMethod("getSystemContext").invoke(activityThread);
        Context shellContext = systemContext.createPackageContext(
                SHELL_PACKAGE,
                Context.CONTEXT_IGNORE_SECURITY);
        return new LocalTmpContext(shellContext, shellDataDir);
    }

    private ApplicationInfo getApplicationInfo(String packageName) throws Exception {
        Class<?> appGlobals = Class.forName("android.app.AppGlobals");
        Object packageManager = appGlobals.getMethod("getPackageManager").invoke(null);
        for (Method method : packageManager.getClass().getMethods()) {
            if (!"getApplicationInfo".equals(method.getName())) {
                continue;
            }
            Class<?>[] types = method.getParameterTypes();
            Object result;
            if (types.length == 3 && types[0] == String.class &&
                    types[1] == long.class && types[2] == int.class) {
                result = method.invoke(packageManager, packageName, 0L, 0);
            } else if (types.length == 3 && types[0] == String.class &&
                    types[1] == int.class && types[2] == int.class) {
                result = method.invoke(packageManager, packageName, 0, 0);
            } else {
                continue;
            }
            return (ApplicationInfo) result;
        }
        throw new NoSuchMethodException("IPackageManager.getApplicationInfo");
    }

    private DexClassLoader buildThermoVueClassLoader(String sourceDir) throws Exception {
        File libDir = new File("/data/local/tmp/thermovue_shell_libs");
        File dexDir = new File("/data/local/tmp/thermovue_shell_dex");
        recreateDir(libDir);
        recreateDir(dexDir);
        int libs = extractNativeLibraries(sourceDir, libDir);
        append("extractedNativeLibs=" + libs + " to " + libDir.getAbsolutePath());
        return new DexClassLoader(
                sourceDir,
                dexDir.getAbsolutePath(),
                libDir.getAbsolutePath(),
                ThermoVueShellBridge.class.getClassLoader());
    }

    private void initializeMmkv(ClassLoader loader) {
        append("===== MMKV initialize =====");
        try {
            Class<?> mmkvClass = Class.forName("com.tencent.mmkv.MMKV", true, loader);
            File mmkvDir = new File("/data/local/tmp/thermovue_shell_mmkv");
            //noinspection ResultOfMethodCallIgnored
            mmkvDir.mkdirs();
            Object result = mmkvClass
                    .getMethod("initialize", String.class)
                    .invoke(null, mmkvDir.getAbsolutePath());
            append("MMKV initialize OK result=" + describeObject(result));
        } catch (Throwable t) {
            append("MMKV initialize FAIL " + formatThrowable(t));
        }
    }

    private void initializeBlankjUtils(ClassLoader loader, Context context) {
        append("===== Blankj Utils initialize =====");
        try {
            Application shellApp = new Application();
            Method attachBaseContext = ContextWrapper.class.getDeclaredMethod(
                    "attachBaseContext", Context.class);
            attachBaseContext.setAccessible(true);
            attachBaseContext.invoke(shellApp, context);
            Class<?> utilsClass = Class.forName("com.blankj.utilcode.util.Utils", true, loader);
            utilsClass.getMethod("init", Application.class).invoke(null, shellApp);
            Object app = utilsClass.getMethod("getApp").invoke(null);
            append("Blankj Utils init OK app=" + describeObject(app));
        } catch (Throwable t) {
            append("Blankj Utils init FAIL " + formatThrowable(t));
        }
    }

    private void initializeRxBaseApplication(ClassLoader loader, Context context) {
        append("===== RXBaseApplication bootstrap =====");
        try {
            Class<?> rxBaseClass = Class.forName(
                    "com.energy.baselibrary.base.RXBaseApplication", true, loader);
            Class<?> baseClass = Class.forName(
                    "com.zzk.rxmvvmbase.base.BaseApplication", true, loader);
            Application rxApp = (Application) rxBaseClass.getConstructor().newInstance();

            Method attachBaseContext = ContextWrapper.class.getDeclaredMethod(
                    "attachBaseContext", Context.class);
            attachBaseContext.setAccessible(true);
            attachBaseContext.invoke(rxApp, context);

            File baseDir = shellDataDir;
            File docs = new File(baseDir, "Documents");
            File pictures = new File(baseDir, "Pictures");
            File dcim = new File(baseDir, "DCIM");
            File deviceDir = new File(docs, "deviceData");
            File calibrationDir = new File(deviceDir, "calibration");
            File commonDataDir = new File(dcim, "eco160dlp");
            File commonCalibrationDir = new File(commonDataDir, "common_calibration_data");
            //noinspection ResultOfMethodCallIgnored
            calibrationDir.mkdirs();
            //noinspection ResultOfMethodCallIgnored
            pictures.mkdirs();
            //noinspection ResultOfMethodCallIgnored
            commonCalibrationDir.mkdirs();

            setStaticField(rxBaseClass, "sInstance", rxApp);
            setStaticField(baseClass, "sInstance", rxApp);
            setField(baseClass, rxApp, "context", context);
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
            append("RXBaseApplication bootstrap OK instance=" + describeObject(instance));
        } catch (Throwable t) {
            append("RXBaseApplication bootstrap FAIL " + formatThrowable(t));
        }
    }

    private void probeClassLoading(ClassLoader loader) {
        String[] classes = new String[]{
                "com.energy.dualmodule.sdk.Tiny2CDualFusionProxy",
                "com.energy.dualmodule.sdk.uvc.USBMonitorManager",
                "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualDeviceControlManager",
                "com.energy.dualmodule.sdk.uvc.UvcNativeCamDualFusionPreviewManager",
                "com.energy.dualmodule.sdk.service.task.DualPreviewMode",
                "com.energy.ac020library.IrcamEngine",
                "com.energy.iruvccamera.usb.USBMonitor",
                "com.energy.ac020library.bean.IIrFrameCallback"
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
    }

    private void waitForThermalUsbAndGrantSelf(long timeoutMs) {
        append("===== Shell USB thermal grant =====");
        try {
            Object usbProxy = getUsbProxy();
            UsbDevice device = waitForThermalUsb(usbProxy, timeoutMs);
            if (device == null) {
                append("shellGrant thermal device not found");
                return;
            }
            Method grant = findMethod(usbProxy.getClass(), "grantDevicePermission", 2);
            grant.invoke(usbProxy, device, Process.myUid());
            append("shellGrant OK uid=" + Process.myUid() + " device=" + describeUsbDevice(device) +
                    " hasPermission=" + hasDevicePermission(usbProxy, device));
        } catch (Throwable t) {
            append("shellGrant FAIL " + formatThrowable(t));
        }
    }

    private void powerThermalModuleViaSysfs() {
        append("===== Shell sysfs thermal power attempt =====");
        writeSysfs(TINY2C_USB_MODE_PATH, "1\n");
        writeSysfs(TINY2C_EXTCON_MODE_PATH, "1\n");
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

    private String hasDevicePermission(Object usbProxy, UsbDevice device) {
        for (Method method : usbProxy.getClass().getMethods()) {
            if (!method.getName().toLowerCase(Locale.US).contains("permission")) {
                continue;
            }
            Class<?>[] types = method.getParameterTypes();
            try {
                if (types.length == 2 && types[0] == UsbDevice.class &&
                        types[1] == String.class) {
                    Object result = method.invoke(usbProxy, device, SHELL_PACKAGE);
                    return method.getName() + "=" + result;
                }
                if (types.length == 2 && types[0] == UsbDevice.class &&
                        types[1] == int.class) {
                    Object result = method.invoke(usbProxy, device, Process.myUid());
                    return method.getName() + "=" + result;
                }
                if (types.length == 1 && types[0] == UsbDevice.class &&
                        method.getReturnType() == boolean.class) {
                    Object result = method.invoke(usbProxy, device);
                    return method.getName() + "=" + result;
                }
            } catch (Throwable ignored) {
                // Try the next permission-like method.
            }
        }
        return "unknown";
    }

    private UsbDevice waitForThermalUsb(Object usbProxy, long timeoutMs) throws Exception {
        long deadline = System.currentTimeMillis() + timeoutMs;
        while (System.currentTimeMillis() < deadline) {
            UsbDevice device = findThermalDevice(usbProxy);
            if (device != null) {
                append("thermalUsb found " + describeUsbDevice(device));
                return device;
            }
            Thread.sleep(100);
        }
        return null;
    }

    private UsbDevice findThermalDevice(Object usbProxy) throws Exception {
        Bundle devices = new Bundle();
        Method getDeviceList = findMethod(usbProxy.getClass(), "getDeviceList", 1);
        getDeviceList.invoke(usbProxy, devices);
        for (String key : devices.keySet()) {
            Object value = devices.get(key);
            if (!(value instanceof UsbDevice)) {
                continue;
            }
            UsbDevice device = (UsbDevice) value;
            if (device.getVendorId() == THERMAL_VENDOR_ID &&
                    device.getProductId() == THERMAL_PRODUCT_ID) {
                return device;
            }
        }
        return null;
    }

    private Object getUsbProxy() throws Exception {
        Class<?> serviceManager = Class.forName("android.os.ServiceManager");
        Object binder = serviceManager.getMethod("getService", String.class).invoke(null, "usb");
        Class<?> stub = Class.forName("android.hardware.usb.IUsbManager$Stub");
        Method asInterface = null;
        for (Method method : stub.getDeclaredMethods()) {
            if ("asInterface".equals(method.getName()) &&
                    method.getParameterTypes().length == 1) {
                asInterface = method;
                break;
            }
        }
        if (asInterface == null) {
            throw new NoSuchMethodException("IUsbManager.Stub.asInterface");
        }
        asInterface.setAccessible(true);
        return asInterface.invoke(null, binder);
    }

    private void runTiny2cDeviceControlPath(ClassLoader loader, Context context) {
        append("===== Shell Tiny2C device-control path =====");
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
            init.invoke(proxy, context, 256, 386, 1.0f, 25, "0", 1440, 1080, 25);
            append("Shell Tiny2C init invoked");
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
            append("Shell handleStartPreview MODE_DUAL_FUSION invoked");
            sleepMs(12000);

            Object connected = tryInvoke(usbMonitorManager, "isDeviceConnected");
            Object ctrlBlock = tryInvoke(usbMonitorManager, "getCtrlBlock");
            append("Shell USB state connected=" + connected +
                    " ctrlBlock=" + describeObject(ctrlBlock));

            boolean sawFrame = pollTiny2c(proxy);
            append("Shell vendor worker frameSeen=" + sawFrame);
            if (!sawFrame && ctrlBlock != null) {
                append("Shell fallback explicit initData/initHandleEngine");
                tryInvoke(proxy, "initData");
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
                append("Shell initHandleEngine result=" + describeObject(initHandleResult));
                sleepMs(1500);
                sawFrame = pollTiny2c(proxy);
                append("Shell explicit initHandleEngine frameSeen=" + sawFrame);
            }
            if (!sawFrame) {
                append("Shell fallback explicit startPreview");
                tryInvoke(proxy, "startPreview");
                sawFrame = pollTiny2c(proxy);
                append("Shell explicit startPreview frameSeen=" + sawFrame);
            }
            streamTiny2c(proxy);
        } catch (Throwable t) {
            append("Shell Tiny2C path FAIL " + formatThrowable(t));
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
                sawFrame = sawFrame || hasFrame(frameCount, firstFrame, rawTemp, remapTemp);
                if (sawFrame) {
                    maybeSendThermalUdp(frameCount, rawTemp);
                }
            } catch (Throwable t) {
                append("Tiny2C poll FAIL " + formatThrowable(t));
                return sawFrame;
            }
        }
        return sawFrame;
    }

    private void streamTiny2c(Object proxy) {
        append("streamTiny2c start seconds=" + streamSeconds);
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
                    maybeSendThermalUdp(frameCount, rawTemp);
                    lastFrameId = frameId;
                } else if (polls % 25 == 0) {
                    append("streamTiny2c heartbeat poll=" + polls +
                            " frameCount=" + frameCount +
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
                    " rawBytes=" + rawTemp.length + " chunks=" + chunks);
        } catch (Throwable t) {
            append("udpThermalFrame FAIL " + formatThrowable(t));
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
                out.setReadable(true, false);
                //noinspection ResultOfMethodCallIgnored
                out.setExecutable(true, false);
                count++;
            }
        }
        return count;
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

    private static Method findMethod(Class<?> owner, String name, int parameterCount)
            throws NoSuchMethodException {
        for (Method method : owner.getMethods()) {
            if (name.equals(method.getName()) &&
                    method.getParameterTypes().length == parameterCount) {
                return method;
            }
        }
        throw new NoSuchMethodException(owner.getName() + "." + name);
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

    private void setStaticField(Class<?> owner, String name, Object value) throws Exception {
        Field field = owner.getDeclaredField(name);
        field.setAccessible(true);
        field.set(null, value);
    }

    private void setField(Class<?> owner, Object target, String name, Object value)
            throws Exception {
        Field field = owner.getDeclaredField(name);
        field.setAccessible(true);
        field.set(target, value);
    }

    private boolean hasFrame(
            Object frameCount,
            Object firstFrame,
            byte[] rawTemp,
            byte[] remapTemp) {
        if (frameCount instanceof Number && ((Number) frameCount).longValue() > 0) {
            return true;
        }
        if (firstFrame instanceof Number && ((Number) firstFrame).longValue() > 0) {
            return true;
        }
        return (rawTemp != null && rawTemp.length > 0 && checksum(rawTemp, 1024) != 0) ||
                (remapTemp != null && remapTemp.length > 0 && checksum(remapTemp, 1024) != 0);
    }

    private int checksum(byte[] bytes, int limitBytes) {
        int checksum = 0;
        int limit = Math.min(bytes.length, limitBytes);
        for (int i = 0; i < limit; i++) {
            checksum = (checksum + (bytes[i] & 0xff)) & 0xffff;
        }
        return checksum;
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
        return "len=" + bytes.length +
                " checksum1024=0x" + Integer.toHexString(checksum(bytes, 1024));
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
        System.out.println(stamped);
        if (logFile != null) {
            try (FileWriter writer = new FileWriter(logFile, true)) {
                writer.write(stamped);
                writer.write('\n');
            } catch (IOException e) {
                System.out.println("log write failed " + e);
            }
        }
    }

    private static final class LocalTmpContext extends ContextWrapper {
        private final File baseDir;

        private LocalTmpContext(Context base, File baseDir) {
            super(base);
            this.baseDir = baseDir;
        }

        @Override
        public File getExternalFilesDir(String type) {
            File dir = type == null ? new File(baseDir, "files") : new File(baseDir, type);
            //noinspection ResultOfMethodCallIgnored
            dir.mkdirs();
            return dir;
        }

        @Override
        public File[] getExternalFilesDirs(String type) {
            return new File[]{getExternalFilesDir(type)};
        }

        @Override
        public File getExternalCacheDir() {
            File dir = new File(baseDir, "cache");
            //noinspection ResultOfMethodCallIgnored
            dir.mkdirs();
            return dir;
        }

        @Override
        public File[] getExternalCacheDirs() {
            return new File[]{getExternalCacheDir()};
        }
    }
}
