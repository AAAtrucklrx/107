package cn.xiaowo.app;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginHandle;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/** Read-only core adapter; the main process never opens the school CookieManager profile. */
@CapacitorPlugin(name="CapacitorCookies")
public final class SchoolCookiesPlugin extends Plugin {
    @PluginMethod public void getCookies(PluginCall call) {
        PluginHandle handle=getBridge().getPlugin("SchoolLogin");
        if(handle==null || !(handle.getInstance() instanceof SchoolLoginPlugin)) {
            call.reject("学校登录窗口不可用","LOGIN_NOT_ACTIVE");return;
        }
        ((SchoolLoginPlugin)handle.getInstance()).client().cookies(call.getString("url"),(data,error)->{
            if(error!=null) {call.reject("学校登录状态读取失败，请重试",error);return;}
            JSObject result=new JSObject();
            for(String name:data.keySet()) result.put(name,data.getString(name));
            call.resolve(result);
        });
    }
    @PluginMethod public void setCookie(PluginCall call) {call.reject("学校登录状态仅允许读取","READ_ONLY");}
    @PluginMethod public void deleteCookie(PluginCall call) {call.reject("请使用 SchoolLogin.cleanup 清理","READ_ONLY");}
    @PluginMethod public void clearCookies(PluginCall call) {call.reject("请使用 SchoolLogin.cleanup 清理","READ_ONLY");}
    @PluginMethod public void clearAllCookies(PluginCall call) {call.reject("请使用 SchoolLogin.cleanup 清理","READ_ONLY");}
}
