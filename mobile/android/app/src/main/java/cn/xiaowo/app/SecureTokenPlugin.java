package cn.xiaowo.app;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONObject;

@CapacitorPlugin(name = "SecureToken")
public final class SecureTokenPlugin extends Plugin {
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private TokenVault vault;
    @Override public void load() { vault = new TokenVault(getContext().getApplicationContext()); }
    @PluginMethod public void get(PluginCall call) {
        worker.execute(() -> {
            try {
                String value = vault.get();
                JSObject result = new JSObject();
                result.put("value", value == null ? JSONObject.NULL : value);
                call.resolve(result);
            } catch (Exception ignored) { call.reject("无法读取安全登录状态，请重新登录", "SECURE_STORAGE_FAILED"); }
        });
    }
    @PluginMethod public void set(PluginCall call) {
        String value = call.getString("value");
        worker.execute(() -> {
            try { vault.set(value); call.resolve(); }
            catch (Exception ignored) { call.reject("无法安全保存登录状态，请重试", "SECURE_STORAGE_FAILED"); }
        });
    }
    @PluginMethod public void remove(PluginCall call) {
        worker.execute(() -> {
            try { vault.remove(); call.resolve(); }
            catch (Exception ignored) { call.reject("无法清除安全登录状态，请重试", "SECURE_STORAGE_FAILED"); }
        });
    }
    @Override protected void handleOnDestroy() { worker.shutdown(); }
}
