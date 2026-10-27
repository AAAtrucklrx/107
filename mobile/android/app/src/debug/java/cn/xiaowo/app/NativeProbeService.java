package cn.xiaowo.app;

import android.app.Service;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Message;
import android.os.Messenger;
import android.os.Process;
import android.webkit.CookieManager;
import android.webkit.ServiceWorkerClient;
import android.webkit.ServiceWorkerController;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;

/** Synthetic, same-UID instrumentation fixture. Removed by the release source set. */
public final class NativeProbeService extends Service {
    static final String SENTINEL="XIAOWO_SYNTHETIC_PROFILE_PROBE_91d3";
    private final Handler main=new Handler(Looper.getMainLooper());
    private WebView web;
    private Messenger reply;
    private final Messenger endpoint=new Messenger(new Handler(Looper.getMainLooper(),message->{
        if(message.sendingUid!=Process.myUid() || message.replyTo==null) return true;
        reply=message.replyTo;seed();return true;
    }));
    @Override public IBinder onBind(Intent intent) {return endpoint.getBinder();}
    private WebResourceResponse response(String path) {
        String source=path.endsWith("xiaowo-probe-sw.js")
            ? "self.addEventListener('install',e=>e.waitUntil(self.skipWaiting()));self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));"
            : "<!doctype html><html><body>Offline synthetic profile probe</body></html>";
        return new WebResourceResponse(path.endsWith(".js")?"application/javascript":"text/html","UTF-8",new ByteArrayInputStream(source.getBytes(StandardCharsets.UTF_8)));
    }
    private void seed() {
        try {
            web=new WebView(this);
            web.getSettings().setJavaScriptEnabled(true);web.getSettings().setDomStorageEnabled(true);
            web.getSettings().setBlockNetworkLoads(true);
            ServiceWorkerController workers=ServiceWorkerController.getInstance();
            workers.getServiceWorkerWebSettings().setBlockNetworkLoads(true);
            workers.setServiceWorkerClient(new ServiceWorkerClient(){
                @Override public WebResourceResponse shouldInterceptRequest(WebResourceRequest request){return response(request.getUrl().getPath());}
            });
            web.setWebViewClient(new WebViewClient(){
                @Override public WebResourceResponse shouldInterceptRequest(WebView view,WebResourceRequest request){return response(request.getUrl().getPath());}
                @Override public void onPageFinished(WebView view,String url) {
                    String script="(async()=>{try{window.__probe='pending';localStorage.setItem('probe','"+SENTINEL+"');"
                        +"await new Promise((ok,no)=>{const r=indexedDB.open('xiaowo-probe',1);r.onupgradeneeded=()=>r.result.createObjectStore('data');r.onerror=no;r.onsuccess=()=>{const d=r.result;const t=d.transaction('data','readwrite');t.objectStore('data').put('"+SENTINEL+"','probe');t.oncomplete=()=>{d.close();ok();};t.onerror=no;};});"
                        +"const c=await caches.open('xiaowo-probe');await c.put('/probe',new Response('"+SENTINEL+"'));"
                        +"await navigator.serviceWorker.register('/xiaowo-probe-sw.js');await navigator.serviceWorker.ready;window.__probe='ready';}catch(e){window.__probe='error';}})()";
                    view.evaluateJavascript(script,null);poll(0);
                }
            });
            CookieManager.getInstance().setCookie("https://id.ustc.edu.cn/","SOURCEID_TGC="+SENTINEL+"; Secure; HttpOnly; Max-Age=3600; Path=/",accepted->{
                CookieManager.getInstance().flush();web.loadUrl("https://id.ustc.edu.cn/xiaowo-probe/");
            });
        } catch(Exception ignored){finish(false);}
    }
    private void poll(int count) {
        web.evaluateJavascript("window.__probe",value->{
            if("\"ready\"".equals(value)){finish(true);return;}
            if("\"error\"".equals(value)||count>200){finish(false);return;}
            main.postDelayed(()->poll(count+1),50);
        });
    }
    private void finish(boolean stored) {
        Bundle data=new Bundle();
        try {
            SchoolProfileFiles.requireInitialized(getDataDir(),getCacheDir(),WebView.getCurrentWebViewPackage().packageName);
            data.putBoolean("profile",true);
            CookieManager.getInstance().flush();
            data.putBoolean("cookieFile",(cookieFileExists(new java.io.File(getDataDir(),"app_webview_school_login")) || cookieFileExists(new java.io.File(getDataDir(),"app_hws_webview_school_login"))));
            StringBuilder locations=new StringBuilder();
            listLocations(getDataDir(),getDataDir(),locations,0);
            data.putString("locations",locations.toString());
            data.putBoolean("httpOnly",SENTINEL.equals(SchoolPolicy.cookies("https://id.ustc.edu.cn/",CookieManager.getInstance().getCookie("https://id.ustc.edu.cn/"),true).get("SOURCEID_TGC")));
        }catch(Exception ignored){data.putBoolean("profile",false);}
        data.putBoolean("storageAndWorker",stored);data.putInt("pid",Process.myPid());
        Message result=Message.obtain();result.setData(data);
        try{reply.send(result);}catch(Exception ignored){}
    }
    private boolean cookieFileExists(java.io.File file) {
        if(java.nio.file.Files.isSymbolicLink(file.toPath())) return false;
        if(file.isFile()) return file.getName().equals("Cookies") && file.length()>0;
        java.io.File[] children=file.listFiles();if(children==null)return false;
        for(java.io.File child:children)if(cookieFileExists(child))return true;
        return false;
    }
    private void listLocations(java.io.File root,java.io.File file,StringBuilder result,int depth) {
        if(depth>4 || result.length()>8000 || java.nio.file.Files.isSymbolicLink(file.toPath())) return;
        if(file.isDirectory()) {
            result.append(file.getAbsolutePath().substring(root.getAbsolutePath().length())).append("/;");
            java.io.File[] files=file.listFiles();if(files!=null)for(java.io.File child:files)listLocations(root,child,result,depth+1);
        } else if(file.getName().toLowerCase(java.util.Locale.ROOT).contains("cookie"))
            result.append(file.getAbsolutePath().substring(root.getAbsolutePath().length())).append(";");
    }
    @Override public void onDestroy() {
        if(web!=null){web.stopLoading();web.destroy();web=null;}super.onDestroy();
    }
}
