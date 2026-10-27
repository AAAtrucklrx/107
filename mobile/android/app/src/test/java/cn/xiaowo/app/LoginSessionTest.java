package cn.xiaowo.app;

import static org.junit.Assert.*;
import org.junit.Test;

public class LoginSessionTest {
    @Test public void aCancelledOpenCannotBecomeActiveWhenCleanupReturnsLate() {
        LoginSession session = new LoginSession();
        long attempt = session.begin();
        session.end();
        assertFalse(session.activate(attempt));
        assertFalse(session.isActive());
    }
    @Test public void landingIsEmittedOnceAndRetryRearmsIt() {
        LoginSession session = new LoginSession();
        long attempt = session.begin();
        assertTrue(session.activate(attempt));
        assertTrue(session.land(attempt));
        assertFalse(session.land(attempt));
        session.retry();
        assertTrue(session.land(attempt));
        session.end();
        assertFalse(session.land(attempt));
    }
}
