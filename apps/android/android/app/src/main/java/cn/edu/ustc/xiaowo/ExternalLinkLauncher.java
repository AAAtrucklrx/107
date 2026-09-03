package cn.edu.ustc.xiaowo;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.widget.Toast;
import androidx.browser.customtabs.CustomTabColorSchemeParams;
import androidx.browser.customtabs.CustomTabsIntent;
import androidx.core.content.ContextCompat;

final class ExternalLinkLauncher {
    private ExternalLinkLauncher() {}

    static void openWeb(Activity activity, Uri uri) {
        if (!NavigationPolicy.isHttps(uri)) {
            showBlocked(activity);
            return;
        }
        try {
            CustomTabColorSchemeParams colors = new CustomTabColorSchemeParams.Builder()
                .setToolbarColor(ContextCompat.getColor(activity, R.color.xiaowo_surface))
                .setNavigationBarColor(ContextCompat.getColor(activity, R.color.xiaowo_surface))
                .build();
            CustomTabsIntent intent = new CustomTabsIntent.Builder()
                .setDefaultColorSchemeParams(colors)
                .setShowTitle(true)
                .setShareState(CustomTabsIntent.SHARE_STATE_ON)
                .build();
            intent.launchUrl(activity, uri);
        } catch (ActivityNotFoundException | SecurityException error) {
            openWithSystem(activity, uri);
        }
    }

    static void openExternalApp(Activity activity, Uri uri) {
        if (!NavigationPolicy.isExternalAppUri(uri)) {
            showBlocked(activity);
            return;
        }
        openWithSystem(activity, uri);
    }

    private static void openWithSystem(Activity activity, Uri uri) {
        try {
            activity.startActivity(new Intent(Intent.ACTION_VIEW, uri));
        } catch (ActivityNotFoundException | SecurityException error) {
            Toast.makeText(activity, R.string.external_app_unavailable, Toast.LENGTH_LONG).show();
        }
    }

    private static void showBlocked(Activity activity) {
        Toast.makeText(activity, R.string.external_navigation_blocked, Toast.LENGTH_LONG).show();
    }
}
