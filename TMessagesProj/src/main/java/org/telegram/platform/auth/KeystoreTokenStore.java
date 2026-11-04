package org.telegram.platform.auth;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.Charset;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * Stores the token pair encrypted with an AES-256-GCM key held by the Android
 * Keystore. The key never leaves secure hardware/keystore, so a filesystem dump of
 * the app sandbox does not expose refresh tokens.
 */
public final class KeystoreTokenStore implements TokenStore {

    private static final String KEYSTORE = "AndroidKeyStore";
    private static final String KEY_ALIAS = "org.telegram.platform.tokens";
    private static final String PREFS = "platform_tokens";
    private static final String PREF_ACCESS = "access";
    private static final String PREF_REFRESH = "refresh";
    private static final String TRANSFORMATION = "AES/GCM/NoPadding";
    private static final int GCM_TAG_BITS = 128;
    private static final Charset UTF8 = Charset.forName("UTF-8");

    private final SharedPreferences preferences;
    private volatile String accessCache;
    private volatile String refreshCache;

    public KeystoreTokenStore(Context context) {
        this.preferences = context.getApplicationContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    @Override
    public synchronized String accessToken() {
        if (accessCache == null) {
            accessCache = read(PREF_ACCESS);
        }
        return accessCache;
    }

    @Override
    public synchronized String refreshToken() {
        if (refreshCache == null) {
            refreshCache = read(PREF_REFRESH);
        }
        return refreshCache;
    }

    @Override
    public synchronized void save(String accessToken, String refreshToken) {
        accessCache = accessToken;
        refreshCache = refreshToken;
        SharedPreferences.Editor editor = preferences.edit();
        editor.putString(PREF_ACCESS, encrypt(accessToken));
        editor.putString(PREF_REFRESH, encrypt(refreshToken));
        editor.apply();
    }

    @Override
    public synchronized void clear() {
        accessCache = null;
        refreshCache = null;
        preferences.edit().remove(PREF_ACCESS).remove(PREF_REFRESH).apply();
    }

    private String read(String key) {
        String stored = preferences.getString(key, null);
        if (stored == null) {
            return null;
        }
        try {
            return decrypt(stored);
        } catch (Exception e) {
            // Key invalidated (e.g. device credentials reset) — drop the value.
            preferences.edit().remove(key).apply();
            return null;
        }
    }

    private static SecretKey key() throws Exception {
        KeyStore keyStore = KeyStore.getInstance(KEYSTORE);
        keyStore.load(null);
        if (keyStore.containsAlias(KEY_ALIAS)) {
            return ((KeyStore.SecretKeyEntry) keyStore.getEntry(KEY_ALIAS, null)).getSecretKey();
        }
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE);
        generator.init(new KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build());
        return generator.generateKey();
    }

    private static String encrypt(String plaintext) {
        if (plaintext == null) {
            return null;
        }
        try {
            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.ENCRYPT_MODE, key());
            byte[] iv = cipher.getIV();
            byte[] encrypted = cipher.doFinal(plaintext.getBytes(UTF8));
            byte[] combined = new byte[iv.length + encrypted.length];
            System.arraycopy(iv, 0, combined, 0, iv.length);
            System.arraycopy(encrypted, 0, combined, iv.length, encrypted.length);
            return Base64.encodeToString(combined, Base64.NO_WRAP);
        } catch (Exception e) {
            throw new IllegalStateException("Unable to encrypt token", e);
        }
    }

    private static String decrypt(String encoded) throws Exception {
        byte[] combined = Base64.decode(encoded, Base64.NO_WRAP);
        int ivLength = 12; // GCM default IV length used by Android Keystore
        byte[] iv = new byte[ivLength];
        byte[] payload = new byte[combined.length - ivLength];
        System.arraycopy(combined, 0, iv, 0, ivLength);
        System.arraycopy(combined, ivLength, payload, 0, payload.length);
        Cipher cipher = Cipher.getInstance(TRANSFORMATION);
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(GCM_TAG_BITS, iv));
        return new String(cipher.doFinal(payload), UTF8);
    }
}
