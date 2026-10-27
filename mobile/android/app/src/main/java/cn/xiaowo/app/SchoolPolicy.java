package cn.xiaowo.app;

import java.net.URI;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

final class SchoolPolicy {
    static final String LOGIN_URL = "https://id.ustc.edu.cn/cas/login?service=https%3A%2F%2Fjw.ustc.edu.cn%2Fucas-sso%2Flogin";
    private static final Set<String> COOKIE_NAMES = new HashSet<>(Arrays.asList(
        "SOURCEID_TGC", "SESSION", "SVRNAME", "rememberToken", "deviceID", "device"));
    private SchoolPolicy() {}

    static boolean allowedCookieUrl(String value) {
        URI uri = secureUri(value);
        return uri != null && ("id.ustc.edu.cn".equalsIgnoreCase(uri.getHost())
            || "jw.ustc.edu.cn".equalsIgnoreCase(uri.getHost()));
    }

    private static URI secureUri(String value) {
        if (value == null || value.length() > 8192 || value.indexOf('\\') >= 0) return null;
        try {
            URI uri = new URI(value);
            return "https".equalsIgnoreCase(uri.getScheme()) && uri.getRawUserInfo() == null
                && uri.getHost() != null && (uri.getPort() == -1 || uri.getPort() == 443) ? uri : null;
        } catch (Exception ignored) { return null; }
    }

    static boolean isLanding(String value) {
        if (!allowedCookieUrl(value)) return false;
        URI uri = URI.create(value);
        String path = uri.getPath().replaceAll("/+$", "").toLowerCase(Locale.ROOT);
        return "jw.ustc.edu.cn".equalsIgnoreCase(uri.getHost())
            && !path.contains("ucas-sso/login") && !"/login".equals(path) && !"/cas/login".equals(path);
    }

    static Map<String,String> cookies(String url, String header, boolean active) {
        if (!active) throw new IllegalStateException("LOGIN_NOT_ACTIVE");
        if (!allowedCookieUrl(url)) throw new IllegalArgumentException("URL_NOT_ALLOWED");
        Map<String,String> result = new LinkedHashMap<>();
        if (header == null || header.isEmpty()) return result;
        if (header.length() > 32768) throw new IllegalArgumentException("INVALID_COOKIE_HEADER");
        for (int i = 0; i < header.length(); i++) {
            char character = header.charAt(i);
            if (character < 32 || character == 127) throw new IllegalArgumentException("INVALID_COOKIE_HEADER");
        }
        for (String pair : header.split(";")) {
            int separator = pair.indexOf('=');
            if (separator < 1) continue;
            String name = pair.substring(0, separator).trim();
            String value = pair.substring(separator + 1).trim();
            if (!COOKIE_NAMES.contains(name) || value.isEmpty()) continue;
            if (value.length() > 4096 || result.containsKey(name)) throw new IllegalArgumentException("AMBIGUOUS_COOKIE");
            result.put(name, value);
        }
        return result;
    }
}
