package cn.xiaowo.app;

import static org.junit.Assert.*;
import android.content.Context;
import androidx.test.core.app.ApplicationProvider;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import java.security.KeyStore;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public class TokenVaultInstrumentationTest {
    private Context context;
    private TokenVault vault;
    private final String token = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
    @Before public void before() throws Exception {
        context = ApplicationProvider.getApplicationContext();
        vault = new TokenVault(context);
        vault.remove();
    }
    @After public void after() throws Exception { vault.remove(); }
    @Test public void tokenSurvivesNewVaultAndOnlyCiphertextIsWritten() throws Exception {
        vault.set(token);
        assertEquals(token, new TokenVault(context).get());
        String stored = context.getSharedPreferences("xiaowo_secure_token", Context.MODE_PRIVATE).getString("ciphertext", "");
        assertFalse(stored.isEmpty());
        assertFalse(stored.contains(token));
        vault.remove();
        assertNull(new TokenVault(context).get());
    }
    @Test public void tamperedCiphertextIsRemovedInsteadOfReturned() throws Exception {
        vault.set(token);
        context.getSharedPreferences("xiaowo_secure_token", Context.MODE_PRIVATE)
            .edit().putString("ciphertext", "v1.invalid.tampered").commit();
        assertNull(vault.get());
        assertTrue(context.getSharedPreferences("xiaowo_secure_token", Context.MODE_PRIVATE).getAll().isEmpty());
    }
    @Test public void missingKeystoreKeyInvalidatesStoredToken() throws Exception {
        vault.set(token);
        KeyStore keyStore = KeyStore.getInstance("AndroidKeyStore");
        keyStore.load(null);
        keyStore.deleteEntry("xiaowo.mobile.token.v1");
        assertNull(new TokenVault(context).get());
    }
    @Test public void invalidTokensNeverEnterPersistentStorage() throws Exception {
        assertThrows(IllegalArgumentException.class, () -> vault.set("not-a-server-token"));
        assertNull(vault.get());
    }
}
