package cn.xiaowo.app;

import static org.junit.Assert.*;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public class PluginRegistrationInstrumentationTest {
    @Test public void coreCookieImportResolvesToTheRestrictedNativeAdapter() {
        try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
            scenario.onActivity(activity -> {
                assertTrue(activity.getBridge().getPlugin("CapacitorCookies").getInstance() instanceof SchoolCookiesPlugin);
                assertTrue(activity.getBridge().getPlugin("SchoolLogin").getInstance() instanceof SchoolLoginPlugin);
                assertTrue(activity.getBridge().getPlugin("SecureToken").getInstance() instanceof SecureTokenPlugin);
                assertFalse("Plugin calls must never be logged", activity.getBridge().getConfig().isLoggingEnabled());
                assertTrue((activity.getWindow().getAttributes().flags & android.view.WindowManager.LayoutParams.FLAG_SECURE) != 0);
            });
        }
    }
}
