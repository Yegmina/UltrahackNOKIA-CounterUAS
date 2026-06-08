package com.yegmina.thermovueinstrumentationprobe;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbManager;
import android.os.Bundle;
import android.os.SystemClock;
import android.util.Log;

import java.io.FileWriter;
import java.lang.reflect.Method;
import java.util.Map;

public class ThermoVueInstrumentation extends Instrumentation {
    private static final String TAG = "ThermoVueInstrProbe";
    private static final int THERMAL_VENDOR_ID = 0x3474;
    private static final int THERMAL_PRODUCT_ID = 0x4321;

    @Override
    public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        start();
    }

    @Override
    public void onStart() {
        Bundle result = new Bundle();
        try {
            Context target = getTargetContext();
            log("targetPackage=" + target.getPackageName());
            log("targetClassLoader=" + target.getClassLoader());

            boolean directA = writeSysfs(
                    "/sys/devices/platform/yft_tiny2c_usb/tiny2c_usb_mode", "1\n");
            boolean directB = writeSysfs("/sys/class/yft_extcon/tiny2c_mode", "1\n");
            result.putBoolean("directTiny2cUsbModeWrite", directA);
            result.putBoolean("directTiny2cModeWrite", directB);

            invokeThermoVueGpio(target);
            SystemClock.sleep(1500);

            UsbManager usbManager = (UsbManager) target.getSystemService(Context.USB_SERVICE);
            Map<String, UsbDevice> devices = usbManager.getDeviceList();
            log("usbDeviceCount=" + devices.size());
            result.putInt("usbDeviceCount", devices.size());
            for (UsbDevice device : devices.values()) {
                String line = "usbDevice name=" + device.getDeviceName()
                        + " vendor=0x" + Integer.toHexString(device.getVendorId())
                        + " product=0x" + Integer.toHexString(device.getProductId())
                        + " class=" + device.getDeviceClass()
                        + " interfaces=" + device.getInterfaceCount()
                        + " hasPermission=" + usbManager.hasPermission(device);
                log(line);
                if (device.getVendorId() == THERMAL_VENDOR_ID
                        && device.getProductId() == THERMAL_PRODUCT_ID) {
                    result.putString("thermalDevice", line);
                }
            }
            finish(Activity.RESULT_OK, result);
        } catch (Throwable t) {
            log("FATAL " + Log.getStackTraceString(t));
            result.putString("fatal", t.toString());
            finish(Activity.RESULT_CANCELED, result);
        }
    }

    private void invokeThermoVueGpio(Context target) {
        try {
            Class<?> gpioClass = target.getClassLoader().loadClass(
                    "com.energy.dualmodule.sdk.util.GPIOUtils");
            Method powerUp = gpioClass.getMethod("powerUpControl");
            powerUp.invoke(null);
            log("GPIOUtils.powerUpControl invoked");
        } catch (Throwable t) {
            log("GPIOUtils.powerUpControl FAIL " + Log.getStackTraceString(t));
        }
    }

    private boolean writeSysfs(String path, String value) {
        try (FileWriter writer = new FileWriter(path)) {
            writer.write(value);
            writer.flush();
            log("writeSysfs OK path=" + path + " value=" + value.trim());
            return true;
        } catch (Throwable t) {
            log("writeSysfs FAIL path=" + path + " " + Log.getStackTraceString(t));
            return false;
        }
    }

    private void log(String message) {
        Log.i(TAG, message);
        Bundle status = new Bundle();
        status.putString("message", message);
        sendStatus(0, status);
    }
}
