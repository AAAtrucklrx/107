package cn.edu.ustc.xiaowo;

import android.app.DownloadManager;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Base64;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import androidx.annotation.RequiresApi;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.util.Locale;
import java.util.concurrent.Executor;
import org.json.JSONException;
import org.json.JSONObject;

final class DownloadStore {
    static final int MAX_DECODED_BYTES = 8 * 1024 * 1024;
    private static final int MAX_ENCODED_CHARS = ((MAX_DECODED_BYTES + 2) / 3) * 4;
    private static final int MAX_PAYLOAD_CHARS = 12 * 1024 * 1024;

    interface Callback {
        void onComplete(Result result);
    }

    static final class Preview {
        final String filename;
        final String mimeType;
        final int estimatedBytes;

        Preview(String filename, String mimeType, int estimatedBytes) {
            this.filename = filename;
            this.mimeType = mimeType;
            this.estimatedBytes = estimatedBytes;
        }
    }

    static final class Result {
        final String destination;
        final Exception error;

        private Result(String destination, Exception error) {
            this.destination = destination;
            this.error = error;
        }

        static Result success(String destination) {
            return new Result(destination, null);
        }

        static Result failure(Exception error) {
            return new Result(null, error);
        }

        boolean isSuccess() {
            return error == null;
        }
    }

    private DownloadStore() {}

    static long enqueueUrlDownload(
        Context context,
        Uri uri,
        String userAgent,
        String contentDisposition,
        String mimeType
    ) {
        if (!(NavigationPolicy.isMainOrigin(uri) || NavigationPolicy.isHttps(uri))) {
            throw new IllegalArgumentException("Download URL is not allowed");
        }
        String filename = sanitizeFilename(URLUtil.guessFileName(uri.toString(), contentDisposition, mimeType));
        String cleanMime = sanitizeMimeType(mimeType);
        DownloadManager.Request request = new DownloadManager.Request(uri)
            .setTitle(filename)
            .setDescription(context.getString(R.string.download_notification_description))
            .setMimeType(cleanMime)
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setAllowedOverMetered(true)
            .setAllowedOverRoaming(false);
        if (userAgent != null && !userAgent.isBlank()) request.addRequestHeader("User-Agent", userAgent);
        String cookie = CookieManager.getInstance().getCookie(uri.toString());
        if (cookie != null && !cookie.isBlank()) request.addRequestHeader("Cookie", cookie);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "小蜗/" + filename);
        } else {
            request.setDestinationInExternalFilesDir(context, Environment.DIRECTORY_DOWNLOADS, filename);
        }
        DownloadManager manager = (DownloadManager) context.getSystemService(Context.DOWNLOAD_SERVICE);
        if (manager == null) throw new IllegalStateException("Download manager is unavailable");
        return manager.enqueue(request);
    }

    static Preview inspectPayload(String payload) throws JSONException {
        JSONObject object = parsePayloadObject(payload);
        String encoded = object.optString("data", "");
        if (encoded.isEmpty() || encoded.length() > MAX_ENCODED_CHARS) {
            throw new JSONException("Encoded file has an invalid size");
        }
        int estimatedBytes = Math.min(MAX_DECODED_BYTES, (encoded.length() * 3) / 4);
        return new Preview(
            sanitizeFilename(object.optString("name", "download.bin")),
            sanitizeMimeType(object.optString("mime", "application/octet-stream")),
            estimatedBytes
        );
    }

    static void saveBase64Async(Context context, String payload, Executor executor, Callback callback) {
        Context appContext = context.getApplicationContext();
        executor.execute(() -> {
            try {
                JSONObject object = parsePayloadObject(payload);
                String filename = sanitizeFilename(object.optString("name", "download.bin"));
                String mime = sanitizeMimeType(object.optString("mime", "application/octet-stream"));
                String encoded = object.optString("data", "");
                if (encoded.isEmpty() || encoded.length() > MAX_ENCODED_CHARS) throw new IOException("Encoded file is too large");
                byte[] bytes = Base64.decode(encoded, Base64.DEFAULT);
                if (bytes.length == 0 || bytes.length > MAX_DECODED_BYTES) throw new IOException("Invalid file size");
                String destination = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q
                    ? saveToMediaStore(appContext, filename, mime, bytes)
                    : saveToAppDownloads(appContext, filename, bytes);
                callback.onComplete(Result.success(destination));
            } catch (Exception error) {
                callback.onComplete(Result.failure(error));
            }
        });
    }

    private static JSONObject parsePayloadObject(String payload) throws JSONException {
        if (!isPayloadSizeAllowed(payload)) throw new JSONException("Payload is too large");
        return new JSONObject(payload == null ? "{}" : payload);
    }

    @RequiresApi(api = Build.VERSION_CODES.Q)
    private static String saveToMediaStore(Context context, String filename, String mime, byte[] bytes) throws IOException {
        ContentResolver resolver = context.getContentResolver();
        ContentValues values = new ContentValues();
        values.put(MediaStore.Downloads.DISPLAY_NAME, filename);
        values.put(MediaStore.Downloads.MIME_TYPE, mime);
        values.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/小蜗");
        values.put(MediaStore.Downloads.IS_PENDING, 1);
        Uri uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
        if (uri == null) throw new IOException("Unable to create download");

        try (OutputStream output = resolver.openOutputStream(uri)) {
            if (output == null) throw new IOException("Unable to open download");
            output.write(bytes);
        } catch (IOException error) {
            resolver.delete(uri, null, null);
            throw error;
        }

        values.clear();
        values.put(MediaStore.Downloads.IS_PENDING, 0);
        resolver.update(uri, values, null, null);
        return Environment.DIRECTORY_DOWNLOADS + "/小蜗/" + filename;
    }

    private static String saveToAppDownloads(Context context, String filename, byte[] bytes) throws IOException {
        File directory = context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
        if (directory == null || (!directory.exists() && !directory.mkdirs())) throw new IOException("Unable to create directory");
        File file = uniqueFile(directory, filename);
        try (OutputStream output = new FileOutputStream(file)) {
            output.write(bytes);
        }
        return file.getAbsolutePath();
    }

    private static File uniqueFile(File directory, String filename) {
        File candidate = new File(directory, filename);
        if (!candidate.exists()) return candidate;
        int dot = filename.lastIndexOf('.');
        String stem = dot > 0 ? filename.substring(0, dot) : filename;
        String extension = dot > 0 ? filename.substring(dot) : "";
        for (int index = 2; index < 10_000; index++) {
            candidate = new File(directory, stem + " (" + index + ")" + extension);
            if (!candidate.exists()) return candidate;
        }
        return new File(directory, System.currentTimeMillis() + "-" + filename);
    }

    static String sanitizeFilename(String filename) {
        String value = filename == null ? "" : filename.trim();
        StringBuilder clean = new StringBuilder();
        String forbidden = "\\/:*?\"<>|";
        for (int index = 0; index < value.length() && clean.length() < 120; index++) {
            char character = value.charAt(index);
            clean.append(character <= 31 || forbidden.indexOf(character) >= 0 ? '_' : character);
        }
        String result = clean.toString().trim();
        return result.isEmpty() || ".".equals(result) || "..".equals(result) ? "download.bin" : result;
    }

    static String sanitizeMimeType(String mimeType) {
        String value = mimeType == null ? "" : mimeType.trim().toLowerCase(Locale.ROOT);
        return value.matches("[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+") ? value : "application/octet-stream";
    }

    static boolean isPayloadSizeAllowed(String payload) {
        return payload == null || payload.length() <= MAX_PAYLOAD_CHARS;
    }
}
