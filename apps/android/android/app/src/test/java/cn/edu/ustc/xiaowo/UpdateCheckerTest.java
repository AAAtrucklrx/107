package cn.edu.ustc.xiaowo;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class UpdateCheckerTest {
    private static final long HOUR = 60L * 60L * 1000L;

    @Test
    public void successfulCheckUsesConfiguredInterval() {
        long now = 100L * HOUR;
        assertFalse(UpdateChecker.shouldStartCheck(now, now - HOUR, 0L, 24L * HOUR));
        assertTrue(UpdateChecker.shouldStartCheck(now, now - 25L * HOUR, 0L, 24L * HOUR));
    }

    @Test
    public void failedAttemptRetriesAfterFifteenMinutesInsteadOfOneDay() {
        long now = 100L * HOUR;
        assertFalse(UpdateChecker.shouldStartCheck(now, 0L, now - 10L * 60L * 1000L, 24L * HOUR));
        assertTrue(UpdateChecker.shouldStartCheck(now, 0L, now - 16L * 60L * 1000L, 24L * HOUR));
    }
}
