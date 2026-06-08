package com.yegmina.thermovuebridgeprobe;

import android.app.Activity;
import android.content.Intent;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbManager;
import android.os.Bundle;
import android.util.Log;

public final class HeadlessUsbAttachActivity extends Activity {
    private static final String TAG = "ThermoVueHeadlessUsb";

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        handleIntent("onCreate", getIntent());
        moveTaskToBack(true);
        finishAndRemoveTask();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handleIntent("onNewIntent", intent);
        moveTaskToBack(true);
        finishAndRemoveTask();
    }

    private void handleIntent(String label, Intent intent) {
        if (intent == null) {
            Log.i(TAG, label + " intent=null");
            return;
        }
        String action = intent.getAction();
        UsbDevice device = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
        UsbManager manager = (UsbManager) getSystemService(USB_SERVICE);
        boolean hasPermission = device != null && manager.hasPermission(device);
        Log.i(TAG, label + " action=" + action +
                " device=" + describeUsbDevice(device) +
                " hasPermission=" + hasPermission);
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
}
