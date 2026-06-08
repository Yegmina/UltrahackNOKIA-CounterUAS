package com.yegmina.sensorprobe;

import android.Manifest;
import android.app.Activity;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.ImageFormat;
import android.hardware.Sensor;
import android.hardware.SensorManager;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbEndpoint;
import android.hardware.usb.UsbInterface;
import android.hardware.usb.UsbManager;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.Image;
import android.media.ImageReader;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Log;
import android.util.Size;
import android.view.Gravity;
import android.widget.TextView;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.Date;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;

public class MainActivity extends Activity {
    private static final String TAG = "SensorProbe";
    private static final int REQ_PERMISSIONS = 1001;
    private static final int FRAMES_PER_FORMAT = 3;
    private static final int FORMAT_Y16 = 0x20363159;
    private static final String ACTION_USB_PERMISSION = "com.yegmina.sensorprobe.USB_PERMISSION";

    private TextView text;
    private HandlerThread cameraThread;
    private Handler cameraHandler;
    private File outDir;
    private File logFile;
    private final StringBuilder logBuffer = new StringBuilder();

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        text = new TextView(this);
        text.setGravity(Gravity.START);
        text.setTextSize(13);
        text.setPadding(24, 24, 24, 24);
        text.setText("SensorProbe starting...\n");
        setContentView(text);

        cameraThread = new HandlerThread("camera-probe");
        cameraThread.start();
        cameraHandler = new Handler(cameraThread.getLooper());

        outDir = new File(getExternalFilesDir(null), "sensor_probe_" + stamp());
        //noinspection ResultOfMethodCallIgnored
        outDir.mkdirs();
        logFile = new File(outDir, "sensor_probe.log");

        if (!hasRuntimePermission(Manifest.permission.CAMERA) ||
                !hasRuntimePermission(Manifest.permission.RECORD_AUDIO)) {
            requestPermissions(new String[]{Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO}, REQ_PERMISSIONS);
        } else {
            startProbe();
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grants) {
        super.onRequestPermissionsResult(requestCode, permissions, grants);
        if (requestCode == REQ_PERMISSIONS &&
                hasRuntimePermission(Manifest.permission.CAMERA) &&
                hasRuntimePermission(Manifest.permission.RECORD_AUDIO)) {
            startProbe();
        } else {
            append("CAMERA or RECORD_AUDIO permission denied");
        }
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (cameraThread != null) {
            cameraThread.quitSafely();
        }
    }

    private boolean hasRuntimePermission(String permission) {
        return checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED;
    }

    private void startProbe() {
        append("Output dir: " + outDir.getAbsolutePath());
        new Thread(() -> {
            try {
                probeAllCameras();
            } catch (Throwable t) {
                append("FATAL: " + Log.getStackTraceString(t));
            }
        }, "probe-runner").start();
    }

    private void probeAllCameras() throws CameraAccessException {
        describeAndroidSensors();
        describeUsbDevices();

        CameraManager manager = (CameraManager) getSystemService(CAMERA_SERVICE);
        String[] ids = manager.getCameraIdList();
        append("Camera IDs: " + Arrays.toString(ids));

        for (String id : ids) {
            CameraCharacteristics cc = manager.getCameraCharacteristics(id);
            describeCamera(id, cc);
        }

        describeConcurrentCameraSupport(manager);
        probeAudio();
        if (ids.length > 0) {
            probeAudioWithCamera(manager, ids[0]);
        }

        for (String id : ids) {
            CameraCharacteristics cc = manager.getCameraCharacteristics(id);
            StreamConfigurationMap map = cc.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
            if (map == null) {
                append("camera " + id + ": no stream configuration map");
                continue;
            }
            for (int format : formatsToTry(map)) {
                Size size = pickSmallUsefulSize(map.getOutputSizes(format));
                if (size == null) {
                    continue;
                }
                append("TRY camera=" + id + " format=" + formatName(format) + "(" + format + ") size=" + size);
                try {
                    ProbeResult result = captureFrames(manager, id, format, size);
                    append("OK " + result.summary);
                } catch (Throwable t) {
                    append("FAIL camera=" + id + " format=" + formatName(format) + ": " + t.getClass().getSimpleName() + ": " + t.getMessage());
                }
            }
        }

        append("DONE. Pull with:");
        append("adb pull " + outDir.getAbsolutePath());
    }

    private void describeAndroidSensors() {
        append("");
        append("===== ANDROID SENSORS =====");
        SensorManager manager = (SensorManager) getSystemService(SENSOR_SERVICE);
        if (manager == null) {
            append("sensorManager=null");
            return;
        }
        List<Sensor> sensors = manager.getSensorList(Sensor.TYPE_ALL);
        append("sensorCount=" + sensors.size());
        for (Sensor sensor : sensors) {
            String searchable = (sensor.getName() + " " + sensor.getVendor() + " " + sensor.getStringType()).toLowerCase(Locale.US);
            boolean interesting = searchable.contains("thermal") ||
                    searchable.contains("therm") ||
                    searchable.contains("infra") ||
                    searchable.contains("ir") ||
                    searchable.contains("temp") ||
                    searchable.contains("uvc");
            append("sensor name=\"" + sensor.getName() + "\"" +
                    " vendor=\"" + sensor.getVendor() + "\"" +
                    " type=" + sensor.getType() +
                    " stringType=\"" + sensor.getStringType() + "\"" +
                    " maxRange=" + sensor.getMaximumRange() +
                    " resolution=" + sensor.getResolution() +
                    " minDelayUs=" + sensor.getMinDelay() +
                    " powerMa=" + sensor.getPower() +
                    (interesting ? " INTERESTING_NAME" : ""));
        }
    }

    private void describeUsbDevices() {
        append("");
        append("===== USB DEVICES =====");
        UsbManager manager = (UsbManager) getSystemService(USB_SERVICE);
        if (manager == null) {
            append("usbManager=null");
            return;
        }
        Map<String, UsbDevice> devices = manager.getDeviceList();
        append("usbDeviceCount=" + devices.size());
        for (Map.Entry<String, UsbDevice> entry : devices.entrySet()) {
            UsbDevice device = entry.getValue();
            append("usb key=\"" + entry.getKey() + "\"" +
                    " name=\"" + device.getDeviceName() + "\"" +
                    " vendorId=" + hex(device.getVendorId()) +
                    " productId=" + hex(device.getProductId()) +
                    " class=" + device.getDeviceClass() +
                    " subclass=" + device.getDeviceSubclass() +
                    " protocol=" + device.getDeviceProtocol() +
                    " interfaceCount=" + device.getInterfaceCount() +
                    " manufacturer=\"" + safeUsbText(() -> device.getManufacturerName()) + "\"" +
                    " product=\"" + safeUsbText(() -> device.getProductName()) + "\"" +
                    " hasPermission=" + manager.hasPermission(device));
            if (device.getVendorId() == 0x3474 && device.getProductId() == 0x4321) {
                probeThermalUsbDevice(manager, device);
            }
        }
    }

    private void probeThermalUsbDevice(UsbManager manager, UsbDevice device) {
        append("THERMAL_USB candidate found. Requesting/opening direct USB access.");
        boolean permitted = manager.hasPermission(device);
        if (!permitted) {
            permitted = requestUsbPermissionAndWait(manager, device);
        }
        append("THERMAL_USB permissionAfterRequest=" + permitted);
        if (!permitted) {
            return;
        }

        UsbDeviceConnection connection = manager.openDevice(device);
        if (connection == null) {
            append("THERMAL_USB openDevice=null");
            return;
        }
        try {
            byte[] raw = connection.getRawDescriptors();
            append("THERMAL_USB rawDescriptors bytes=" + (raw == null ? 0 : raw.length) +
                    " head=" + sampleHex(raw, 48));
            for (int i = 0; i < device.getInterfaceCount(); i++) {
                UsbInterface iface = device.getInterface(i);
                append("THERMAL_USB interface " + i +
                        " class=" + iface.getInterfaceClass() +
                        " subclass=" + iface.getInterfaceSubclass() +
                        " protocol=" + iface.getInterfaceProtocol() +
                        " endpoints=" + iface.getEndpointCount());
                boolean claimed = false;
                try {
                    claimed = connection.claimInterface(iface, true);
                } catch (Throwable t) {
                    append("THERMAL_USB claim interface " + i + " threw " + t.getClass().getSimpleName() + ": " + t.getMessage());
                }
                append("THERMAL_USB claim interface " + i + "=" + claimed);
                for (int j = 0; j < iface.getEndpointCount(); j++) {
                    UsbEndpoint ep = iface.getEndpoint(j);
                    append("THERMAL_USB endpoint i=" + i + " e=" + j +
                            " address=0x" + Integer.toHexString(ep.getAddress()) +
                            " direction=" + ep.getDirection() +
                            " type=" + ep.getType() +
                            " maxPacket=" + ep.getMaxPacketSize() +
                            " interval=" + ep.getInterval());
                }
                if (claimed) {
                    try {
                        connection.releaseInterface(iface);
                    } catch (Throwable ignored) {
                    }
                }
            }
        } finally {
            connection.close();
        }
    }

    private boolean requestUsbPermissionAndWait(UsbManager manager, UsbDevice device) {
        CountDownLatch latch = new CountDownLatch(1);
        final boolean[] granted = new boolean[]{false};
        BroadcastReceiver receiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                append("THERMAL_USB permission broadcast action=" + intent.getAction());
                if (ACTION_USB_PERMISSION.equals(intent.getAction())) {
                    UsbDevice received = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
                    append("THERMAL_USB permission broadcast device=" +
                            (received == null ? "null" : received.getDeviceName()) +
                            " granted=" + intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false));
                    if (received != null && received.getDeviceId() == device.getDeviceId()) {
                        granted[0] = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false);
                        latch.countDown();
                    }
                }
            }
        };
        IntentFilter filter = new IntentFilter(ACTION_USB_PERMISSION);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(receiver, filter, Context.RECEIVER_EXPORTED);
        } else {
            registerReceiver(receiver, filter);
        }
        try {
            Intent intent = new Intent(ACTION_USB_PERMISSION).setPackage(getPackageName());
            PendingIntent pendingIntent = PendingIntent.getBroadcast(
                    this,
                    device.getDeviceId(),
                    intent,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_MUTABLE);
            manager.requestPermission(device, pendingIntent);
            append("THERMAL_USB waiting for permission response...");
            if (!latch.await(60, TimeUnit.SECONDS)) {
                append("THERMAL_USB permission wait timed out");
            }
            return granted[0] || manager.hasPermission(device);
        } catch (Throwable t) {
            append("THERMAL_USB permission request failed: " + t.getClass().getSimpleName() + ": " + t.getMessage());
            return manager.hasPermission(device);
        } finally {
            try {
                unregisterReceiver(receiver);
            } catch (Throwable ignored) {
            }
        }
    }

    private void describeConcurrentCameraSupport(CameraManager manager) {
        append("");
        append("===== CONCURRENT CAMERA SUPPORT =====");
        if (Build.VERSION.SDK_INT < 30) {
            append("getConcurrentCameraIds unavailable before Android 11");
            return;
        }
        try {
            append("concurrentCameraIdSets=" + manager.getConcurrentCameraIds());
        } catch (Throwable t) {
            append("concurrentCameraIdSets failed: " + t.getClass().getSimpleName() + ": " + t.getMessage());
        }
    }

    private void probeAudio() {
        append("");
        append("===== AUDIO RECORD =====");
        try {
            AudioProbeResult result = recordMicForMs(1200);
            append("AUDIO OK " + result.summary);
        } catch (Throwable t) {
            append("AUDIO FAIL " + t.getClass().getSimpleName() + ": " + t.getMessage());
        }
    }

    private void probeAudioWithCamera(CameraManager manager, String id) {
        append("");
        append("===== AUDIO + CAMERA " + id + " =====");
        AudioRecord recorder = null;
        try {
            recorder = createAudioRecord();
            recorder.startRecording();
            ProbeResult result = captureSmallYuvFrames(manager, id);
            AudioProbeResult audioResult = readMic(recorder, 1200);
            append("AUDIO_CAMERA OK " + result.summary + " audio=" + audioResult.summary);
        } catch (Throwable t) {
            append("AUDIO_CAMERA FAIL " + t.getClass().getSimpleName() + ": " + t.getMessage());
        } finally {
            if (recorder != null) {
                try {
                    recorder.stop();
                } catch (Throwable ignored) {
                }
                recorder.release();
            }
        }
    }

    private void describeCamera(String id, CameraCharacteristics cc) {
        append("");
        append("===== CAMERA " + id + " =====");
        append("facing=" + facingName(cc.get(CameraCharacteristics.LENS_FACING)));
        append("hardwareLevel=" + cc.get(CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL));
        append("capabilities=" + intArray(cc.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES)));
        append("physicalSize=" + cc.get(CameraCharacteristics.SENSOR_INFO_PHYSICAL_SIZE));
        append("pixelArray=" + cc.get(CameraCharacteristics.SENSOR_INFO_PIXEL_ARRAY_SIZE));
        append("activeArray=" + cc.get(CameraCharacteristics.SENSOR_INFO_ACTIVE_ARRAY_SIZE));
        append("orientation=" + cc.get(CameraCharacteristics.SENSOR_ORIENTATION));

        StreamConfigurationMap map = cc.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
        if (map == null) {
            append("formats=[]");
            return;
        }
        int[] formats = map.getOutputFormats();
        Arrays.sort(formats);
        append("outputFormats=" + formatList(formats));
        for (int format : formats) {
            Size[] sizes = map.getOutputSizes(format);
            append("  " + formatName(format) + "(" + format + "): " + summarizeSizes(sizes));
        }

        List<String> suspiciousKeys = new ArrayList<>();
        for (CameraCharacteristics.Key<?> key : cc.getKeys()) {
            String name = key.getName().toLowerCase(Locale.US);
            if (name.contains("thermal") || name.contains("therm") || name.contains("infra") ||
                    name.contains("ir") || name.contains("yft") || name.contains("custom") ||
                    name.contains("sensor") || name.contains("camera")) {
                suspiciousKeys.add(key.getName());
            }
        }
        Collections.sort(suspiciousKeys);
        append("interestingKeys=" + suspiciousKeys);
    }

    private List<Integer> formatsToTry(StreamConfigurationMap map) {
        Set<Integer> wanted = new LinkedHashSet<>();
        int[] available = map.getOutputFormats();
        Set<Integer> have = new LinkedHashSet<>();
        for (int fmt : available) {
            have.add(fmt);
        }
        int[] preferred = new int[]{
                ImageFormat.YUV_420_888,
                ImageFormat.JPEG,
                ImageFormat.RAW_SENSOR,
                ImageFormat.RAW10,
                ImageFormat.RAW12,
                FORMAT_Y16,
                ImageFormat.DEPTH16,
                ImageFormat.YUV_422_888,
                ImageFormat.YUV_444_888,
                ImageFormat.FLEX_RGB_888,
                ImageFormat.FLEX_RGBA_8888
        };
        for (int fmt : preferred) {
            if (have.contains(fmt)) {
                wanted.add(fmt);
            }
        }
        return new ArrayList<>(wanted);
    }

    private ProbeResult captureSmallYuvFrames(CameraManager manager, String id) throws Exception {
        CameraCharacteristics cc = manager.getCameraCharacteristics(id);
        StreamConfigurationMap map = cc.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
        if (map == null) {
            throw new RuntimeException("camera " + id + " has no stream configuration map");
        }
        Size size = pickSmallUsefulSize(map.getOutputSizes(ImageFormat.YUV_420_888));
        if (size == null) {
            throw new RuntimeException("camera " + id + " has no YUV_420_888 output size");
        }
        return captureFrames(manager, id, ImageFormat.YUV_420_888, size);
    }

    private ProbeResult captureFrames(CameraManager manager, String id, int format, Size size) throws Exception {
        ImageReader reader = ImageReader.newInstance(size.getWidth(), size.getHeight(), format, 3);
        CountDownLatch openLatch = new CountDownLatch(1);
        CountDownLatch frameLatch = new CountDownLatch(FRAMES_PER_FORMAT);
        Semaphore firstImageSaved = new Semaphore(1);
        firstImageSaved.acquire();
        List<String> frameSummaries = Collections.synchronizedList(new ArrayList<>());
        CameraDevice[] holder = new CameraDevice[1];
        CameraCaptureSession[] sessionHolder = new CameraCaptureSession[1];

        reader.setOnImageAvailableListener(r -> {
            Image image = null;
            try {
                image = r.acquireLatestImage();
                if (image == null) {
                    return;
                }
                String summary = summarizeImage(image);
                frameSummaries.add(summary);
                if (!firstImageSaved.tryAcquire()) {
                    saveImage(id, format, image);
                }
            } catch (Throwable t) {
                frameSummaries.add("image-error=" + t.getClass().getSimpleName() + ":" + t.getMessage());
            } finally {
                if (image != null) {
                    image.close();
                }
                frameLatch.countDown();
            }
        }, cameraHandler);

        manager.openCamera(id, new CameraDevice.StateCallback() {
            @Override
            public void onOpened(CameraDevice camera) {
                holder[0] = camera;
                openLatch.countDown();
            }

            @Override
            public void onDisconnected(CameraDevice camera) {
                holder[0] = camera;
                openLatch.countDown();
            }

            @Override
            public void onError(CameraDevice camera, int error) {
                append("open error camera=" + id + " error=" + error);
                holder[0] = camera;
                openLatch.countDown();
            }
        }, cameraHandler);

        if (!openLatch.await(4, TimeUnit.SECONDS) || holder[0] == null) {
            reader.close();
            throw new RuntimeException("camera open timeout");
        }
        CameraDevice camera = holder[0];

        CountDownLatch sessionLatch = new CountDownLatch(1);
        camera.createCaptureSession(Collections.singletonList(reader.getSurface()), new CameraCaptureSession.StateCallback() {
            @Override
            public void onConfigured(CameraCaptureSession session) {
                sessionHolder[0] = session;
                sessionLatch.countDown();
            }

            @Override
            public void onConfigureFailed(CameraCaptureSession session) {
                sessionHolder[0] = session;
                sessionLatch.countDown();
            }
        }, cameraHandler);

        if (!sessionLatch.await(4, TimeUnit.SECONDS) || sessionHolder[0] == null) {
            camera.close();
            reader.close();
            throw new RuntimeException("session configure timeout");
        }
        CameraCaptureSession session = sessionHolder[0];
        CaptureRequest.Builder builder = camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
        builder.addTarget(reader.getSurface());
        session.setRepeatingRequest(builder.build(), null, cameraHandler);

        boolean gotFrames = frameLatch.await(6, TimeUnit.SECONDS);
        try {
            session.stopRepeating();
        } catch (Throwable ignored) {
        }
        session.close();
        camera.close();
        reader.close();

        String summary = "camera=" + id + " format=" + formatName(format) + " size=" + size +
                " gotFrames=" + (FRAMES_PER_FORMAT - frameLatch.getCount()) + "/" + FRAMES_PER_FORMAT +
                " complete=" + gotFrames + " firstSummaries=" + frameSummaries;
        return new ProbeResult(summary);
    }

    private AudioProbeResult recordMicForMs(int durationMs) {
        AudioRecord recorder = createAudioRecord();
        try {
            recorder.startRecording();
            return readMic(recorder, durationMs);
        } finally {
            try {
                recorder.stop();
            } catch (Throwable ignored) {
            }
            recorder.release();
        }
    }

    @SuppressWarnings("deprecation")
    private AudioRecord createAudioRecord() {
        int sampleRate = 16000;
        int minBuffer = AudioRecord.getMinBufferSize(
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT);
        if (minBuffer <= 0) {
            throw new RuntimeException("AudioRecord minBuffer=" + minBuffer);
        }
        int bufferSize = Math.max(minBuffer * 2, sampleRate);
        AudioRecord recorder = new AudioRecord(
                MediaRecorder.AudioSource.MIC,
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufferSize);
        if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
            recorder.release();
            throw new RuntimeException("AudioRecord not initialized");
        }
        return recorder;
    }

    private AudioProbeResult readMic(AudioRecord recorder, int durationMs) {
        byte[] buffer = new byte[4096];
        long deadline = System.currentTimeMillis() + durationMs;
        int reads = 0;
        int bytes = 0;
        int nonZero = 0;
        int maxAbs = 0;
        while (System.currentTimeMillis() < deadline) {
            int n = recorder.read(buffer, 0, buffer.length);
            if (n <= 0) {
                continue;
            }
            reads++;
            bytes += n;
            for (int i = 0; i + 1 < n; i += 2) {
                int sample = (short) ((buffer[i] & 0xff) | (buffer[i + 1] << 8));
                int abs = Math.abs(sample);
                if (abs > 0) {
                    nonZero++;
                }
                maxAbs = Math.max(maxAbs, abs);
            }
        }
        return new AudioProbeResult("reads=" + reads + " bytes=" + bytes +
                " nonZeroSamples=" + nonZero + " maxAbs=" + maxAbs);
    }

    private String summarizeImage(Image image) {
        StringBuilder sb = new StringBuilder();
        sb.append("image format=").append(formatName(image.getFormat())).append("(").append(image.getFormat()).append(")");
        sb.append(" size=").append(image.getWidth()).append("x").append(image.getHeight());
        sb.append(" timestamp=").append(image.getTimestamp());
        sb.append(" planes=").append(image.getPlanes().length);
        for (int i = 0; i < image.getPlanes().length; i++) {
            Image.Plane p = image.getPlanes()[i];
            ByteBuffer duplicate = p.getBuffer().duplicate();
            int sample = Math.min(duplicate.remaining(), 4096);
            int min = 255;
            int max = 0;
            long sum = 0;
            for (int j = 0; j < sample; j++) {
                int value = duplicate.get() & 0xff;
                min = Math.min(min, value);
                max = Math.max(max, value);
                sum += value;
            }
            double avg = sample > 0 ? (double) sum / sample : 0.0;
            sb.append(" plane").append(i)
                    .append("{row=").append(p.getRowStride())
                    .append(",pixel=").append(p.getPixelStride())
                    .append(",bytes=").append(p.getBuffer().remaining())
                    .append(",sampleMin=").append(min)
                    .append(",sampleMax=").append(max)
                    .append(",sampleAvg=").append(String.format(Locale.US, "%.2f", avg))
                    .append("}");
        }
        return sb.toString();
    }

    private void saveImage(String id, int format, Image image) throws IOException {
        String prefix = "camera_" + id + "_" + formatName(format).replace('/', '_') + "_" +
                image.getWidth() + "x" + image.getHeight();
        if (image.getFormat() == ImageFormat.JPEG) {
            ByteBuffer buf = image.getPlanes()[0].getBuffer();
            byte[] bytes = new byte[buf.remaining()];
            buf.get(bytes);
            writeBytes(new File(outDir, prefix + ".jpg"), bytes);
            return;
        }
        for (int i = 0; i < image.getPlanes().length; i++) {
            Image.Plane p = image.getPlanes()[i];
            ByteBuffer buf = p.getBuffer();
            byte[] bytes = new byte[buf.remaining()];
            buf.get(bytes);
            writeBytes(new File(outDir, prefix + "_plane" + i + ".bin"), bytes);
            if (i == 0) {
                writePgm(new File(outDir, prefix + "_plane0.pgm"), bytes, image.getWidth(), image.getHeight(), p.getRowStride());
            }
        }
    }

    private void writePgm(File file, byte[] plane, int width, int height, int rowStride) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.write(("P5\n" + width + " " + height + "\n255\n").getBytes());
        for (int y = 0; y < height; y++) {
            int row = y * rowStride;
            if (row + width <= plane.length) {
                out.write(plane, row, width);
            }
        }
        writeBytes(file, out.toByteArray());
    }

    private void writeBytes(File file, byte[] bytes) throws IOException {
        try (FileOutputStream out = new FileOutputStream(file)) {
            out.write(bytes);
        }
    }

    private Size pickSmallUsefulSize(Size[] sizes) {
        if (sizes == null || sizes.length == 0) {
            return null;
        }
        List<Size> list = new ArrayList<>(Arrays.asList(sizes));
        list.sort(Comparator.comparingInt(s -> s.getWidth() * s.getHeight()));
        Size fallback = list.get(0);
        for (Size s : list) {
            int pixels = s.getWidth() * s.getHeight();
            if (s.getWidth() >= 160 && s.getHeight() >= 120 && pixels <= 640 * 480) {
                return s;
            }
        }
        return fallback;
    }

    private String summarizeSizes(Size[] sizes) {
        if (sizes == null) {
            return "[]";
        }
        List<Size> list = new ArrayList<>(Arrays.asList(sizes));
        list.sort(Comparator.comparingInt((Size s) -> s.getWidth() * s.getHeight()).thenComparingInt(Size::getWidth));
        int limit = Math.min(12, list.size());
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < limit; i++) {
            if (i > 0) sb.append(", ");
            sb.append(list.get(i).getWidth()).append("x").append(list.get(i).getHeight());
        }
        if (list.size() > limit) {
            sb.append(", ... total=").append(list.size());
        }
        sb.append("]");
        return sb.toString();
    }

    private String formatList(int[] formats) {
        List<String> names = new ArrayList<>();
        for (int fmt : formats) {
            names.add(formatName(fmt) + "(" + fmt + ")");
        }
        return names.toString();
    }

    private String formatName(int format) {
        switch (format) {
            case ImageFormat.JPEG:
                return "JPEG";
            case ImageFormat.YUV_420_888:
                return "YUV_420_888";
            case ImageFormat.YUV_422_888:
                return "YUV_422_888";
            case ImageFormat.YUV_444_888:
                return "YUV_444_888";
            case ImageFormat.RAW_SENSOR:
                return "RAW_SENSOR";
            case ImageFormat.RAW10:
                return "RAW10";
            case ImageFormat.RAW12:
                return "RAW12";
            case ImageFormat.DEPTH16:
                return "DEPTH16";
            case FORMAT_Y16:
                return "Y16";
            case ImageFormat.FLEX_RGB_888:
                return "FLEX_RGB_888";
            case ImageFormat.FLEX_RGBA_8888:
                return "FLEX_RGBA_8888";
            case 34:
                return "PRIVATE";
            case 842094169:
                return "YV12";
            case 54:
                return "HEIC";
            default:
                return "FMT_" + format;
        }
    }

    private String facingName(Integer facing) {
        if (facing == null) return "null";
        switch (facing) {
            case CameraCharacteristics.LENS_FACING_BACK:
                return "BACK";
            case CameraCharacteristics.LENS_FACING_FRONT:
                return "FRONT";
            case CameraCharacteristics.LENS_FACING_EXTERNAL:
                return "EXTERNAL";
            default:
                return String.valueOf(facing);
        }
    }

    private String intArray(int[] values) {
        return values == null ? "null" : Arrays.toString(values);
    }

    private String hex(int value) {
        return String.format(Locale.US, "0x%04x", value);
    }

    private String sampleHex(byte[] bytes, int limit) {
        if (bytes == null) {
            return "null";
        }
        StringBuilder sb = new StringBuilder();
        int count = Math.min(bytes.length, limit);
        for (int i = 0; i < count; i++) {
            if (i > 0) sb.append(' ');
            sb.append(String.format(Locale.US, "%02x", bytes[i] & 0xff));
        }
        if (bytes.length > count) {
            sb.append(" ...");
        }
        return sb.toString();
    }

    private interface UsbTextSupplier {
        String get();
    }

    private String safeUsbText(UsbTextSupplier supplier) {
        try {
            String value = supplier.get();
            return value == null ? "" : value;
        } catch (Throwable t) {
            return t.getClass().getSimpleName();
        }
    }

    private String stamp() {
        return new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date());
    }

    private void append(String line) {
        Log.i(TAG, line);
        synchronized (logBuffer) {
            logBuffer.append(line).append('\n');
            try {
                if (logFile != null) {
                    writeBytes(logFile, logBuffer.toString().getBytes());
                }
            } catch (IOException ignored) {
            }
        }
        runOnUiThread(() -> text.append(line + "\n"));
    }

    private static class ProbeResult {
        final String summary;
        ProbeResult(String summary) {
            this.summary = summary;
        }
    }

    private static class AudioProbeResult {
        final String summary;
        AudioProbeResult(String summary) {
            this.summary = summary;
        }
    }
}
