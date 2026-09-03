package cn.edu.ustc.xiaowo;

import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.webkit.WebView;
import org.json.JSONException;
import org.json.JSONObject;

final class WebCompatibility {
    private static final String COPY_PROMPT = "__XIAOWO_COPY_V2__";
    private static final String SAVE_PROMPT = "__XIAOWO_SAVE_V2__";
    private static final int MAX_CLIPBOARD_CHARS = 1_000_000;

    enum Type {
        COPY,
        SAVE
    }

    static final class Request {
        final Type type;
        final String payload;
        final String clipboardText;
        final DownloadStore.Preview downloadPreview;

        private Request(Type type, String payload, String clipboardText, DownloadStore.Preview downloadPreview) {
            this.type = type;
            this.payload = payload;
            this.clipboardText = clipboardText;
            this.downloadPreview = downloadPreview;
        }

        static Request copy(String text) {
            return new Request(Type.COPY, null, text, null);
        }

        static Request save(String payload, DownloadStore.Preview preview) {
            return new Request(Type.SAVE, payload, null, preview);
        }
    }

    private static final String JAVASCRIPT = """
        (() => {
          if (window.__xiaowoAndroidCompatV2) return;
          window.__xiaowoAndroidCompatV2 = true;

          const nativeCreateObjectURL = URL.createObjectURL.bind(URL);
          const nativeRevokeObjectURL = URL.revokeObjectURL.bind(URL);
          const blobUrls = new Map();
          URL.createObjectURL = (value) => {
            const url = nativeCreateObjectURL(value);
            if (value instanceof Blob) blobUrls.set(url, value);
            return url;
          };
          URL.revokeObjectURL = (url) => {
            blobUrls.delete(url);
            nativeRevokeObjectURL(url);
          };

          if (!navigator.clipboard?.writeText) {
            const clipboard = {
              writeText: (text) => {
                const result = window.prompt('__XIAOWO_COPY_V2__', JSON.stringify({ text: String(text) }));
                return result === null ? Promise.reject(new DOMException('Copy cancelled', 'NotAllowedError')) : Promise.resolve();
              }
            };
            try {
              Object.defineProperty(navigator, 'clipboard', { configurable: true, value: clipboard });
            } catch (_) {
              navigator.clipboard = clipboard;
            }
          }

          const requestBlobSave = (anchor, blob) => {
            if (!blob || blob.size <= 0 || blob.size > 8 * 1024 * 1024) return false;
            const reader = new FileReader();
            reader.onload = () => {
              const value = String(reader.result || '');
              const comma = value.indexOf(',');
              if (comma < 0) return;
              window.prompt('__XIAOWO_SAVE_V2__', JSON.stringify({
                name: anchor.download || 'download.bin',
                mime: blob.type || 'application/octet-stream',
                data: value.slice(comma + 1)
              }));
            };
            reader.readAsDataURL(blob);
            return true;
          };

          document.addEventListener('click', (event) => {
            const anchor = event.target instanceof Element ? event.target.closest('a[download]') : null;
            if (!anchor || !anchor.href?.startsWith('blob:')) return;
            const blob = blobUrls.get(anchor.href);
            if (requestBlobSave(anchor, blob)) event.preventDefault();
          }, true);

          const nativeAnchorClick = HTMLAnchorElement.prototype.click;
          HTMLAnchorElement.prototype.click = function () {
            const blob = this.download && this.href?.startsWith('blob:') ? blobUrls.get(this.href) : null;
            if (!requestBlobSave(this, blob)) nativeAnchorClick.call(this);
          };
        })();
        """;

    private WebCompatibility() {}

    static void inject(WebView webView) {
        webView.evaluateJavascript(JAVASCRIPT, null);
    }

    static Request parsePrompt(String message, String defaultValue) {
        try {
            if (COPY_PROMPT.equals(message)) {
                String text = new JSONObject(defaultValue == null ? "{}" : defaultValue).optString("text", "");
                if (text.isEmpty() || text.length() > MAX_CLIPBOARD_CHARS) return null;
                return Request.copy(text);
            }
            if (SAVE_PROMPT.equals(message)) {
                return Request.save(defaultValue, DownloadStore.inspectPayload(defaultValue));
            }
        } catch (JSONException ignored) {
            return null;
        }
        return null;
    }

    static void copyToClipboard(Context context, String text) {
        ClipboardManager clipboard = (ClipboardManager) context.getSystemService(Context.CLIPBOARD_SERVICE);
        clipboard.setPrimaryClip(ClipData.newPlainText(context.getString(R.string.clipboard_label), text));
    }
}
