package cn.xiaowo.app;
import android.app.Application;
import android.webkit.WebView;
public final class XiaowoApplication extends Application {
    @Override public void onCreate() {
        super.onCreate();
        if ((getPackageName() + ":school").equals(Application.getProcessName())) {
            // Must precede CookieManager, WebView, or any other WebKit call in this process.
            WebView.setDataDirectorySuffix(SchoolProfileFiles.SUFFIX);
            WebView.setWebContentsDebuggingEnabled(false);
        }
    }
}
