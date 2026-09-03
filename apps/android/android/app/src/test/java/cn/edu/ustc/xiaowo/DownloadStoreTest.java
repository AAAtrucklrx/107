package cn.edu.ustc.xiaowo;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class DownloadStoreTest {
    @Test
    public void filenameSanitizationRemovesPathAndWindowsCharacters() {
        assertEquals("lecture_notes_week_.pdf", DownloadStore.sanitizeFilename(" lecture/notes:week?.pdf "));
        assertEquals("bad_name.txt", DownloadStore.sanitizeFilename("bad\u0001name.txt"));
    }

    @Test
    public void filenameSanitizationUsesSafeFallbacks() {
        assertEquals("download.bin", DownloadStore.sanitizeFilename(null));
        assertEquals("download.bin", DownloadStore.sanitizeFilename("   "));
        assertEquals("download.bin", DownloadStore.sanitizeFilename("."));
        assertEquals("download.bin", DownloadStore.sanitizeFilename(".."));
    }

    @Test
    public void filenameSanitizationCapsLength() {
        assertEquals(120, DownloadStore.sanitizeFilename("a".repeat(160)).length());
    }

    @Test
    public void mimeTypeFallsBackWhenPageSuppliesInvalidMetadata() {
        assertEquals("text/csv", DownloadStore.sanitizeMimeType(" Text/CSV "));
        assertEquals("application/octet-stream", DownloadStore.sanitizeMimeType("text/csv; charset=utf-8"));
        assertEquals("application/octet-stream", DownloadStore.sanitizeMimeType(null));
    }

    @Test
    public void nativePayloadHasAnExplicitUpperBound() {
        assertTrue(DownloadStore.isPayloadSizeAllowed(null));
        assertTrue(DownloadStore.isPayloadSizeAllowed("x".repeat(12 * 1024 * 1024)));
        assertFalse(DownloadStore.isPayloadSizeAllowed("x".repeat(12 * 1024 * 1024 + 1)));
    }

}
