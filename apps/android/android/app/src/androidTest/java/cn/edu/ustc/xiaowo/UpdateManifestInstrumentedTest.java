package cn.edu.ustc.xiaowo;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.json.JSONException;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public final class UpdateManifestInstrumentedTest {
    private static final String VALID_HASH = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    @Test
    public void validManifestIsNormalized() throws JSONException {
        UpdateManifest manifest = UpdateManifest.parse("{"
            + "\"versionCode\":2,"
            + "\"versionName\":\" 0.2.0 \","
            + "\"releaseNotes\":\" fixes \","
            + "\"downloadUrl\":\"https://downloads.example.org/xiaowo.apk\","
            + "\"sha256\":\"" + VALID_HASH.toUpperCase() + "\""
            + "}");

        assertEquals(2, manifest.versionCode);
        assertEquals("0.2.0", manifest.versionName);
        assertEquals("fixes", manifest.releaseNotes);
        assertEquals("https://downloads.example.org/xiaowo.apk", manifest.downloadUri.toString());
        assertEquals(VALID_HASH, manifest.sha256);
    }

    @Test
    public void insecureDownloadIsRejected() {
        String json = "{\"versionCode\":2,\"versionName\":\"0.2.0\","
            + "\"downloadUrl\":\"http://downloads.example.org/xiaowo.apk\","
            + "\"sha256\":\"" + VALID_HASH + "\"}";
        assertThrows(JSONException.class, () -> UpdateManifest.parse(json));
    }

    @Test
    public void malformedVersionAndHashAreRejected() {
        String badVersion = "{\"versionCode\":0,\"versionName\":\"\","
            + "\"downloadUrl\":\"https://downloads.example.org/xiaowo.apk\","
            + "\"sha256\":\"" + VALID_HASH + "\"}";
        String badHash = "{\"versionCode\":2,\"versionName\":\"0.2.0\","
            + "\"downloadUrl\":\"https://downloads.example.org/xiaowo.apk\","
            + "\"sha256\":\"not-a-hash\"}";

        assertThrows(JSONException.class, () -> UpdateManifest.parse(badVersion));
        assertThrows(JSONException.class, () -> UpdateManifest.parse(badHash));
    }
}
