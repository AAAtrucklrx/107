package cn.edu.ustc.xiaowo;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import android.net.Uri;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public final class NavigationPolicyInstrumentedTest {
    @Test
    public void mainWebViewRequiresExactOriginAndPort() {
        assertTrue(NavigationPolicy.isMainOrigin(Uri.parse("http://114.214.241.119:8850/academic?tab=schedule")));
        assertTrue(NavigationPolicy.isMainOrigin(Uri.parse("http://114.214.241.119:8850/admin")));

        assertFalse(NavigationPolicy.isMainOrigin(Uri.parse("http://114.214.241.119/")));
        assertFalse(NavigationPolicy.isMainOrigin(Uri.parse("http://114.214.241.119:8851/")));
        assertFalse(NavigationPolicy.isMainOrigin(Uri.parse("https://114.214.241.119:8850/")));
        assertFalse(NavigationPolicy.isMainOrigin(Uri.parse("http://114.214.241.119.evil.example:8850/")));
    }

    @Test
    public void originValidationRejectsCredentialsPathsAndInsecureProduction() {
        assertTrue(AppConfig.isValidOrigin(Uri.parse("http://114.214.241.119:8850"), false));
        assertTrue(AppConfig.isValidOrigin(Uri.parse("https://xiaowo.example"), true));
        assertFalse(AppConfig.isValidOrigin(Uri.parse("http://xiaowo.example"), true));
        assertFalse(AppConfig.isValidOrigin(Uri.parse("https://user@xiaowo.example"), true));
        assertFalse(AppConfig.isValidOrigin(Uri.parse("https://xiaowo.example/path"), true));
        assertFalse(AppConfig.isValidOrigin(Uri.parse("https://xiaowo.example?debug=1"), true));
    }

    @Test
    public void topLevelRoutingSeparatesMainExternalAndBlockedLinks() {
        assertEquals(
            NavigationPolicy.Route.ALLOW_IN_MAIN,
            NavigationPolicy.routeMainFrame(Uri.parse("http://114.214.241.119:8850/campus"))
        );
        assertEquals(
            NavigationPolicy.Route.OPEN_CUSTOM_TAB,
            NavigationPolicy.routeMainFrame(Uri.parse("https://www.ustc.edu.cn/"))
        );
        assertEquals(
            NavigationPolicy.Route.OPEN_EXTERNAL_APP,
            NavigationPolicy.routeMainFrame(Uri.parse("mailto:help@example.org"))
        );
        assertEquals(NavigationPolicy.Route.BLOCK, NavigationPolicy.routeMainFrame(Uri.parse("http://example.com/")));
        assertEquals(NavigationPolicy.Route.BLOCK, NavigationPolicy.routeMainFrame(Uri.parse("javascript:alert(1)")));
        assertEquals(NavigationPolicy.Route.BLOCK, NavigationPolicy.routeMainFrame(Uri.parse("file:///sdcard/secret")));
    }

    @Test
    public void homeAndDisplayedAuthorityAreNarrowlyRecognized() {
        assertTrue(NavigationPolicy.isMainHome(Uri.parse("http://114.214.241.119:8850/")));
        assertFalse(NavigationPolicy.isMainHome(Uri.parse("http://114.214.241.119:8850/admin")));
        assertEquals("https://example.com", NavigationPolicy.displayAuthority(Uri.parse("https://EXAMPLE.com/path")));
        assertEquals("https://example.com:8443", NavigationPolicy.displayAuthority(Uri.parse("https://example.com:8443/path")));
        assertEquals(
            "不安全 · http://114.214.241.119:8850",
            NavigationPolicy.displayAuthority(Uri.parse("http://114.214.241.119:8850/"))
        );
    }
}
