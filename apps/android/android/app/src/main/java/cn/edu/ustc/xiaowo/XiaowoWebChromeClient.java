package cn.edu.ustc.xiaowo;

import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Message;
import android.webkit.GeolocationPermissions;
import android.webkit.JsPromptResult;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import java.util.concurrent.atomic.AtomicBoolean;

final class XiaowoWebChromeClient extends WebChromeClient {
    private final MainActivity activity;

    XiaowoWebChromeClient(MainActivity activity) {
        this.activity = activity;
    }

    @Override
    public void onProgressChanged(WebView view, int progress) {
        activity.onPageProgress(progress);
    }

    @Override
    public void onPermissionRequest(PermissionRequest request) {
        activity.runOnUiThread(request::deny);
    }

    @Override
    public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) {
        callback.invoke(origin, false, false);
    }

    @Override
    public boolean onCreateWindow(WebView view, boolean isDialog, boolean isUserGesture, Message resultMsg) {
        if (!isUserGesture || !(resultMsg.obj instanceof WebView.WebViewTransport transport)) return false;

        WebView popup = new WebView(activity);
        popup.getSettings().setJavaScriptEnabled(false);
        popup.getSettings().setDomStorageEnabled(false);
        popup.getSettings().setAllowFileAccess(false);
        popup.getSettings().setAllowContentAccess(false);
        AtomicBoolean handled = new AtomicBoolean(false);
        popup.setWebViewClient(new WebViewClient() {
            private void route(Uri uri) {
                if (uri == null || NavigationPolicy.isBlank(uri)) return;
                if (handled.compareAndSet(false, true)) {
                    activity.openPopupUri(uri);
                    popup.post(() -> {
                        popup.stopLoading();
                        popup.destroy();
                    });
                }
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView popupView, WebResourceRequest request) {
                if (request.isForMainFrame()) route(request.getUrl());
                return true;
            }

            @Override
            public void onPageStarted(WebView popupView, String url, Bitmap favicon) {
                route(Uri.parse(url == null ? "" : url));
            }
        });
        transport.setWebView(popup);
        resultMsg.sendToTarget();
        return true;
    }

    @Override
    public boolean onJsPrompt(WebView view, String url, String message, String defaultValue, JsPromptResult result) {
        Uri source = Uri.parse(url == null ? "" : url);
        WebCompatibility.Request request = NavigationPolicy.isMainOrigin(source)
            ? WebCompatibility.parsePrompt(message, defaultValue)
            : null;
        if (request == null) return super.onJsPrompt(view, url, message, defaultValue, result);
        activity.confirmCompatibilityRequest(request, result);
        return true;
    }
}
