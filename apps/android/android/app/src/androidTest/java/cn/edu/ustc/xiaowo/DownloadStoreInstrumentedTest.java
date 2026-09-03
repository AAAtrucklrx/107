package cn.edu.ustc.xiaowo;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.json.JSONException;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public final class DownloadStoreInstrumentedTest {
    @Test
    public void payloadPreviewNormalizesMetadataWithoutDecodingOnUiThread() throws JSONException {
        DownloadStore.Preview preview = DownloadStore.inspectPayload(
            "{\"name\":\"grades?.csv\",\"mime\":\"TEXT/CSV\",\"data\":\"aGVsbG8=\"}"
        );
        assertEquals("grades_.csv", preview.filename);
        assertEquals("text/csv", preview.mimeType);
        assertEquals(6, preview.estimatedBytes);
    }

    @Test
    public void emptyFilePayloadIsRejected() {
        assertThrows(JSONException.class, () -> DownloadStore.inspectPayload(
            "{\"name\":\"empty.txt\",\"mime\":\"text/plain\",\"data\":\"\"}"
        ));
    }
}
