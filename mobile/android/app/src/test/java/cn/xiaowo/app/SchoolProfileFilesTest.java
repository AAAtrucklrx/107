package cn.xiaowo.app;
import static org.junit.Assert.*;
import java.io.File;
import java.nio.file.Files;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
public class SchoolProfileFilesTest {
    @Rule public TemporaryFolder temp = new TemporaryFolder();
    @Test public void deletesOnlyDedicatedDataAndCache() throws Exception {
        File data=temp.newFolder("data"), cache=temp.newFolder("cache");
        File ordinary=new File(data,"app_webview/Default"); ordinary.mkdirs();
        Files.write(new File(ordinary,"history").toPath(),new byte[]{1});
        File school=new File(data,"app_webview_school_login/Default"); school.mkdirs();
        Files.write(new File(school,"Cookies-wal").toPath(),new byte[]{2});
        new File(cache,"webview_school_login/Cache").mkdirs();
        File huawei=new File(data,"app_hws_webview_school_login/Default");huawei.mkdirs();
        Files.write(new File(huawei,"Cookies").toPath(),new byte[]{3});
        File huaweiDefault=new File(data,"app_hws_webview/Default");huaweiDefault.mkdirs();
        Files.write(new File(huaweiDefault,"history").toPath(),new byte[]{4});
        SchoolProfileFiles.delete(data,cache,false);
        assertTrue(new File(ordinary,"history").isFile());
        assertFalse(new File(data,"app_webview_school_login").exists());
        assertFalse(new File(cache,"webview_school_login").exists());
        assertFalse(new File(data,"app_hws_webview_school_login").exists());
        assertTrue(new File(huaweiDefault,"history").isFile());
    }
    @Test public void refusesToDeleteWhileSchoolProcessLives() throws Exception {
        File data=temp.newFolder("data"), cache=temp.newFolder("cache");
        File school=new File(data,"app_webview_school_login"); school.mkdirs();
        assertThrows(IllegalStateException.class,()->SchoolProfileFiles.delete(data,cache,true));
        assertTrue(school.exists());
    }
    @Test public void neverFollowsNestedSymlinksIntoDefaultProfile() throws Exception {
        File data=temp.newFolder("data"), cache=temp.newFolder("cache");
        File ordinary=new File(data,"app_webview");ordinary.mkdirs();
        Files.write(new File(ordinary,"history").toPath(),new byte[]{1});
        File school=new File(data,"app_webview_school_login");school.mkdirs();
        Files.createSymbolicLink(new File(school,"escape").toPath(),ordinary.toPath());
        SchoolProfileFiles.delete(data,cache,false);
        assertTrue(new File(ordinary,"history").isFile());
        assertFalse(school.exists());
    }
    @Test public void refusesASymlinkAsProfileRoot() throws Exception {
        File data=temp.newFolder("data"), cache=temp.newFolder("cache");
        File ordinary=new File(data,"app_webview");ordinary.mkdirs();
        Files.createSymbolicLink(new File(data,"app_webview_school_login").toPath(),ordinary.toPath());
        assertThrows(java.io.IOException.class,()->SchoolProfileFiles.delete(data,cache,false));
        assertTrue(ordinary.exists());
    }
}
