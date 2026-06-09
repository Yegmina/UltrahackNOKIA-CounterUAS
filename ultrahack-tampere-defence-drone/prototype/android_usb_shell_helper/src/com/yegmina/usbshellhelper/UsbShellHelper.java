package com.yegmina.usbshellhelper;

import android.content.ComponentName;
import android.hardware.usb.UsbDevice;
import android.os.Bundle;
import android.os.IBinder;
import android.os.Parcel;

import java.lang.reflect.Field;
import java.lang.reflect.Method;

public final class UsbShellHelper {
    private static final String DESCRIPTOR = "android.hardware.usb.IUsbManager";
    private static final String BRIDGE_COMPONENT =
            "com.yegmina.thermovuebridgeprobe/.HeadlessUsbAttachActivity";
    private static final String BRIDGE_PACKAGE = "com.yegmina.thermovuebridgeprobe";
    private static final int THERMAL_VENDOR_ID = 0x3474;
    private static final int THERMAL_PRODUCT_ID = 0x4321;

    private UsbShellHelper() {
    }

    public static void main(String[] args) throws Exception {
        String command = args.length > 0 ? args[0] : "inspect";

        Object binder = getUsbBinder();
        System.out.println("usbBinder=" + binder);
        inspectIUsbManager();

        if ("set-fixed-handler".equals(command)) {
            ComponentName component = parseComponent(args.length > 1 ? args[1] : BRIDGE_COMPONENT);
            setFixedHandler(binder, component);
        } else if ("clear-fixed-handler".equals(command)) {
            setFixedHandler(binder, null);
        } else if ("grant-thermal".equals(command)) {
            String packageName = args.length > 1 ? args[1] : BRIDGE_PACKAGE;
            long timeoutMs = args.length > 2 ? Long.parseLong(args[2]) : 15000L;
            grantThermalDeviceToPackage(binder, packageName, timeoutMs);
        } else if ("changenode-inspect".equals(command)) {
            String node = args.length > 1
                    ? args[1]
                    : "/sys/devices/platform/yft_tiny2c_usb/tiny2c_usb_mode";
            inspectChangeNode(node);
        } else if ("changenode-write".equals(command)) {
            if (args.length < 3) {
                throw new IllegalArgumentException("Usage: changenode-write <node> <data>");
            }
            writeChangeNode(args[1], args[2]);
        } else if ("inspect".equals(command)) {
            // Inspection only.
        } else {
            throw new IllegalArgumentException("Unknown command: " + command);
        }
    }

    private static ComponentName parseComponent(String value) {
        ComponentName component = ComponentName.unflattenFromString(value);
        if (component == null) {
            throw new IllegalArgumentException("Bad component: " + value);
        }
        return component;
    }

    private static Object getUsbBinder() throws Exception {
        Class<?> serviceManager = Class.forName("android.os.ServiceManager");
        return serviceManager.getMethod("getService", String.class).invoke(null, "usb");
    }

    private static void inspectIUsbManager() throws Exception {
        try {
            Class<?> iface = Class.forName("android.hardware.usb.IUsbManager");
            System.out.println("IUsbManager=" + iface);
            for (Method method : iface.getMethods()) {
                String name = method.getName();
                if (name.contains("UsbDeviceConnectionHandler") ||
                        name.contains("grantDevicePermission")) {
                    System.out.println("method=" + describeMethod(method));
                }
            }
        } catch (Throwable t) {
            System.out.println("inspect IUsbManager fail=" + t);
        }

        try {
            Class<?> stub = Class.forName("android.hardware.usb.IUsbManager$Stub");
            System.out.println("IUsbManager.Stub=" + stub);
            for (Method method : stub.getDeclaredMethods()) {
                String name = method.getName();
                if (name.contains("asInterface") ||
                        name.contains("UsbDeviceConnectionHandler")) {
                    System.out.println("stubMethod=" + describeMethod(method));
                }
            }
            for (Field field : stub.getDeclaredFields()) {
                String name = field.getName();
                if (name.contains("TRANSACTION") ||
                        name.contains("setUsbDeviceConnectionHandler")) {
                    field.setAccessible(true);
                    System.out.println("stubField=" + name + "=" + field.get(null));
                }
            }
        } catch (Throwable t) {
            System.out.println("inspect IUsbManager.Stub fail=" + t);
        }
    }

    private static void setFixedHandler(Object binderObject, ComponentName component)
            throws Exception {
        if (!(binderObject instanceof IBinder)) {
            throw new IllegalStateException("usb service is not IBinder: " + binderObject);
        }
        IBinder binder = (IBinder) binderObject;

        if (trySetViaProxy(binder, component)) {
            return;
        }
        transactSetFixedHandler(binder, component);
    }

    private static boolean trySetViaProxy(IBinder binder, ComponentName component) {
        try {
            Object proxy = getUsbProxy(binder);
            Method setter = null;
            for (Method method : proxy.getClass().getMethods()) {
                if ("setUsbDeviceConnectionHandler".equals(method.getName()) &&
                        method.getParameterTypes().length == 1) {
                    setter = method;
                    break;
                }
            }
            if (setter == null) {
                System.out.println("proxy path unavailable: setter missing");
                return false;
            }
            setter.invoke(proxy, component);
            System.out.println("setFixedHandler proxy OK component=" + component);
            return true;
        } catch (Throwable t) {
            System.out.println("setFixedHandler proxy FAIL=" + unwrap(t));
            return false;
        }
    }

    private static Object getUsbProxy(IBinder binder) throws Exception {
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

    private static void grantThermalDeviceToPackage(
            Object binderObject,
            String packageName,
            long timeoutMs) throws Exception {
        if (!(binderObject instanceof IBinder)) {
            throw new IllegalStateException("usb service is not IBinder: " + binderObject);
        }
        IBinder binder = (IBinder) binderObject;
        Object proxy = getUsbProxy(binder);
        int uid = getPackageUid(packageName, 0);
        System.out.println("grantThermal package=" + packageName + " uid=" + uid +
                " timeoutMs=" + timeoutMs);

        long deadline = System.currentTimeMillis() + timeoutMs;
        UsbDevice thermalDevice = null;
        while (System.currentTimeMillis() < deadline) {
            thermalDevice = findThermalDevice(proxy);
            if (thermalDevice != null) {
                break;
            }
            Thread.sleep(100);
        }
        if (thermalDevice == null) {
            System.out.println("grantThermal FAIL thermal device not found");
            return;
        }

        System.out.println("grantThermal found " + describeUsbDevice(thermalDevice));
        Method grant = findMethod(proxy.getClass(), "grantDevicePermission", 2);
        grant.invoke(proxy, thermalDevice, uid);
        System.out.println("grantThermal OK package=" + packageName +
                " uid=" + uid + " device=" + thermalDevice.getDeviceName());
    }

    private static Object getChangeNodeService() throws Exception {
        Class<?> changeNodeClass =
                Class.forName("vendor.yft.hardware.changenode.V1_0.IChangeNode");
        Method getService = changeNodeClass.getMethod("getService");
        Object service = getService.invoke(null);
        if (service == null) {
            throw new IllegalStateException("IChangeNode.getService returned null");
        }
        System.out.println("changeNodeService=" + service);
        return service;
    }

    private static void inspectChangeNode(String node) throws Exception {
        Object service = getChangeNodeService();
        Method contains = findMethod(service.getClass(), "is_node_contain", 1);
        Object result = contains.invoke(service, node);
        System.out.println("changeNode is_node_contain node=" + node + " result=" + result);
    }

    private static void writeChangeNode(String node, String data) throws Exception {
        Object service = getChangeNodeService();
        Method contains = findMethod(service.getClass(), "is_node_contain", 1);
        Object before = contains.invoke(service, node);
        System.out.println("changeNode before node=" + node + " result=" + before);
        Method change = findMethod(service.getClass(), "change_node_data", 2);
        Object writeResult = change.invoke(service, node, data);
        System.out.println("changeNode write node=" + node +
                " data=" + data + " result=" + writeResult);
        Object after = contains.invoke(service, node);
        System.out.println("changeNode after node=" + node + " result=" + after);
    }

    private static UsbDevice findThermalDevice(Object usbProxy) throws Exception {
        Bundle devices = new Bundle();
        Method getDeviceList = findMethod(usbProxy.getClass(), "getDeviceList", 1);
        getDeviceList.invoke(usbProxy, devices);
        for (String key : devices.keySet()) {
            Object value = devices.get(key);
            if (!(value instanceof UsbDevice)) {
                continue;
            }
            UsbDevice device = (UsbDevice) value;
            System.out.println("usbDevice " + describeUsbDevice(device));
            if (device.getVendorId() == THERMAL_VENDOR_ID &&
                    device.getProductId() == THERMAL_PRODUCT_ID) {
                return device;
            }
        }
        return null;
    }

    private static int getPackageUid(String packageName, int userId) throws Exception {
        Class<?> appGlobals = Class.forName("android.app.AppGlobals");
        Object packageManager = appGlobals.getMethod("getPackageManager").invoke(null);
        for (Method method : packageManager.getClass().getMethods()) {
            if (!"getPackageUid".equals(method.getName())) {
                continue;
            }
            Class<?>[] types = method.getParameterTypes();
            Object result;
            if (types.length == 3 && types[0] == String.class &&
                    types[1] == long.class && types[2] == int.class) {
                result = method.invoke(packageManager, packageName, 0L, userId);
            } else if (types.length == 3 && types[0] == String.class &&
                    types[1] == int.class && types[2] == int.class) {
                result = method.invoke(packageManager, packageName, 0, userId);
            } else if (types.length == 2 && types[0] == String.class &&
                    types[1] == int.class) {
                result = method.invoke(packageManager, packageName, userId);
            } else {
                continue;
            }
            return ((Integer) result).intValue();
        }
        throw new NoSuchMethodException("IPackageManager.getPackageUid");
    }

    private static Method findMethod(Class<?> owner, String name, int parameterCount)
            throws NoSuchMethodException {
        for (Method method : owner.getMethods()) {
            if (name.equals(method.getName()) &&
                    method.getParameterTypes().length == parameterCount) {
                return method;
            }
        }
        throw new NoSuchMethodException(owner.getName() + "." + name +
                " parameterCount=" + parameterCount);
    }

    private static void transactSetFixedHandler(IBinder binder, ComponentName component)
            throws Exception {
        int code = findTransactionCode();
        Parcel data = Parcel.obtain();
        Parcel reply = Parcel.obtain();
        try {
            data.writeInterfaceToken(DESCRIPTOR);
            data.writeTypedObject(component, 0);
            boolean ok = binder.transact(code, data, reply, 0);
            reply.readException();
            System.out.println("setFixedHandler transact OK code=" + code +
                    " component=" + component + " transactResult=" + ok);
        } finally {
            reply.recycle();
            data.recycle();
        }
    }

    private static int findTransactionCode() throws Exception {
        Class<?> stub = Class.forName("android.hardware.usb.IUsbManager$Stub");
        for (Field field : stub.getDeclaredFields()) {
            if ("TRANSACTION_setUsbDeviceConnectionHandler".equals(field.getName())) {
                field.setAccessible(true);
                return field.getInt(null);
            }
        }
        throw new NoSuchFieldException("TRANSACTION_setUsbDeviceConnectionHandler");
    }

    private static String describeMethod(Method method) {
        StringBuilder builder = new StringBuilder(method.getName()).append('(');
        Class<?>[] types = method.getParameterTypes();
        for (int i = 0; i < types.length; i++) {
            if (i > 0) {
                builder.append(',');
            }
            builder.append(types[i].getName());
        }
        return builder.append(')').toString();
    }

    private static String describeUsbDevice(UsbDevice device) {
        if (device == null) {
            return "null";
        }
        return device.getDeviceName() +
                " vendor=0x" + Integer.toHexString(device.getVendorId()) +
                " product=0x" + Integer.toHexString(device.getProductId()) +
                " class=" + device.getDeviceClass() +
                " interfaces=" + device.getInterfaceCount();
    }

    private static Throwable unwrap(Throwable t) {
        Throwable current = t;
        while (current.getCause() != null) {
            current = current.getCause();
        }
        return current;
    }
}
