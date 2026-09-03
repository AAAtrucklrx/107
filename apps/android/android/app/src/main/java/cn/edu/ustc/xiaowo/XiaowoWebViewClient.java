package cn.edu.ustc.xiaowo;

import android.graphics.Bitmap;
import android.net.Uri;
import android.net.http.SslError;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.SslErrorHandler;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import androidx.webkit.SafeBrowsingResponseCompat;
import androidx.webkit.WebResourceErrorCompat;
import androidx.webkit.WebViewClientCompat;
import androidx.webkit.WebViewFeature;

final class XiaowoWebViewClient extends WebViewClientCompat {
    private final MainActivity activity;

    XiaowoWebViewClient(MainActivity activity) {
        this.activity = activity;
    }

    @Override
    public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
        if (!request.isForMainFrame()) return false;
        return activity.handleMainFrameNavigation(request.getUrl());
    }

    @Override
    public void onPageStarted(WebView view, String url, Bitmap favicon) {
        activity.onMainPageStarted(Uri.parse(url == null ? "" : url));
    }

    @Override
    public void onPageFinished(WebView view, String url) {
        Uri uri = Uri.parse(url == null ? "" : url);
        activity.onMainPageFinished(uri);
        if (NavigationPolicy.isMainOrigin(uri)) WebCompatibility.inject(view);
    }

    @Override
    public void onReceivedError(WebView view, WebResourceRequest request, WebResourceErrorCompat error) {
        if (request.isForMainFrame()) activity.onMainPageError(request.getUrl());
    }

    @Override
    public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse errorResponse) {
        if (request.isForMainFrame() && errorResponse.getStatusCode() >= 500) {
            activity.onMainPageError(request.getUrl());
        }
    }

    @Override
    public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
        handler.cancel();
        activity.onMainPageError(error == null ? null : Uri.parse(error.getUrl()));
    }

    @Override
    public void onSafeBrowsingHit(WebView view, WebResourceRequest request, int threatType, SafeBrowsingResponseCompat callback) {
        if (WebViewFeature.isFeatureSupported(WebViewFeature.SAFE_BROWSING_RESPONSE_BACK_TO_SAFETY)) {
            callback.backToSafety(true);
        } else {
            view.stopLoading();
        }
        activity.onUnsafePageBlocked(request.getUrl());
    }

    @Override
    public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
        activity.onRendererGone(detail != null && detail.didCrash());
        return true;
    }
}
