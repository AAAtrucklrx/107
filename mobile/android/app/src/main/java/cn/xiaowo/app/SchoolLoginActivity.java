package cn.xiaowo.app;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.os.Bundle;
import android.graphics.Color;
import android.net.http.SslError;
import android.os.Build;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;


/** Untrusted school pages run in their own process, without Capacitor or any injected bridge. */
public final class SchoolLoginActivity extends Activity {
    private final LoginSession session = new LoginSession();
    private WebView schoolView;
    private LinearLayout content;
    private TextView status;
    private ProgressBar progress;
    private Button retry;
    private boolean importFailure;
    private boolean pageFailed;
    private boolean closing;
    private long attempt;
    @Override protected void onCreate(Bundle state) {
        super.onCreate(null);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        if (SchoolSessionService.current == null || !SchoolSessionService.current.hasClient()) { finish(); return; }
        attempt=session.begin();session.activate(attempt);
        try { createWindow();SchoolSessionService.current.attached(this); }
        catch(Exception ignored) { closeWindow(); }
    }
    boolean isActive() { return session.isActive() && !closing; }
    void showStatus(String state, String message) {
        if(!isActive()) return;
        importFailure="error".equals(state);
        renderStatus(state,message);
        if(schoolView!=null) schoolView.setVisibility("error".equals(state)?View.VISIBLE:View.INVISIBLE);
    }
    @Override public void onBackPressed() { closeWindow(); }
    @Override protected void onSaveInstanceState(Bundle state) { state.clear(); }
    @SuppressLint("SetJavaScriptEnabled")
    private void createWindow() {
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setBackgroundColor(Color.WHITE);
        int padding = (int) (12 * this.getResources().getDisplayMetrics().density);
        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setPadding(padding, padding, padding, padding);
        toolbar.setGravity(android.view.Gravity.CENTER_VERTICAL);
        TextView title = new TextView(this);
        title.setText("中国科学技术大学统一认证");
        title.setTextColor(Color.rgb(35, 55, 65));
        title.setTextSize(16);
        toolbar.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        Button cancel = new Button(this);
        cancel.setText("取消");
        cancel.setOnClickListener(view -> closeWindow());
        toolbar.addView(cancel);
        content.addView(toolbar);
        status = new TextView(this);
        status.setPadding(padding, padding / 2, padding, padding);
        status.setTextColor(Color.rgb(35, 55, 65));
        status.setTextSize(15);
        content.addView(status);
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setIndeterminate(true);
        content.addView(progress, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, padding / 3));
        retry = new Button(this);
        retry.setText("重试");
        retry.setVisibility(View.GONE);
        retry.setOnClickListener(view -> retryLogin());
        content.addView(retry);
        schoolView = new WebView(this);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) schoolView.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
        WebSettings settings = schoolView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
        settings.setSaveFormData(false);
        settings.setSupportMultipleWindows(false);
        settings.setJavaScriptCanOpenWindowsAutomatically(true);
        settings.setMediaPlaybackRequiresUserGesture(true);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) settings.setSafeBrowsingEnabled(true);
        // No user-agent override, injected script, JavascriptInterface or form interception.
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(schoolView, false);
        schoolView.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onConsoleMessage(ConsoleMessage message) { return true; }
            @Override public void onPermissionRequest(PermissionRequest request) { request.deny(); }
            @Override public void onGeolocationPermissionsShowPrompt(String origin, android.webkit.GeolocationPermissions.Callback callback) {
                callback.invoke(origin, false, false);
            }
            @Override public boolean onShowFileChooser(WebView view, android.webkit.ValueCallback<android.net.Uri[]> callback, FileChooserParams params) {
                callback.onReceiveValue(null); return true;
            }
        });
        final long windowAttempt = attempt;
        schoolView.setWebViewClient(new WebViewClient() {
            @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) {
                if (!session.current(windowAttempt)) { view.stopLoading(); return; }
                pageFailed = false;
                renderStatus("loading", "请在学校页面手动完成密码与短信验证");
            }
            @Override public void onPageFinished(WebView view, String url) {
                if (!session.current(windowAttempt) || pageFailed) return;
                progress.setVisibility(View.GONE);
                if (SchoolPolicy.isLanding(url) && session.land(windowAttempt)) {
                    view.stopLoading();
                    renderStatus("loading", "正在验证教务登录状态…");
                    Bundle event = new Bundle();
                    // Do not forward query/fragment: they can contain CAS tickets.
                    event.putString("url", "https://jw.ustc.edu.cn/");
                    SchoolSessionService.current.event("landed", event);
                }
            }
            @Override public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handler.cancel(); pageError("学校网站安全连接校验失败，请稍后重试");
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) pageError("学校登录页面加载失败，请检查网络后重试");
            }
            @Override public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
                if (request.isForMainFrame() && response.getStatusCode() >= 400) pageError("学校登录页面暂时不可用，请稍后重试");
            }
            @Override public boolean onRenderProcessGone(WebView view, android.webkit.RenderProcessGoneDetail detail) {
                closeWindow(); return true;
            }
        });
        content.addView(schoolView, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
        setContentView(content);
        Window window = getWindow();
        if (window != null) {
            window.addFlags(WindowManager.LayoutParams.FLAG_SECURE);
            window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        }
        if (window != null) window.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
        renderStatus("loading", "请在学校页面手动完成密码与短信验证");
        try {
            CookieManager.getInstance().flush();
            SchoolProfileFiles.requireInitialized(getDataDir(),getCacheDir(),WebView.getCurrentWebViewPackage().packageName);
        } catch(Exception unsupportedProfile) {
            SchoolSessionService.current.failOpen("PROFILE_PATH_UNSUPPORTED");closeWindow();return;
        }
        schoolView.loadUrl(SchoolPolicy.LOGIN_URL);
    }

    private void pageError(String message) {
        if (!isActive()) return;
        pageFailed = true;
        importFailure = false;
        renderStatus("error", message);
    }
    private void renderStatus(String state, String message) {
        if (status == null) return;
        status.setText(message);
        progress.setVisibility("loading".equals(state) ? View.VISIBLE : View.GONE);
        retry.setVisibility("error".equals(state) ? View.VISIBLE : View.GONE);
    }
    private void retryLogin() {
        if (!isActive() || schoolView == null) return;
        if (importFailure) {
            importFailure = false;
            renderStatus("loading", "正在重新验证登录状态…");
            SchoolSessionService.current.event("retry", new Bundle());
        } else {
            pageFailed = false;
            session.retry();
            schoolView.loadUrl(SchoolPolicy.LOGIN_URL);
        }
    }
    void closeWindow() {
        if(closing) return;
        closing=true;session.end();
        if(SchoolSessionService.current!=null) SchoolSessionService.current.windowClosing(this);
        if(schoolView!=null) {
            schoolView.stopLoading();schoolView.setWebViewClient(new WebViewClient());
            schoolView.setWebChromeClient(null);schoolView.getSettings().setJavaScriptEnabled(false);
            content.removeView(schoolView);schoolView.removeAllViews();schoolView.destroy();schoolView=null;
        }
        finish();
    }
    @Override protected void onDestroy() {
        closeWindow();
        if(SchoolSessionService.current!=null) SchoolSessionService.current.detached(this,true);
        super.onDestroy();
        if(SchoolSessionService.current==null) android.os.Process.killProcess(android.os.Process.myPid());
    }
}
