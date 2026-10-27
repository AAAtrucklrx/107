package cn.xiaowo.app;

import android.app.Activity;
import android.app.ActivityManager;
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
import android.os.Process;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Owns the process lifetime. Deletion is unreachable until Binder death and process absence agree. */
final class SchoolProcessClient {
    interface Result { void complete(Bundle value, String error); }
    interface Events { void emit(String name, Bundle value); }
    private static SchoolProcessClient instance;
    static synchronized SchoolProcessClient attach(Activity activity,Events events) {
        if(instance==null) instance=new SchoolProcessClient(activity,events);
        else {instance.activity=new java.lang.ref.WeakReference<>(activity);instance.events=events;}
        return instance;
    }
    private java.lang.ref.WeakReference<Activity> activity;
    private final Context context;
    private Events events;
    private final Handler main=new Handler(Looper.getMainLooper());
    private final ExecutorService files=Executors.newSingleThreadExecutor();
    private final Map<Integer,Result> pending=new HashMap<>();
    private final List<Result> cleanupWaiters=new ArrayList<>();
    private final Messenger receiver=new Messenger(new Handler(Looper.getMainLooper(),this::receive));
    private Messenger remote;
    private IBinder binder;
    private int pid;
    private int nextId;
    private long generation;
    private long connectionGeneration;
    private boolean bound;
    private boolean active;
    private boolean opening;
    private boolean cleaning;
    private boolean deathObserved;
    private Result connectResult;
    private Result shutdownResult;
    private boolean awaitingDeath;
    private ServiceConnection connection;
    SchoolProcessClient(Activity activity, Events events) {
        this.activity=new java.lang.ref.WeakReference<>(activity);this.context=activity.getApplicationContext();this.events=events;
    }
    void release(Activity owner) {if(activity.get()==owner) {activity.clear();events=(name,data)->{};}}
    boolean isActive() { return active && !cleaning; }
    void open(Result result) {
        final Result outcome=once(result);
        main.post(()->{
            if(opening || active || cleaning) { outcome.complete(null,"LOGIN_BUSY");return; }
            opening=true;
            final long attempt=++generation;
            cleanupInternal((ignored,error)->{
                if(attempt!=generation) {outcome.complete(null,"LOGIN_CANCELLED");return;}
                if(error!=null) { opening=false;outcome.complete(null,error);return; }
                connect((ready,connectionError)->{
                    if(attempt!=generation) {
                        outcome.complete(null,"LOGIN_CANCELLED");
                        if(cleaning) {if(connectionError==null) closeRemote();else finishCleanup(connectionError);}
                        else cleanupInternal((a,b)->{});
                        return;
                    }
                    if(connectionError!=null) { opening=false;outcome.complete(null,connectionError);cleanupInternal((a,b)->{});return; }
                    request(SchoolProtocol.OPEN,new Bundle(),(data,openError)->{
                        if(attempt!=generation) {outcome.complete(null,"LOGIN_CANCELLED");return;}
                        opening=false;active=openError==null;outcome.complete(data,openError);
                        if(openError!=null) cleanupInternal((a,b)->{});
                    });
                    try {
                        Activity owner=activity.get();
                        if(owner==null || owner.isFinishing()) throw new IllegalStateException();
                        owner.startActivityForResult(new Intent(owner,SchoolLoginActivity.class), 7103);
                    }
                    catch(Exception ignoredLaunch) {
                        opening=false;outcome.complete(null,"LOGIN_OPEN_FAILED");
                        request(SchoolProtocol.ABORT_LAUNCH,new Bundle(),(a,b)->cleanupInternal((x,y)->{}));
                    }
                });
            });
        });
    }
    void cookies(String url, Result result) {
        main.post(()->{
            if(!isActive()) {result.complete(null,"LOGIN_NOT_ACTIVE");return;}
            if(!SchoolPolicy.allowedCookieUrl(url)) {result.complete(null,"URL_NOT_ALLOWED");return;}
            final long attempt=generation;
            Bundle data=new Bundle();data.putString("url",url);
            request(SchoolProtocol.COOKIES,data,(cookies,error)->{
                if(attempt!=generation || !isActive()) result.complete(null,"LOGIN_CANCELLED");
                else result.complete(cookies,error);
            });
        });
    }
    void status(String state,String text,Result result) {
        main.post(()->{
            if(!isActive()) {result.complete(null,"LOGIN_NOT_ACTIVE");return;}
            Bundle data=new Bundle();data.putString("state",state);data.putString("message",text);
            request(SchoolProtocol.STATUS,data,result);
        });
    }
    void cleanup(Result result) {
        main.post(()->{++generation;opening=false;cleanupInternal(result);});
    }
    private void cleanupInternal(Result result) {
        if(Looper.myLooper()!=Looper.getMainLooper()) {main.post(()->cleanupInternal(result));return;}
        cleanupWaiters.add(result);
        if(cleaning) return;
        cleaning=true;active=false;
        if(remote!=null && binder!=null && binder.isBinderAlive()) {closeRemote();return;}
        if(bound && remote==null) return; // Existing connect callback will close after cancellation.
        List<Integer> live=schoolPids(context);
        if(live==null) {finishCleanup("PROCESS_STATE_UNKNOWN");return;}
        if(!live.isEmpty()) {
            connect((data,error)->{if(error!=null) finishCleanup(error);else closeRemote();});
        } else deleteProfile();
    }
    private void closeRemote() {
        shutdownResult=(data,error)->{
            if(error!=null) {finishCleanup(error);return;}
            // CLOSE is acknowledged only after Activity.onDestroy, so Android cannot restore a live school page.
            try {Activity owner=activity.get();if(owner!=null)owner.finishActivity(7103);}catch(Exception ignored){}
            detach();
            context.stopService(new Intent(context,SchoolSessionService.class));
            List<Integer> live=schoolPids(context);
            if(live==null || pid<=0) {finishCleanup("PROCESS_STATE_UNKNOWN");return;}
            if(live.contains(pid)) Process.killProcess(pid);
            awaitDeath(System.currentTimeMillis()+10000);
        };
        request(SchoolProtocol.CLOSE,new Bundle(),shutdownResult);
    }
    private void awaitDeath(long deadline) {
        awaitingDeath=true;
        List<Integer> live=schoolPids(context);
        if(live==null) {finishCleanup("PROCESS_STATE_UNKNOWN");return;}
        boolean binderDead=deathObserved || binder==null || !binder.isBinderAlive();
        boolean pidGone=pid==0 || !new java.io.File("/proc/"+pid).exists();
        if(binderDead && pidGone && live.isEmpty()) {deleteProfile();return;}
        if(System.currentTimeMillis()>=deadline) {finishCleanup("PROCESS_EXIT_TIMEOUT");return;}
        main.postDelayed(()->awaitDeath(deadline),100);
    }
    private void deleteProfile() {
        files.execute(()->{
            String error=null;
            try {
                List<Integer> live=schoolPids(context);
                if(live==null) throw new IllegalStateException();
                SchoolProfileFiles.delete(context.getDataDir(),context.getCacheDir(),!live.isEmpty());
            } catch(Exception ignored) {error="PROFILE_CLEANUP_FAILED";}
            String outcome=error;main.post(()->finishCleanup(outcome));
        });
    }
    private void finishCleanup(String error) {
        cleaning=false;awaitingDeath=false;shutdownResult=null;
        if(error==null) detach();
        if(error==null) {remote=null;binder=null;pid=0;deathObserved=false;}
        List<Result> callbacks=new ArrayList<>(cleanupWaiters);cleanupWaiters.clear();
        for(Result callback:callbacks) callback.complete(new Bundle(),error);
    }
    private void connect(Result result) {
        if(bound || connectResult!=null) {result.complete(null,"SERVICE_BUSY");return;}
        connectResult=result;
        final long token=++connectionGeneration;
        connection=new ServiceConnection() {
            @Override public void onServiceConnected(ComponentName name,IBinder service) {
                if(token!=connectionGeneration) return;
                binder=service;remote=new Messenger(service);deathObserved=false;
                try {service.linkToDeath(()->main.post(()->{if(binder==service)died();}),0);}
                catch(Exception ignored) {died();return;}
                request(SchoolProtocol.HELLO,new Bundle(),(data,error)->{
                    if(token!=connectionGeneration) return;
                    if(error!=null) {finishConnection(error);return;}
                    int reported=data.getInt("pid",0);
                    List<Integer> live=schoolPids(context);
                    if(reported==Process.myPid() || live==null || !live.contains(reported)) {finishConnection("PROCESS_ID_INVALID");return;}
                    pid=reported;finishConnection(null);
                });
            }
            @Override public void onServiceDisconnected(ComponentName name) {if(token==connectionGeneration)died();}
            @Override public void onBindingDied(ComponentName name) {if(token==connectionGeneration)died();}
            @Override public void onNullBinding(ComponentName name) {if(token==connectionGeneration)finishConnection("SERVICE_UNAVAILABLE");}
        };
        try {
            bound=context.bindService(new Intent(context,SchoolSessionService.class),connection,Context.BIND_AUTO_CREATE);
            if(!bound) finishConnection("SERVICE_UNAVAILABLE");
            else main.postDelayed(()->{if(token==connectionGeneration && connectResult==result)finishConnection("SERVICE_TIMEOUT");},10000);
        } catch(Exception ignored) {finishConnection("SERVICE_UNAVAILABLE");}
    }
    private void finishConnection(String error) {
        Result callback=connectResult;connectResult=null;
        if(error!=null) detach();
        if(callback!=null) callback.complete(new Bundle(),error);
    }
    private void detach() {
        if(bound) {++connectionGeneration;try {context.unbindService(connection);}catch(Exception ignored){}bound=false;}
    }
    private void request(int command,Bundle data,Result result) {
        if(remote==null) {result.complete(null,"SERVICE_UNAVAILABLE");return;}
        int request=++nextId;pending.put(request,result);
        Message message=Message.obtain(null,command);message.arg1=request;message.replyTo=receiver;message.setData(data);
        try {remote.send(message);}catch(Exception ignored){pending.remove(request);result.complete(null,"SERVICE_UNAVAILABLE");return;}
        main.postDelayed(()->{Result timed=pending.remove(request);if(timed!=null)timed.complete(null,"SCHOOL_REQUEST_TIMEOUT");},10000);
    }
    private boolean receive(Message message) {
        if(message.sendingUid!=Process.myUid()) return true;
        Bundle data=message.getData();
        if(message.what==SchoolProtocol.RESULT) {
            Result callback=pending.remove(message.arg1);
            if(callback!=null) callback.complete(data.getBundle("result"),data.getString("error"));
        } else if(message.what==SchoolProtocol.EVENT) {
            String event=data.getString("event","");
            if(event.equals("cancelled")) {
                active=false;cleanup((ignored,error)->{});events.emit("cancelled",new Bundle());
            } else if(isActive() && (event.equals("landed") || event.equals("retry"))) events.emit(event,data);
        }
        return true;
    }
    private void died() {
        deathObserved=true;
        detach();
        if(cleaning) {
            Result connecting=connectResult;connectResult=null;
            if(connecting!=null) cleanupWaiters.add((data,error)->connecting.complete(null,"SCHOOL_PROCESS_EXITED"));
            Integer closeId=null;
            for(Map.Entry<Integer,Result> item:pending.entrySet()) if(item.getValue()==shutdownResult) closeId=item.getKey();
            if(closeId!=null) {Result callback=pending.remove(closeId);callback.complete(new Bundle(),null);}
            if(!awaitingDeath) awaitDeath(System.currentTimeMillis()+10000);
            return;
        }
        boolean wasActive=active || opening;active=false;opening=false;
        finishConnection("SCHOOL_PROCESS_EXITED");
        List<Result> callbacks=new ArrayList<>(pending.values());pending.clear();
        for(Result callback:callbacks) callback.complete(null,"SCHOOL_PROCESS_EXITED");
        cleanupInternal((ignored,error)->{});
        if(wasActive) events.emit("cancelled",new Bundle());
    }
    private static Result once(Result result) {
        java.util.concurrent.atomic.AtomicBoolean completed=new java.util.concurrent.atomic.AtomicBoolean();
        return (data,error)->{if(completed.compareAndSet(false,true))result.complete(data,error);};
    }
    static List<Integer> schoolPids(Context context) {
        ActivityManager manager=(ActivityManager)context.getSystemService(Context.ACTIVITY_SERVICE);
        if(manager==null) return null;
        List<ActivityManager.RunningAppProcessInfo> processes=manager.getRunningAppProcesses();
        if(processes==null) return null;
        List<Integer> result=new ArrayList<>();
        for(ActivityManager.RunningAppProcessInfo process:processes)
            if(process.uid==Process.myUid() && (context.getPackageName()+":school").equals(process.processName)) result.add(process.pid);
        return result;
    }
}
