package cn.xiaowo.app;

import static org.junit.Assert.*;
import java.util.Map;
import org.junit.Test;

public class SchoolPolicyTest {
    @Test public void cookieReadOnlyAcceptsExactHttpsIdentityAndAcademicHosts() {
        assertTrue(SchoolPolicy.allowedCookieUrl("https://id.ustc.edu.cn/cas/login?service=encoded"));
        assertTrue(SchoolPolicy.allowedCookieUrl("https://jw.ustc.edu.cn:443/home"));
        String[] rejected = {"http://id.ustc.edu.cn/", "https://jw.ustc.edu.cn.evil.invalid/",
            "https://jw.ustc.edu.cn@evil.invalid/", "https://user@jw.ustc.edu.cn/",
            "https://jw.ustc.edu.cn:444/", "https://jw.ustc.edu.cn./", "file:///tmp/test",
            "javascript:alert(1)", "intent://jw.ustc.edu.cn/", "https://jw.ustc.edu.cn\\@evil.invalid/", "", null};
        for (String url : rejected) assertFalse(String.valueOf(url), SchoolPolicy.allowedCookieUrl(url));
    }
    @Test public void landingRejectsSsoAndLookalikeHosts() {
        assertTrue(SchoolPolicy.isLanding("https://jw.ustc.edu.cn/home"));
        assertFalse(SchoolPolicy.isLanding("https://id.ustc.edu.cn/cas/login?next=jw.ustc.edu.cn"));
        assertFalse(SchoolPolicy.isLanding("https://jw.ustc.edu.cn.evil.invalid/home"));
        assertFalse(SchoolPolicy.isLanding("http://jw.ustc.edu.cn/home"));
        assertFalse(SchoolPolicy.isLanding("https://jw.ustc.edu.cn/ucas-sso/login?ticket=synthetic"));
        assertFalse(SchoolPolicy.isLanding("https://jw.ustc.edu.cn/UCAS-SSO/login/redirect"));
        assertFalse(SchoolPolicy.isLanding("https://jw.ustc.edu.cn/auth/ucas-sso/login"));
        assertFalse(SchoolPolicy.isLanding("https://jw.ustc.edu.cn/login"));
    }
    @Test public void arbitraryNavigationUrlsDoNotGrantCookieAccessOrCompleteLogin() {
        String[] navigationUrls = {
            "https://ustc.edu.cn/",
            "https://MFA.USTC.EDU.CN:443/second-factor?return=%2Flogin",
            "https://verify.auth.ustc.edu.cn/auth/%E8%AE%A4%E8%AF%81",
            "https://analytics.ustc.edu.cn/",
            "https://analytics.ustc.edu.cn/.isg/other",
            "https://analytics.ustc.edu.cn/.isg/gateway/../../other",
            "https://analytics.ustc.edu.cn/.isg/gateway/%2e%2e/other",
            "https://analytics.ustc.edu.cn/.isg/gateway/%2fother",
            "https://third-party.invalid/second-factor?next=https://jw.ustc.edu.cn/",
            "https://jw.ustc.edu.cn.third-party.invalid/",
            "https://third-party.invalid:8443/verification"
        };
        for (String url : navigationUrls) {
            assertFalse(url, SchoolPolicy.allowedCookieUrl(url));
            assertFalse(url, SchoolPolicy.isLanding(url));
            assertThrows(IllegalArgumentException.class, () -> SchoolPolicy.cookies(url, "SOURCEID_TGC=fake", true));
        }
    }
    @Test public void cookieReadRequiresAnActiveSchoolWindow() {
        assertThrows(IllegalStateException.class, () -> SchoolPolicy.cookies("https://id.ustc.edu.cn/", "SOURCEID_TGC=fake", false));
        assertThrows(IllegalArgumentException.class, () -> SchoolPolicy.cookies("https://evil.invalid/", "SOURCEID_TGC=fake", true));
    }
    @Test public void onlyNecessaryCookiesAreReturnedWithoutDecodingTheirValues() {
        Map<String,String> values = SchoolPolicy.cookies("https://id.ustc.edu.cn/cas/login",
            "SOURCEID_TGC=a+b%2Fc==; tracking=private; SESSION=s; SVRNAME=node; rememberToken=r; deviceID=d; device=x; JSESSIONID=unused", true);
        assertEquals(6, values.size());
        assertEquals("a+b%2Fc==", values.get("SOURCEID_TGC"));
        assertFalse(values.containsKey("tracking"));
        assertFalse(values.containsKey("JSESSIONID"));
    }
    @Test public void ambiguousOrOversizedCredentialHeadersFailClosed() {
        assertThrows(IllegalArgumentException.class, () -> SchoolPolicy.cookies("https://jw.ustc.edu.cn/", "SESSION=one; SESSION=two", true));
        assertThrows(IllegalArgumentException.class, () -> SchoolPolicy.cookies("https://jw.ustc.edu.cn/", "SESSION=bad\r\nvalue", true));
        assertThrows(IllegalArgumentException.class, () -> SchoolPolicy.cookies("https://jw.ustc.edu.cn/", "SESSION=" + "x".repeat(5000), true));
        assertTrue(SchoolPolicy.cookies("https://jw.ustc.edu.cn/", null, true).isEmpty());
    }
}
