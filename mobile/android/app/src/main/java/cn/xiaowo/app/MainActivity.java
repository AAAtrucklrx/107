package cn.xiaowo.app;

import android.os.Bundle;
import android.view.WindowManager;
import android.webkit.WebSettings;
import android.webkit.WebView;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override public void onCreate(Bundle savedInstanceState) {
        registerPlugin(SchoolLoginPlugin.class);
        registerPlugin(SchoolCookiesPlugin.class);
        registerPlugin(SecureTokenPlugin.class);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        super.onCreate(savedInstanceState);
        WebView.setWebContentsDebuggingEnabled(false);
        if (getBridge() != null) {
            WebView appView = getBridge().getWebView();
            // The core plugin is loaded before the restricted replacement; remove its setter interface.
            appView.removeJavascriptInterface("CapacitorCookiesAndroidInterface");
            appView.getSettings().setMixedContentMode(BuildConfig.DEBUG
                ? WebSettings.MIXED_CONTENT_ALWAYS_ALLOW : WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        }
    }
}
