package com.yegmina.thermallivedebug;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.IBinder;
import android.util.DisplayMetrics;
import android.view.WindowManager;

import java.io.File;
import java.io.FileOutputStream;
import java.io.FileWriter;
import java.nio.ByteBuffer;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

public class ThermalScreenCaptureService extends Service {
    public static final String ACTION_START =
            "com.yegmina.thermallivedebug.action.START_SCREEN_CAPTURE";
    public static final String ACTION_STOP =
            "com.yegmina.thermallivedebug.action.STOP_SCREEN_CAPTURE";
    public static final String EXTRA_RESULT_CODE = "resultCode";
    public static final String EXTRA_RESULT_DATA = "resultData";

    private static final int NOTIFICATION_ID = 2001;
    private static final String CHANNEL_ID = "thermal_screen_capture";

    private MediaProjection projection;
    private VirtualDisplay virtualDisplay;
    private ImageReader imageReader;
    private File captureDir;
    private File statusFile;
    private long lastSavedAt;
    private int frameCount;
    private int width;
    private int height;
    private int densityDpi;

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) {
            return START_NOT_STICKY;
        }
        String action = intent.getAction();
        if (ACTION_STOP.equals(action)) {
            writeStatus("stop requested");
            stopCapture();
            stopSelf();
            return START_NOT_STICKY;
        }
        if (ACTION_START.equals(action)) {
            startForegroundCompat();
            int resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, 0);
            Intent resultData = intent.getParcelableExtra(EXTRA_RESULT_DATA);
            startCapture(resultCode, resultData);
            return START_STICKY;
        }
        return START_NOT_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        stopCapture();
        super.onDestroy();
    }

    private void startForegroundCompat() {
        NotificationManager manager =
                (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID,
                    "Thermal screen capture",
                    NotificationManager.IMPORTANCE_LOW);
            manager.createNotificationChannel(channel);
        }
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        Notification notification = builder
                .setContentTitle("Thermal Live Debug")
                .setContentText("Capturing ThermoVue screen")
                .setSmallIcon(android.R.drawable.presence_video_online)
                .setOngoing(true)
                .build();
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(
                    NOTIFICATION_ID,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private void startCapture(int resultCode, Intent resultData) {
        stopCapture();
        captureDir = new File(getExternalFilesDir(null), "screen_capture");
        //noinspection ResultOfMethodCallIgnored
        captureDir.mkdirs();
        statusFile = new File(captureDir, "capture_status.txt");
        writeStatus("starting media projection");

        if (resultData == null) {
            writeStatus("FAIL resultData=null");
            stopSelf();
            return;
        }

        DisplayMetrics metrics = new DisplayMetrics();
        WindowManager windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
        windowManager.getDefaultDisplay().getRealMetrics(metrics);
        width = metrics.widthPixels;
        height = metrics.heightPixels;
        densityDpi = metrics.densityDpi;

        MediaProjectionManager projectionManager =
                (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
        projection = projectionManager.getMediaProjection(resultCode, resultData);
        if (projection == null) {
            writeStatus("FAIL getMediaProjection returned null");
            stopSelf();
            return;
        }
        projection.registerCallback(new MediaProjection.Callback() {
            @Override
            public void onStop() {
                writeStatus("media projection stopped");
                stopCapture();
            }
        }, null);

        imageReader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2);
        imageReader.setOnImageAvailableListener(reader -> handleImage(reader), null);
        virtualDisplay = projection.createVirtualDisplay(
                "ThermalLiveDebugScreen",
                width,
                height,
                densityDpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                imageReader.getSurface(),
                null,
                null);
        writeStatus("capture running width=" + width + " height=" + height);
    }

    private void handleImage(ImageReader reader) {
        Image image = null;
        try {
            image = reader.acquireLatestImage();
            if (image == null) {
                return;
            }
            frameCount++;
            long now = System.currentTimeMillis();
            if (now - lastSavedAt < 700) {
                return;
            }
            lastSavedAt = now;

            Image.Plane plane = image.getPlanes()[0];
            ByteBuffer buffer = plane.getBuffer();
            int pixelStride = plane.getPixelStride();
            int rowStride = plane.getRowStride();
            int rowPadding = rowStride - pixelStride * width;
            int bitmapWidth = width + rowPadding / pixelStride;
            Bitmap padded = Bitmap.createBitmap(bitmapWidth, height, Bitmap.Config.ARGB_8888);
            padded.copyPixelsFromBuffer(buffer);
            Bitmap cropped = Bitmap.createBitmap(padded, 0, 0, width, height);
            padded.recycle();

            File latest = new File(captureDir, "latest_thermovue_screen.jpg");
            try (FileOutputStream output = new FileOutputStream(latest)) {
                cropped.compress(Bitmap.CompressFormat.JPEG, 88, output);
            }
            cropped.recycle();
            writeStatus("captured frames=" + frameCount +
                    " latest=" + latest.getAbsolutePath() +
                    " time=" + stamp());
        } catch (Throwable t) {
            writeStatus("capture FAIL " + t.getClass().getName() + ": " + t.getMessage());
        } finally {
            if (image != null) {
                image.close();
            }
        }
    }

    private void stopCapture() {
        if (virtualDisplay != null) {
            virtualDisplay.release();
            virtualDisplay = null;
        }
        if (imageReader != null) {
            imageReader.close();
            imageReader = null;
        }
        if (projection != null) {
            projection.stop();
            projection = null;
        }
        writeStatus("capture stopped frames=" + frameCount);
    }

    private void writeStatus(String line) {
        String stamped = stamp() + " " + line;
        if (statusFile == null) {
            File dir = new File(getExternalFilesDir(null), "screen_capture");
            //noinspection ResultOfMethodCallIgnored
            dir.mkdirs();
            statusFile = new File(dir, "capture_status.txt");
        }
        try (FileWriter writer = new FileWriter(statusFile, true)) {
            writer.write(stamped);
            writer.write('\n');
        } catch (Throwable ignored) {
            // Best-effort diagnostic file.
        }
    }

    private String stamp() {
        return new SimpleDateFormat("HH:mm:ss.SSS", Locale.US).format(new Date());
    }
}
