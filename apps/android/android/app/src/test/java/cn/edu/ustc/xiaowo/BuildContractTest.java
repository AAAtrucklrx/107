package cn.edu.ustc.xiaowo;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class BuildContractTest {
    @Test
    public void demoBuildUsesFixedServerContract() {
        assertEquals("http://114.214.241.119:8850", BuildConfig.XIAOWO_SERVER_ORIGIN);
        assertEquals("demo", BuildConfig.XIAOWO_CHANNEL);
        assertEquals("/mobile/android/update.json", BuildConfig.XIAOWO_UPDATE_PATH);
        assertEquals("0.2.0-demo", BuildConfig.VERSION_NAME);
        assertEquals(2, BuildConfig.VERSION_CODE);
        assertTrue(BuildConfig.XIAOWO_DEMO_BUILD);
        assertTrue(!BuildConfig.XIAOWO_REQUIRE_HTTPS);
    }
}
