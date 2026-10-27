package cn.xiaowo.app;

/** Guards callbacks from an earlier window after cancellation or a new attempt. */
final class LoginSession {
    private long generation;
    private boolean active;
    private boolean landed;
    synchronized long begin() { active = false; landed = false; return ++generation; }
    synchronized boolean activate(long attempt) {
        if (attempt != generation) return false;
        active = true;
        return true;
    }
    synchronized boolean isActive() { return active; }
    synchronized boolean current(long attempt) { return active && attempt == generation; }
    synchronized boolean land(long attempt) {
        if (!current(attempt) || landed) return false;
        landed = true;
        return true;
    }
    synchronized void retry() { if (active) landed = false; }
    synchronized void end() { ++generation; active = false; landed = false; }
}
