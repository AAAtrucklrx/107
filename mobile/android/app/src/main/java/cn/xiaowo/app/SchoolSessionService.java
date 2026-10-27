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
import java.util.Map;

/** Same-UID, non-exported IPC. School credentials are returned only in Binder memory. */
public final class SchoolSessionService extends Service {
    static SchoolSessionService current;
    private Messenger client;
    private SchoolLoginActivity window;
    private int openRequest;
    private int closeRequest;
    private boolean active;
    private boolean abandoned;
    private boolean cancelNotified;
    private boolean launchPending;
    private IBinder clientBinder;
    private final Handler main=new Handler(Looper.getMainLooper());
    private final IBinder.DeathRecipient clientDeath=()->main.post(this::abandon);

    private final Messenger endpoint = new Messenger(new Handler(Looper.getMainLooper(), this::receive));
    @Override public void onCreate() { super.onCreate(); current=this; }
    @Override public IBinder onBind(Intent intent) { return endpoint.getBinder(); }
    @Override public boolean onUnbind(Intent intent) { abandon();return false; }
    @Override public void onDestroy() {
        if(window!=null) window.closeWindow();
        if(clientBinder!=null) {try {clientBinder.unlinkToDeath(clientDeath,0);}catch(Exception ignored){}}
        current=null;super.onDestroy();
    }
    private void abandon() {
        if(abandoned) return;
        abandoned=true;active=false;client=null;
        if(window!=null) window.closeWindow();else exitProcess();
    }
    private void exitProcess() {stopSelf();main.post(()->Process.killProcess(Process.myPid()));}

    private boolean receive(Message message) {
        if (message.sendingUid != Process.myUid()) return true;
        if (message.replyTo == null) return true;
        if(clientBinder!=null && clientBinder!=message.replyTo.getBinder() && !clientBinder.equals(message.replyTo.getBinder())) return true;
        if(clientBinder==null) {
            clientBinder=message.replyTo.getBinder();
            try {clientBinder.linkToDeath(clientDeath,0);}catch(Exception ignored){abandon();return true;}
        }
        client=message.replyTo;
        Bundle data=message.getData();
        try {
            switch(message.what) {
                case SchoolProtocol.HELLO:
                    Bundle hello=new Bundle();hello.putInt("pid",Process.myPid());respond(message.arg1,hello,null);break;
                case SchoolProtocol.OPEN:
                    if (active || openRequest != 0) { respond(message.arg1,null,"LOGIN_BUSY"); break; }
                    openRequest=message.arg1;launchPending=true;cancelNotified=false;
                    if(window != null) attached(window);
                    break;
                case SchoolProtocol.COOKIES:
                    if (!active || window == null || !window.isActive()) { respond(message.arg1,null,"LOGIN_NOT_ACTIVE"); break; }
                    String url=data.getString("url");
                    if (!SchoolPolicy.allowedCookieUrl(url)) { respond(message.arg1,null,"URL_NOT_ALLOWED"); break; }
                    Bundle cookies=new Bundle();
                    for(Map.Entry<String,String> item:SchoolPolicy.cookies(url,CookieManager.getInstance().getCookie(url),true).entrySet())
                        cookies.putString(item.getKey(),item.getValue());
                    respond(message.arg1,cookies,null);break;
                case SchoolProtocol.STATUS:
                    String state=data.getString("state", ""), text=data.getString("message", "");
                    if (!active || window == null || !window.isActive()) { respond(message.arg1,null,"LOGIN_NOT_ACTIVE"); break; }
                    if (!(state.equals("loading") || state.equals("error") || state.equals("success")) || text.length()>300) {
                        respond(message.arg1,null,"INVALID_STATUS");break;
                    }
                    window.showStatus(state,text);respond(message.arg1,null,null);break;
                case SchoolProtocol.ABORT_LAUNCH:
                    if(window!=null) {respond(message.arg1,null,"LOGIN_BUSY");break;}
                    launchPending=false;failOpen("LOGIN_OPEN_FAILED");respond(message.arg1,null,null);break;
                case SchoolProtocol.CLOSE:
                    active=false;closeRequest=message.arg1;
                    if(openRequest!=0) {respond(openRequest,null,"LOGIN_CANCELLED");openRequest=0;}
                    if(window != null) window.closeWindow();
                    else if(!launchPending) {respond(closeRequest,null,null);closeRequest=0;}

                    break;
                default: respond(message.arg1,null,"INVALID_COMMAND");
            }
        } catch(Exception ignored) { respond(message.arg1,null,"SCHOOL_OPERATION_FAILED"); }
        return true;
    }
    boolean hasClient() { return client != null && !abandoned && closeRequest==0; }
    void failOpen(String error) { if(openRequest!=0) {respond(openRequest,null,error);openRequest=0;} }
    void windowClosing(SchoolLoginActivity activity) {
        active=false;
        if(closeRequest==0 && !abandoned && !cancelNotified) {cancelNotified=true;event("cancelled",new Bundle());}
    }
    void attached(SchoolLoginActivity activity) {
        window=activity;launchPending=false;
        if(closeRequest!=0 || abandoned) {activity.closeWindow();return;}
        if(openRequest != 0) { active=true;respond(openRequest,null,null);openRequest=0; }
    }
    void detached(SchoolLoginActivity activity, boolean cancelled) {
        if(window == activity) window=null;
        active=false;launchPending=false;
        if(abandoned) {exitProcess();return;}
        if(closeRequest != 0) { respond(closeRequest,null,null);closeRequest=0; }
        else if(cancelled && !cancelNotified) {cancelNotified=true;event("cancelled",new Bundle());}
    }
    void event(String event, Bundle payload) {
        if(client==null) return;
        payload.putString("event",event);
        Message message=Message.obtain(null,SchoolProtocol.EVENT);message.setData(payload);
        try {client.send(message);}catch(Exception ignored) {}
    }
    private void respond(int request, Bundle payload, String error) {
        if(client==null) return;
        Message response=Message.obtain(null,SchoolProtocol.RESULT);response.arg1=request;
        Bundle envelope=new Bundle();envelope.putBundle("result",payload==null?new Bundle():payload);
        if(error!=null) envelope.putString("error",error);response.setData(envelope);
        try {client.send(response);}catch(Exception ignored) {}
    }
}
