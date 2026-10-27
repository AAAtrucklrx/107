package cn.xiaowo.app;

import android.os.Handler;
import android.os.Looper;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import androidx.test.core.app.ApplicationProvider;
import androidx.test.platform.app.InstrumentationRegistry;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Offline-only instrumentation helper. Never packaged in the app. No school requests or JS bridge. */
final class OfflineStorageProbe implements AutoCloseable {
    private final AtomicReference<WebView> view = new AtomicReference<>();
    OfflineStorageProbe(String origin) throws Exception {
        CountDownLatch loaded = new CountDownLatch(1);
        InstrumentationRegistry.getInstrumentation().runOnMainSync(() -> {
            WebView web = new WebView(ApplicationProvider.getApplicationContext());
            view.set(web);
            web.getSettings().setJavaScriptEnabled(true);
            web.getSettings().setDomStorageEnabled(true);
            web.getSettings().setBlockNetworkLoads(true);
            web.setWebViewClient(new WebViewClient() {
                @Override public void onPageFinished(WebView ignored, String url) { loaded.countDown(); }
            });
            web.loadDataWithBaseURL(origin, "<html><body>Offline storage probe</body></html>", "text/html", "UTF-8", null);
        });
        if (!loaded.await(10, TimeUnit.SECONDS)) throw new AssertionError("Offline storage page did not load");
    }
    void seed() throws Exception {
        String result = run("localStorage.setItem('xiaowo_cleanup_probe','synthetic');"
            + "const r=indexedDB.open('xiaowo_cleanup_probe',1);"
            + "r.onupgradeneeded=()=>r.result.createObjectStore('history');"
            + "r.onerror=()=>window.__probe='error';"
            + "r.onsuccess=()=>{const db=r.result;const tx=db.transaction('history','readwrite');"
            + "tx.objectStore('history').put('synthetic','item');tx.onerror=()=>window.__probe='error';"
            + "tx.oncomplete=()=>{db.close();window.__probe='seeded';};};");
        if (!"\"seeded\"".equals(result)) throw new AssertionError("Offline localStorage/IndexedDB seed failed");
    }
    boolean bothPresent() throws Exception { return "\"present:present\"".equals(inspect()); }
    boolean bothAbsent() throws Exception { return "\"absent:absent\"".equals(inspect()); }
    private String inspect() throws Exception {
        return run("const local=localStorage.getItem('xiaowo_cleanup_probe')==='synthetic'?'present':'absent';"
            + "const r=indexedDB.open('xiaowo_cleanup_probe',1);let missing=false;"
            + "r.onupgradeneeded=()=>{missing=true;r.transaction.abort();};"
            + "r.onerror=()=>window.__probe=missing?local+':absent':'error';"
            + "r.onsuccess=()=>{const db=r.result;if(!db.objectStoreNames.contains('history')){db.close();window.__probe=local+':absent';return;}"
            + "const tx=db.transaction('history');const value=tx.objectStore('history').get('item');"
            + "value.onerror=()=>window.__probe='error';value.onsuccess=()=>{window.__probe=local+(value.result==='synthetic'?':present':':absent');};"
            + "tx.oncomplete=()=>db.close();};");
    }
    private String run(String script) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>();
        InstrumentationRegistry.getInstrumentation().runOnMainSync(() -> {
            view.get().evaluateJavascript("(()=>{window.__probe='pending';try{" + script + "}catch(e){window.__probe='error';}})()", null);
            Handler main = new Handler(Looper.getMainLooper());
            Runnable poll = new Runnable() {
                int attempts;
                @Override public void run() {
                    view.get().evaluateJavascript("window.__probe", value -> {
                        if (!"\"pending\"".equals(value) || ++attempts >= 100) {
                            result.set(value); done.countDown();
                        } else main.postDelayed(this, 50);
                    });
                }
            };
            main.post(poll);
        });
        if (!done.await(10, TimeUnit.SECONDS)) throw new AssertionError("Offline storage probe timed out");
        return result.get();
    }
    @Override public void close() {
        InstrumentationRegistry.getInstrumentation().runOnMainSync(() -> view.get().destroy());
    }
}
