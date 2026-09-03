package cn.edu.ustc.xiaowo;

import android.net.Uri;
import java.util.Locale;

final class AppConfig {
    private AppConfig() {}

    static Uri serverOrigin() {
        Uri origin = Uri.parse(BuildConfig.XIAOWO_SERVER_ORIGIN);
        if (!isValidOrigin(origin, BuildConfig.XIAOWO_REQUIRE_HTTPS)) {
            throw new IllegalStateException("Invalid Xiaowo server origin for " + BuildConfig.XIAOWO_CHANNEL);
        }
        return origin;
    }

    static Uri homeUri() {
        return serverOrigin().buildUpon().path("/").clearQuery().fragment(null).build();
    }

    static Uri updateManifestUri() {
        return Uri.parse(serverOrigin().toString() + BuildConfig.XIAOWO_UPDATE_PATH);
    }

    static String userAgentSuffix() {
        return "XiaowoAndroid/" + BuildConfig.VERSION_NAME + " (" + BuildConfig.XIAOWO_CHANNEL + ")";
    }

    static boolean isValidOrigin(Uri uri, boolean requireHttps) {
        if (uri == null || uri.getScheme() == null || uri.getHost() == null) return false;
        String scheme = uri.getScheme().toLowerCase(Locale.ROOT);
        if (!"http".equals(scheme) && !"https".equals(scheme)) return false;
        if (requireHttps && !"https".equals(scheme)) return false;
        String path = uri.getPath();
        return uri.getUserInfo() == null &&
            uri.getQuery() == null &&
            uri.getFragment() == null &&
            (path == null || path.isEmpty() || "/".equals(path));
    }
}
