package cn.xiaowo.app;

import static org.junit.Assert.*;
import android.content.Context;
import android.content.pm.ApplicationInfo;
import android.security.NetworkSecurityPolicy;
import androidx.test.core.app.ApplicationProvider;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.filters.SdkSuppress;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public class ManifestSecurityInstrumentationTest {
    @Test @SdkSuppress(minSdkVersion = 24) public void debugHttpIsLimitedToLoopbackAndBackupIsDisabled() {
        NetworkSecurityPolicy policy = NetworkSecurityPolicy.getInstance();
        assertTrue(policy.isCleartextTrafficPermitted("127.0.0.1"));
        assertFalse(policy.isCleartextTrafficPermitted("id.ustc.edu.cn"));
        assertFalse(policy.isCleartextTrafficPermitted("example.invalid"));
        Context context = ApplicationProvider.getApplicationContext();
        assertEquals(0, context.getApplicationInfo().flags & ApplicationInfo.FLAG_ALLOW_BACKUP);
    }
}
