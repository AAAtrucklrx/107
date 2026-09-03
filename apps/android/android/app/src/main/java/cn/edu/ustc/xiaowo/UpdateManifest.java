package cn.edu.ustc.xiaowo;

import android.net.Uri;
import java.util.Locale;
import org.json.JSONException;
import org.json.JSONObject;

final class UpdateManifest {
    final int versionCode;
    final String versionName;
    final String releaseNotes;
    final Uri downloadUri;
    final String sha256;

    private UpdateManifest(int versionCode, String versionName, String releaseNotes, Uri downloadUri, String sha256) {
        this.versionCode = versionCode;
        this.versionName = versionName;
        this.releaseNotes = releaseNotes;
        this.downloadUri = downloadUri;
        this.sha256 = sha256;
    }

    static UpdateManifest parse(String json) throws JSONException {
        JSONObject object = new JSONObject(json);
        int versionCode = object.getInt("versionCode");
        String versionName = object.getString("versionName").trim();
        String releaseNotes = object.optString("releaseNotes", "").trim();
        Uri downloadUri = Uri.parse(object.getString("downloadUrl").trim());
        String sha256 = object.getString("sha256").trim().toLowerCase(Locale.ROOT);
        if (versionCode < 1 || versionName.isEmpty()) throw new JSONException("Invalid version");
        if (!NavigationPolicy.isHttps(downloadUri)) throw new JSONException("Update download must use HTTPS");
        if (!sha256.matches("[0-9a-f]{64}")) throw new JSONException("Invalid SHA-256");
        return new UpdateManifest(versionCode, versionName, releaseNotes, downloadUri, sha256);
    }
}
