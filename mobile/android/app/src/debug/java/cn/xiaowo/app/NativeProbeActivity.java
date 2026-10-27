package cn.xiaowo.app;
import android.app.Activity;
import android.os.Bundle;
import android.view.WindowManager;
import android.widget.TextView;
public final class NativeProbeActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        TextView text=new TextView(this);text.setText("小蛙助手：离线原生测试");setContentView(text);
    }
}
