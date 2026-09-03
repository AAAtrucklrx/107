package cn.edu.ustc.xiaowo;

import android.net.Uri;
import java.util.Locale;

final class NavigationPolicy {
    enum Route {
        ALLOW_IN_MAIN,
        OPEN_CUSTOM_TAB,
        OPEN_EXTERNAL_APP,
        BLOCK
    }

    private NavigationPolicy() {}

    static Route routeMainFrame(Uri uri) {
        if (uri == null) return Route.BLOCK;
        if (isMainOrigin(uri) || isBlank(uri)) return Route.ALLOW_IN_MAIN;
        if (isHttps(uri)) return Route.OPEN_CUSTOM_TAB;
        if (isExternalAppUri(uri)) return Route.OPEN_EXTERNAL_APP;
        return Route.BLOCK;
    }

    static boolean isMainOrigin(Uri uri) {
        return sameOrigin(uri, AppConfig.serverOrigin());
    }

    static boolean isMainHome(Uri uri) {
        if (!isMainOrigin(uri)) return false;
        String path = uri.getPath();
        return path == null || path.isEmpty() || "/".equals(path);
    }

    static boolean isHttps(Uri uri) {
        return uri != null && "https".equalsIgnoreCase(uri.getScheme()) && uri.getHost() != null;
    }

    static boolean isWebUri(Uri uri) {
        if (uri == null || uri.getHost() == null) return false;
        String scheme = uri.getScheme();
        return "http".equalsIgnoreCase(scheme) || "https".equalsIgnoreCase(scheme);
    }

    static boolean isExternalAppUri(Uri uri) {
        if (uri == null || uri.getScheme() == null) return false;
        return switch (uri.getScheme().toLowerCase(Locale.ROOT)) {
            case "mailto", "tel", "sms", "geo", "market" -> true;
            default -> false;
        };
    }

    static boolean isBlank(Uri uri) {
        return uri != null && "about".equalsIgnoreCase(uri.getScheme()) && "blank".equalsIgnoreCase(uri.getSchemeSpecificPart());
    }

    static String displayAuthority(Uri uri) {
        if (uri == null || uri.getHost() == null) return "";
        String prefix = isHttps(uri) ? "https://" : "不安全 · http://";
        int port = effectivePort(uri);
        boolean standardPort = ("https".equalsIgnoreCase(uri.getScheme()) && port == 443) ||
            ("http".equalsIgnoreCase(uri.getScheme()) && port == 80);
        return prefix + uri.getHost().toLowerCase(Locale.ROOT) + (standardPort ? "" : ":" + port);
    }

    private static boolean sameOrigin(Uri left, Uri right) {
        if (left == null || right == null || left.getScheme() == null || right.getScheme() == null) return false;
        if (!left.getScheme().equalsIgnoreCase(right.getScheme())) return false;
        if (left.getHost() == null || right.getHost() == null || !left.getHost().equalsIgnoreCase(right.getHost())) return false;
        return effectivePort(left) == effectivePort(right);
    }

    private static int effectivePort(Uri uri) {
        if (uri.getPort() != -1) return uri.getPort();
        return "https".equalsIgnoreCase(uri.getScheme()) ? 443 : 80;
    }
}
