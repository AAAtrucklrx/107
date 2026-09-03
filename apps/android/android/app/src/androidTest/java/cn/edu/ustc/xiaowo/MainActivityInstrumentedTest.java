package cn.edu.ustc.xiaowo;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import android.view.View;
import android.webkit.WebSettings;
import android.webkit.WebView;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public final class MainActivityInstrumentedTest {
    @Test
    public void activityCreatesHardenedWebViewAndVisibleDemoBoundary() {
        try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
            scenario.onActivity(activity -> {
                WebView webView = activity.getMainWebViewForTesting();
                assertNotNull(webView);
                WebSettings settings = webView.getSettings();
                assertTrue(settings.getJavaScriptEnabled());
                assertTrue(settings.getDomStorageEnabled());
                assertFalse(settings.getAllowFileAccess());
                assertFalse(settings.getAllowContentAccess());
                assertEqualsMixedContentNeverAllow(settings.getMixedContentMode());
                assertTrue(webView.getSettings().getUserAgentString().contains("XiaowoAndroid/0.2.0-demo"));
                assertTrue(activity.findViewById(R.id.demo_banner).getVisibility() == View.VISIBLE);
            });
        }
    }

    private static void assertEqualsMixedContentNeverAllow(int mode) {
        assertTrue(mode == WebSettings.MIXED_CONTENT_NEVER_ALLOW);
    }
}
