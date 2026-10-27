package cn.xiaowo.app;

import static org.junit.Assert.*;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Message;
import android.os.Messenger;
import androidx.test.core.app.ActivityScenario;
import androidx.test.core.app.ApplicationProvider;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import java.io.File;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public class SchoolCleanupInstrumentationTest {
    @Test public void killsDedicatedProcessAndRemovesWholeProfileWithoutTouchingAppStorage() throws Exception {
        Context context=ApplicationProvider.getApplicationContext();
        AtomicReference<SchoolProcessClient> client=new AtomicReference<>();
        try(ActivityScenario<NativeProbeActivity> scenario=ActivityScenario.launch(NativeProbeActivity.class)) {
            scenario.onActivity(activity->client.set(SchoolProcessClient.attach(activity,(name,data)->{})));
            clean(client.get());
            try(OfflineStorageProbe app=new OfflineStorageProbe("https://localhost/")){app.seed();}
            TokenVault vault=new TokenVault(context);vault.set("BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB");
            try {
                CountDownLatch seeded=new CountDownLatch(1);
                AtomicReference<Bundle> result=new AtomicReference<>();
                Messenger receiver=new Messenger(new Handler(Looper.getMainLooper(),message->{result.set(message.getData());seeded.countDown();return true;}));
                ServiceConnection connection=new ServiceConnection(){
                    @Override public void onServiceConnected(ComponentName name,IBinder binder){
                        Message message=Message.obtain();message.replyTo=receiver;
                        try{new Messenger(binder).send(message);}catch(Exception ignored){seeded.countDown();}
                    }
                    @Override public void onServiceDisconnected(ComponentName name){}
                };
                assertTrue(context.bindService(new Intent(context,NativeProbeService.class),connection,Context.BIND_AUTO_CREATE));
                try {
                    assertTrue("Offline probe must complete",seeded.await(20,TimeUnit.SECONDS));
                    assertNotNull(result.get());
                    assertTrue("Vendor must honor the dedicated profile path",result.get().getBoolean("profile"));
                    assertTrue("Native IPC process must be able to read HttpOnly",result.get().getBoolean("httpOnly"));
                    assertTrue("Main profile must not receive school cookies",android.webkit.CookieManager.getInstance().getCookie("https://id.ustc.edu.cn/")==null);
                    assertTrue("Cookie database locations: "+result.get().getString("locations"),result.get().getBoolean("cookieFile"));
                    assertTrue("Offline localStorage, IndexedDB, CacheStorage and service worker must be seeded",result.get().getBoolean("storageAndWorker"));
                    assertTrue(new File(context.getDataDir(),"app_webview_school_login").isDirectory());
                    assertTrue("Raw synthetic marker must exist before cleanup",containsMarker(context.getDataDir()));
                } finally {context.unbindService(connection);}
                clean(client.get());
                assertTrue("School process must be absent",SchoolProcessClient.schoolPids(context).isEmpty());
                assertFalse("All cookie/SQLite/WAL/site data must be removed with the profile",new File(context.getDataDir(),"app_webview_school_login").exists());
                assertFalse("Huawei profile must also be removed",new File(context.getDataDir(),"app_hws_webview_school_login").exists());
                assertFalse("Dedicated HTTP cache must be removed",new File(context.getCacheDir(),"webview_school_login").exists());
                assertFalse("No raw synthetic marker may remain anywhere in app data",containsMarker(context.getDataDir()));
                try(OfflineStorageProbe app=new OfflineStorageProbe("https://localhost/")){assertTrue("Main app history must survive",app.bothPresent());}
                assertTrue("Keystore bearer must survive school cleanup","BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB".equals(vault.get()));
                clean(client.get()); // Repeated cleanup must not leave a stale bound connection.
            } finally {vault.remove();clean(client.get());}
        }
    }
    private void clean(SchoolProcessClient client) throws Exception {
        CountDownLatch done=new CountDownLatch(1);AtomicReference<String> error=new AtomicReference<>();
        client.cleanup((value,failure)->{error.set(failure);done.countDown();});
        assertTrue("Cleanup must terminate",done.await(20,TimeUnit.SECONDS));
        assertNull(error.get());
    }
    private boolean containsMarker(File file) throws Exception {
        if(java.nio.file.Files.isSymbolicLink(file.toPath())) return false;
        if(file.isDirectory()) {
            File[] children=file.listFiles();if(children==null)throw new java.io.IOException("Test data cannot be inspected");
            for(File child:children)if(containsMarker(child))return true;
            return false;
        }
        byte[] marker=NativeProbeService.SENTINEL.getBytes(java.nio.charset.StandardCharsets.UTF_8);
        try(java.io.InputStream stream=new java.io.BufferedInputStream(new java.io.FileInputStream(file))) {
            int matched=0,value;
            while((value=stream.read())!=-1) {
                if(value==(marker[matched]&255)) {if(++matched==marker.length)return true;}
                else matched=value==(marker[0]&255)?1:0;
            }
        }
        return false;
    }
}
