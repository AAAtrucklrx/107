package cn.edu.ustc.xiaowo;

import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.lang.ref.WeakReference;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import org.json.JSONObject;

final class UpdateChecker implements AutoCloseable {
    private static final String TAG = "XiaowoUpdate";
    private static final String PREFS = "xiaowo_update";
    private static final String LAST_ATTEMPT_AT = "last_attempt_at";
    private static final String LAST_SUCCESS_AT = "last_success_at";
    private static final int MAX_MANIFEST_CHARS = 128 * 1024;
    private static final long FAILURE_RETRY_DELAY_MS = 15L * 60L * 1000L;

    private final WeakReference<MainActivity> activityReference;
    private final Context appContext;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private Future<?> runningTask;

    UpdateChecker(MainActivity activity) {
        activityReference = new WeakReference<>(activity);
        appContext = activity.getApplicationContext();
    }

    void maybeCheck() {
        UpdateConfig config;
        try {
            config = UpdateConfig.load(appContext);
        } catch (Exception error) {
            Log.w(TAG, "Update configuration is invalid", error);
            return;
        }
        if (!config.enabled) return;
        if (config.requireHttps && !NavigationPolicy.isHttps(config.manifestUri)) {
            Log.w(TAG, "Update checks remain disabled until the manifest uses HTTPS");
            return;
        }

        SharedPreferences preferences = appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        long now = System.currentTimeMillis();
        long intervalMillis = config.checkIntervalHours * 60L * 60L * 1000L;
        if (!shouldStartCheck(
            now,
            preferences.getLong(LAST_SUCCESS_AT, 0L),
            preferences.getLong(LAST_ATTEMPT_AT, 0L),
            intervalMillis
        )) return;
        preferences.edit().putLong(LAST_ATTEMPT_AT, now).apply();

        runningTask = executor.submit(() -> {
            try {
                UpdateManifest manifest = UpdateManifest.parse(fetch(config.manifestUri));
                preferences.edit().putLong(LAST_SUCCESS_AT, System.currentTimeMillis()).apply();
                if (manifest.versionCode > BuildConfig.VERSION_CODE) {
                    mainHandler.post(() -> showAvailableUpdate(manifest));
                }
            } catch (Exception error) {
                Log.w(TAG, "Unable to check for updates", error);
            }
        });
    }

    static boolean shouldStartCheck(long now, long lastSuccessAt, long lastAttemptAt, long successIntervalMillis) {
        if (lastSuccessAt > 0L && now - lastSuccessAt < successIntervalMillis) return false;
        return lastAttemptAt <= 0L || now - lastAttemptAt >= FAILURE_RETRY_DELAY_MS;
    }

    private static String fetch(Uri manifestUri) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(manifestUri.toString()).openConnection();
        connection.setConnectTimeout(8_000);
        connection.setReadTimeout(8_000);
        connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("User-Agent", AppConfig.userAgentSuffix());
        try {
            int status = connection.getResponseCode();
            if (status != HttpURLConnection.HTTP_OK) throw new IOException("Unexpected HTTP status " + status);
            String contentType = connection.getContentType();
            if (contentType != null && !contentType.toLowerCase(Locale.ROOT).contains("json")) {
                throw new IOException("Unexpected update manifest content type");
            }
            try (InputStream input = connection.getInputStream()) {
                return readLimited(input);
            }
        } finally {
            connection.disconnect();
        }
    }

    private static String readLimited(InputStream input) throws IOException {
        StringBuilder output = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(input, StandardCharsets.UTF_8))) {
            char[] buffer = new char[4_096];
            int read;
            while ((read = reader.read(buffer)) != -1) {
                output.append(buffer, 0, read);
                if (output.length() > MAX_MANIFEST_CHARS) throw new IOException("Manifest is too large");
            }
        }
        return output.toString();
    }

    private void showAvailableUpdate(UpdateManifest manifest) {
        MainActivity activity = activityReference.get();
        if (activity == null || activity.isFinishing() || activity.isDestroyed()) return;
        String digest = manifest.sha256.substring(0, 12) + "…";
        String notes = manifest.releaseNotes.isEmpty() ? "SHA-256: " + digest : manifest.releaseNotes + "\n\nSHA-256: " + digest;
        new AlertDialog.Builder(activity)
            .setTitle(R.string.update_available_title)
            .setMessage(activity.getString(R.string.update_available_message, manifest.versionName, notes))
            .setPositiveButton(R.string.update_open_page, (dialog, which) -> ExternalLinkLauncher.openWeb(activity, manifest.downloadUri))
            .setNegativeButton(R.string.update_later, null)
            .show();
    }

    @Override
    public void close() {
        if (runningTask != null) runningTask.cancel(true);
        executor.shutdownNow();
        activityReference.clear();
    }

    private static final class UpdateConfig {
        final boolean enabled;
        final Uri manifestUri;
        final int checkIntervalHours;
        final boolean requireHttps;

        private UpdateConfig(boolean enabled, Uri manifestUri, int checkIntervalHours, boolean requireHttps) {
            this.enabled = enabled;
            this.manifestUri = manifestUri;
            this.checkIntervalHours = checkIntervalHours;
            this.requireHttps = requireHttps;
        }

        static UpdateConfig load(Context context) throws Exception {
            try (InputStream input = context.getResources().openRawResource(R.raw.update_config)) {
                JSONObject object = new JSONObject(readLimited(input));
                boolean enabled = object.getBoolean("enabled");
                Uri manifestUri = Uri.parse(object.getString("manifestUrl"));
                int interval = object.optInt("checkIntervalHours", 24);
                boolean requireHttps = object.optBoolean("requireHttps", true);
                if (interval < 1 || interval > 168 || !NavigationPolicy.isWebUri(manifestUri)) {
                    throw new IllegalArgumentException("Invalid update configuration");
                }
                return new UpdateConfig(enabled, manifestUri, interval, requireHttps);
            }
        }
    }
}
