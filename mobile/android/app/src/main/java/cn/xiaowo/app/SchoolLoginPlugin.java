package cn.xiaowo.app;

import android.os.Bundle;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name="SchoolLogin")
public final class SchoolLoginPlugin extends Plugin {
    private SchoolProcessClient client;
    @Override public void load() {
        client=SchoolProcessClient.attach(getActivity(),(name,data)->{
            JSObject event=new JSObject();
            if("landed".equals(name)) event.put("url","https://jw.ustc.edu.cn/");
            notifyListeners(name,event);
        });
        client.cleanup((ignored,error)->{});
    }
    SchoolProcessClient client() { return client; }
    @PluginMethod public void open(PluginCall call) { client.open((data,error)->complete(call,error)); }
    @PluginMethod public void showStatus(PluginCall call) {
        client.status(call.getString("state",""),call.getString("message",""),(data,error)->complete(call,error));
    }
    @PluginMethod public void close(PluginCall call) { client.cleanup((data,error)->complete(call,error)); }
    @PluginMethod public void cleanup(PluginCall call) { client.cleanup((data,error)->complete(call,error)); }
    private void complete(PluginCall call,String error) {
        if(error==null) call.resolve();
        else call.reject("学校登录未完成或数据尚未清理，请重试",error);
    }
    @Override protected void handleOnDestroy() {
        if(client!=null) {client.cleanup((data,error)->{});client.release(getActivity());}
    }
}
