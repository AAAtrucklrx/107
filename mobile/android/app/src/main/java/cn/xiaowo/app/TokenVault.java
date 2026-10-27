package cn.xiaowo.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Only the app bearer belongs here; school credentials never enter this store. */
final class TokenVault {
    private static final String ALIAS = "xiaowo.mobile.token.v1";
    private static final String CIPHERTEXT = "ciphertext";
    private static final byte[] AAD = "cn.xiaowo.app:bearer:v1".getBytes(StandardCharsets.UTF_8);
    private final SharedPreferences prefs;

    TokenVault(Context context) { prefs = context.getSharedPreferences("xiaowo_secure_token", Context.MODE_PRIVATE); }

    synchronized void set(String value) throws Exception {
        if (value == null || !value.matches("[A-Za-z0-9_-]{43}")) throw new IllegalArgumentException("INVALID_TOKEN");
        try {
            KeyStore keyStore = keyStore();
            SecretKey key = (SecretKey) keyStore.getKey(ALIAS, null);
            if (key == null) {
                KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
                generator.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setKeySize(256).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setRandomizedEncryptionRequired(true).build());
                key = generator.generateKey();
            }
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key);
            cipher.updateAAD(AAD);
            byte[] encrypted = cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
            String record = "v1." + Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP) + "."
                + Base64.encodeToString(encrypted, Base64.NO_WRAP);
            if (!prefs.edit().putString(CIPHERTEXT, record).commit()) throw new IOException("TOKEN_WRITE_FAILED");
        } catch (Exception error) {
            remove();
            throw error;
        }
    }

    synchronized String get() throws Exception {
        try {
            String record = prefs.getString(CIPHERTEXT, null);
            if (record == null) return null;
            String[] pieces = record.split("\\.", -1);
            if (pieces.length != 3 || !"v1".equals(pieces[0])) throw new GeneralSecurityException("INVALID_RECORD");
            SecretKey key = (SecretKey) keyStore().getKey(ALIAS, null);
            if (key == null) throw new GeneralSecurityException("KEY_MISSING");
            byte[] iv = Base64.decode(pieces[1], Base64.NO_WRAP);
            if (iv.length != 12) throw new GeneralSecurityException("INVALID_IV");
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(128, iv));
            cipher.updateAAD(AAD);
            String token = new String(cipher.doFinal(Base64.decode(pieces[2], Base64.NO_WRAP)), StandardCharsets.UTF_8);
            if (!token.matches("[A-Za-z0-9_-]{43}")) throw new GeneralSecurityException("INVALID_TOKEN");
            return token;
        } catch (Exception ignored) {
            // Restored ciphertext, corrupt records and invalidated keys all require a new login.
            remove();
            return null;
        }
    }

    synchronized void remove() throws Exception {
        boolean cleared = prefs.edit().clear().commit();
        KeyStore store = keyStore();
        if (store.containsAlias(ALIAS)) store.deleteEntry(ALIAS);
        if (!cleared) throw new IOException("TOKEN_CLEAR_FAILED");
    }

    private KeyStore keyStore() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        return store;
    }
}
