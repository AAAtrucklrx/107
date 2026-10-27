package cn.xiaowo.app;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;

/** Only Chromium's fixed suffix directories are eligible; never follow a filesystem link. */
final class SchoolProfileFiles {
    static final String SUFFIX = "school_login";
    static void delete(File dataDir, File cacheDir, boolean schoolProcessAlive) throws Exception {
        if (schoolProcessAlive) throw new IllegalStateException("SCHOOL_PROCESS_ALIVE");
        File data = new File(dataDir, "app_webview_" + SUFFIX);
        // Huawei 114 honors the suffix but stores its actual Chromium profile under this additional root.
        File huawei = new File(dataDir, "app_hws_webview_" + SUFFIX);
        File cache = new File(cacheDir, "webview_" + SUFFIX);
        validateRoot(data, dataDir);
        validateRoot(huawei,dataDir);
        validateRoot(cache, cacheDir);
        remove(data);
        remove(huawei);
        remove(cache);
        if (Files.exists(data.toPath(), LinkOption.NOFOLLOW_LINKS) || Files.exists(huawei.toPath(), LinkOption.NOFOLLOW_LINKS) || Files.exists(cache.toPath(), LinkOption.NOFOLLOW_LINKS))
            throw new IOException("PROFILE_REMAINED");
    }
    static void requireInitialized(File dataDir,File cacheDir,String provider) throws IOException {
        File data=new File(dataDir,"app_webview_"+SUFFIX), cache=new File(cacheDir,"webview_"+SUFFIX);
        validateRoot(data,dataDir);validateRoot(cache,cacheDir);
        if(!data.isDirectory() || !cache.isDirectory()) throw new IOException("PROFILE_PATH_UNSUPPORTED");
        File huawei=new File(dataDir,"app_hws_webview_"+SUFFIX);
        validateRoot(huawei,dataDir);
        if("com.huawei.webview".equals(provider) && !huawei.isDirectory()) throw new IOException("PROFILE_PATH_UNSUPPORTED");
    }
    private static void validateRoot(File root, File parent) throws IOException {
        if (Files.isSymbolicLink(root.toPath()) || !root.getCanonicalFile().getParentFile().equals(parent.getCanonicalFile()))
            throw new IOException("PROFILE_PATH_INVALID");
    }
    private static void remove(File file) throws IOException {
        if (!Files.exists(file.toPath(), LinkOption.NOFOLLOW_LINKS)) return;
        if (Files.isSymbolicLink(file.toPath())) { Files.delete(file.toPath()); return; }
        if (file.isDirectory()) {
            File[] children = file.listFiles();
            if (children == null) throw new IOException("PROFILE_UNREADABLE");
            for (File child : children) remove(child);
        }
        Files.delete(file.toPath());
    }
}
