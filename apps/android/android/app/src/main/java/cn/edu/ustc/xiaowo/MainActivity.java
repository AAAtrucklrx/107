package cn.edu.ustc.xiaowo;

import android.annotation.SuppressLint;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.res.Configuration;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.format.Formatter;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.JsPromptResult;
import android.webkit.URLUtil;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;
import androidx.activity.OnBackPressedCallback;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.graphics.Insets;
import androidx.core.splashscreen.SplashScreen;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;
import androidx.webkit.WebSettingsCompat;
import androidx.webkit.WebViewFeature;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends AppCompatActivity {
    private static final long EXIT_CONFIRMATION_WINDOW_MS = 2_000L;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService downloadExecutor = Executors.newSingleThreadExecutor();
    private WebView mainWebView;
    private ProgressBar progressView;
    private View errorView;
    private TextView errorText;
    private TextView demoBanner;
    private Uri lastRequestedUri;
    private boolean mainFrameFailed;
    private long lastBackPressAt;
    private UpdateChecker updateChecker;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        SplashScreen.installSplashScreen(this);
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        configureSystemBars();

        mainWebView = findViewById(R.id.main_webview);
        progressView = findViewById(R.id.main_progress);
        errorView = findViewById(R.id.main_error);
        errorText = findViewById(R.id.main_error_text);
        demoBanner = findViewById(R.id.demo_banner);
        findViewById(R.id.main_retry).setOnClickListener(view -> retry());

        try {
            configureDemoBanner();
            configureMainWebView();
            configureBackNavigation();
            boolean restored = savedInstanceState != null && mainWebView.restoreState(savedInstanceState) != null;
            if (!restored) loadMainUri(AppConfig.homeUri());
            updateChecker = new UpdateChecker(this);
            updateChecker.maybeCheck();
            handleIntent(getIntent());
        } catch (IllegalStateException error) {
            showConfigurationError();
        }
    }

    private void configureSystemBars() {
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        View root = findViewById(R.id.main_root);
        ViewCompat.setOnApplyWindowInsetsListener(root, (view, windowInsets) -> {
            Insets bars = windowInsets.getInsets(WindowInsetsCompat.Type.systemBars());
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return windowInsets;
        });
        updateSystemBarAppearance();
    }

    private void updateSystemBarAppearance() {
        boolean night = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
        WindowInsetsControllerCompat controller = WindowCompat.getInsetsController(getWindow(), getWindow().getDecorView());
        controller.setAppearanceLightStatusBars(!night);
        controller.setAppearanceLightNavigationBars(!night);
    }

    private void configureDemoBanner() {
        if (!BuildConfig.XIAOWO_DEMO_BUILD) {
            demoBanner.setVisibility(View.GONE);
            return;
        }
        demoBanner.setText(getString(R.string.demo_security_banner, NavigationPolicy.displayAuthority(AppConfig.serverOrigin())));
        demoBanner.setVisibility(View.VISIBLE);
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void configureMainWebView() {
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        WebSettings settings = mainWebView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setJavaScriptCanOpenWindowsAutomatically(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setGeolocationEnabled(false);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSupportMultipleWindows(true);
        settings.setSaveFormData(false);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setTextZoom(100);
        String userAgent = settings.getUserAgentString();
        String suffix = AppConfig.userAgentSuffix();
        if (!userAgent.contains(suffix)) settings.setUserAgentString(userAgent + " " + suffix);

        if (WebViewFeature.isFeatureSupported(WebViewFeature.SAFE_BROWSING_ENABLE)) {
            WebSettingsCompat.setSafeBrowsingEnabled(settings, true);
        }

        CookieManager cookieManager = CookieManager.getInstance();
        cookieManager.setAcceptCookie(true);
        cookieManager.setAcceptThirdPartyCookies(mainWebView, false);

        mainWebView.setWebViewClient(new XiaowoWebViewClient(this));
        mainWebView.setWebChromeClient(new XiaowoWebChromeClient(this));
        mainWebView.setDownloadListener(this::confirmUrlDownload);
    }

    private void configureBackNavigation() {
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                handleBackPress();
            }
        });
    }

    private void handleBackPress() {
        if (errorView.getVisibility() == View.VISIBLE && lastRequestedUri != null && !NavigationPolicy.isMainHome(lastRequestedUri)) {
            loadMainUri(AppConfig.homeUri());
            return;
        }
        Uri current = Uri.parse(mainWebView.getUrl() == null ? "" : mainWebView.getUrl());
        if (mainWebView.canGoBack() && !NavigationPolicy.isMainHome(current)) {
            mainWebView.goBack();
            return;
        }

        long now = SystemClock.elapsedRealtime();
        if (now - lastBackPressAt <= EXIT_CONFIRMATION_WINDOW_MS) {
            finishAfterTransition();
            return;
        }
        lastBackPressAt = now;
        Toast.makeText(this, R.string.back_again_to_exit, Toast.LENGTH_SHORT).show();
    }

    boolean handleMainFrameNavigation(Uri uri) {
        return switch (NavigationPolicy.routeMainFrame(uri)) {
            case ALLOW_IN_MAIN -> {
                if (NavigationPolicy.isMainOrigin(uri)) lastRequestedUri = uri;
                yield false;
            }
            case OPEN_CUSTOM_TAB -> {
                ExternalLinkLauncher.openWeb(this, uri);
                yield true;
            }
            case OPEN_EXTERNAL_APP -> {
                ExternalLinkLauncher.openExternalApp(this, uri);
                yield true;
            }
            case BLOCK -> {
                showBlockedNavigation();
                yield true;
            }
        };
    }

    void openPopupUri(Uri uri) {
        if (!handleMainFrameNavigation(uri) && NavigationPolicy.isMainOrigin(uri)) loadMainUri(uri);
    }

    void onMainPageStarted(Uri uri) {
        mainFrameFailed = false;
        if (NavigationPolicy.isMainOrigin(uri)) lastRequestedUri = uri;
        errorView.setVisibility(View.GONE);
        mainWebView.setVisibility(View.VISIBLE);
        progressView.setVisibility(View.VISIBLE);
    }

    void onMainPageFinished(Uri uri) {
        if (!mainFrameFailed) {
            errorView.setVisibility(View.GONE);
            mainWebView.setVisibility(View.VISIBLE);
        }
        progressView.setVisibility(View.GONE);
        if (NavigationPolicy.isMainOrigin(uri)) lastRequestedUri = uri;
    }

    void onMainPageError(Uri uri) {
        mainFrameFailed = true;
        if (NavigationPolicy.isMainOrigin(uri)) lastRequestedUri = uri;
        progressView.setVisibility(View.GONE);
        errorText.setText(R.string.main_connection_error);
        errorView.setVisibility(View.VISIBLE);
    }

    void onUnsafePageBlocked(Uri uri) {
        mainFrameFailed = true;
        progressView.setVisibility(View.GONE);
        errorText.setText(R.string.unsafe_page_blocked);
        errorView.setVisibility(View.VISIBLE);
    }

    void onRendererGone(boolean crashed) {
        WebView failedView = mainWebView;
        if (failedView != null) {
            ViewGroup parent = (ViewGroup) failedView.getParent();
            if (parent != null) parent.removeView(failedView);
            failedView.destroy();
            mainWebView = null;
        }
        Toast.makeText(this, crashed ? R.string.web_renderer_crashed : R.string.web_renderer_restarted, Toast.LENGTH_LONG).show();
        mainHandler.post(this::recreate);
    }

    void onPageProgress(int progress) {
        progressView.setProgress(progress);
        progressView.setVisibility(progress >= 100 || mainFrameFailed ? View.GONE : View.VISIBLE);
    }

    void confirmCompatibilityRequest(WebCompatibility.Request request, JsPromptResult promptResult) {
        if (request.type == WebCompatibility.Type.COPY) {
            String preview = request.clipboardText.length() > 160
                ? request.clipboardText.substring(0, 160) + "…"
                : request.clipboardText;
            new AlertDialog.Builder(this)
                .setTitle(R.string.clipboard_confirm_title)
                .setMessage(preview)
                .setPositiveButton(R.string.copy_label, (dialog, which) -> {
                    WebCompatibility.copyToClipboard(this, request.clipboardText);
                    promptResult.confirm("");
                })
                .setNegativeButton(R.string.cancel_label, (dialog, which) -> promptResult.cancel())
                .setOnCancelListener(dialog -> promptResult.cancel())
                .show();
            return;
        }

        DownloadStore.Preview preview = request.downloadPreview;
        String size = Formatter.formatShortFileSize(this, preview.estimatedBytes);
        new AlertDialog.Builder(this)
            .setTitle(R.string.download_confirm_title)
            .setMessage(getString(R.string.download_confirm_message, preview.filename, size))
            .setPositiveButton(R.string.save_label, (dialog, which) -> {
                promptResult.confirm("");
                saveBlobDownload(request.payload);
            })
            .setNegativeButton(R.string.cancel_label, (dialog, which) -> promptResult.cancel())
            .setOnCancelListener(dialog -> promptResult.cancel())
            .show();
    }

    private void saveBlobDownload(String payload) {
        DownloadStore.saveBase64Async(getApplicationContext(), payload, downloadExecutor, result -> mainHandler.post(() -> {
            if (isFinishing() || isDestroyed()) return;
            if (result.isSuccess()) {
                Toast.makeText(this, getString(R.string.download_saved, result.destination), Toast.LENGTH_LONG).show();
            } else {
                Toast.makeText(this, R.string.download_failed, Toast.LENGTH_LONG).show();
            }
        }));
    }

    private void confirmUrlDownload(String url, String userAgent, String contentDisposition, String mimeType, long contentLength) {
        Uri uri = Uri.parse(url == null ? "" : url);
        if (!(NavigationPolicy.isMainOrigin(uri) || NavigationPolicy.isHttps(uri))) {
            showBlockedNavigation();
            return;
        }
        String filename = DownloadStore.sanitizeFilename(URLUtil.guessFileName(url, contentDisposition, mimeType));
        String size = contentLength > 0 ? Formatter.formatShortFileSize(this, contentLength) : getString(R.string.unknown_size);
        new AlertDialog.Builder(this)
            .setTitle(R.string.download_confirm_title)
            .setMessage(getString(R.string.download_confirm_message, filename, size))
            .setPositiveButton(R.string.save_label, (dialog, which) -> enqueueUrlDownload(uri, userAgent, contentDisposition, mimeType))
            .setNegativeButton(R.string.cancel_label, null)
            .show();
    }

    private void enqueueUrlDownload(Uri uri, String userAgent, String contentDisposition, String mimeType) {
        try {
            DownloadStore.enqueueUrlDownload(getApplicationContext(), uri, userAgent, contentDisposition, mimeType);
            Toast.makeText(this, R.string.download_started, Toast.LENGTH_LONG).show();
        } catch (RuntimeException error) {
            Toast.makeText(this, R.string.download_failed, Toast.LENGTH_LONG).show();
        }
    }

    private void retry() {
        loadMainUri(lastRequestedUri == null ? AppConfig.homeUri() : lastRequestedUri);
    }

    private void loadMainUri(Uri uri) {
        if (mainWebView == null || !NavigationPolicy.isMainOrigin(uri)) return;
        lastRequestedUri = uri;
        mainFrameFailed = false;
        errorView.setVisibility(View.GONE);
        mainWebView.setVisibility(View.VISIBLE);
        mainWebView.loadUrl(uri.toString());
    }

    private void showConfigurationError() {
        mainFrameFailed = true;
        progressView.setVisibility(View.GONE);
        if (mainWebView != null) mainWebView.setVisibility(View.INVISIBLE);
        errorText.setText(R.string.invalid_configuration);
        errorView.setVisibility(View.VISIBLE);
        findViewById(R.id.main_retry).setVisibility(View.GONE);
    }

    private void showBlockedNavigation() {
        Toast.makeText(this, R.string.external_navigation_blocked, Toast.LENGTH_LONG).show();
    }

    private void handleIntent(Intent intent) {
        if (intent == null || intent.getData() == null || mainWebView == null) return;
        Uri uri = intent.getData();
        if (NavigationPolicy.isMainOrigin(uri)) loadMainUri(uri);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIntent(intent);
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        if (mainWebView != null) mainWebView.saveState(outState);
        super.onSaveInstanceState(outState);
    }

    @Override
    protected void onPause() {
        CookieManager.getInstance().flush();
        super.onPause();
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        updateSystemBarAppearance();
    }

    @Override
    protected void onDestroy() {
        if (updateChecker != null) updateChecker.close();
        downloadExecutor.shutdownNow();
        if (mainWebView != null) {
            mainWebView.stopLoading();
            mainWebView.setDownloadListener(null);
            mainWebView.setWebChromeClient(null);
            mainWebView.setWebViewClient(null);
            mainWebView.destroy();
            mainWebView = null;
        }
        super.onDestroy();
    }

    WebView getMainWebViewForTesting() {
        return mainWebView;
    }

    View getErrorViewForTesting() {
        return errorView;
    }
}
