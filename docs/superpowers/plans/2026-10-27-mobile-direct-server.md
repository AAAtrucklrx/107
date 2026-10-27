# Mobile Direct Server Implementation Plan

> Execute in the existing checkout; preserve all prior changes. No Git commits. User explicitly selected a phone connecting directly over Wi-Fi/4G.

**Goal:** Deliver an installable 小蛙助手 APK connecting directly to the existing server 150.158.32.37 with HTTPS and mobile token authentication.

**Architecture:** Use a publicly trusted Let's Encrypt IP certificate on nginx 443. Keep the existing single FastAPI service and business logic; enable its existing mobile authentication, move its public origin to HTTPS, and redirect normal HTTP traffic to HTTPS while retaining HTTP-01 challenge access. Preserve existing data encryption keys, databases and service behavior. Build a debug-signed HTTPS test APK compatible with the already installed debug app; no app data deletion. Keep HTTP redirect temporary (307), preserve methods, and bind backend 8000 only to loopback.

**Spec:** User requested immediate server use and explicitly chose standalone Wi-Fi/4G rather than USB/SSH tunneling on 2026-10-27.

## Constraints
- Never send school cookies or tokens over public HTTP; retain normal TLS/system CA checks.
- No rewriting QA/tools, no old-server changes, no committed credentials.
- Back up changed server configuration privately before applying; validate nginx and application configuration before restart.
- Certificate renewal must be automated because IP certificates last 160 hours.
- Real school password and SMS remain manual; never claim this test occurred automatically.

## Tasks
- [x] Confirm root causes: APK loopback address, mobile auth disabled, missing vault key, no HTTPS listener.
- [x] Prepare ACME challenge location and trusted IP certificate; configure automatic renewal and nginx reload.
- [x] Configure mobile auth with a distinct generated vault key, independent mobile DB, HTTPS origin, debug loopback disabled; preserve preexisting secrets.
- [x] Validate existing service, enable HTTPS and redirect HTTP; verify exact Android CORS and rejection of untrusted origins.
- [x] Check public 443 reachability; if cloud firewall blocks it, prepare all server/package work and ask user to enable TCP 443.
- [x] Verify bootstrap → Bearer session → SSE → logout/revocation, with tokens only in memory.
- [x] Build HTTPS APK, inspect packaged API metadata/signature/size; install only when device is connected, preserving app data.
- [x] Independent review, deployment/rollback notes and honest delivery status.

Completed deployment and packaged APK verification. ADB had no device; real school login/course verification remains a user acceptance step, not an automated pass. See [deployment record](../../mobile-server-direct-2026-10-27.md).
