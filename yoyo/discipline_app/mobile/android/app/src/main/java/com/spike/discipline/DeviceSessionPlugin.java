package com.spike.discipline;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** AES-GCM per Android KeyGenParameterSpec; key never leaves AndroidKeyStore. */
@CapacitorPlugin(name = "DeviceSession")
public class DeviceSessionPlugin extends Plugin {
    private static final String ALIAS = "spike-device-session-v1";
    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (!store.containsAlias(ALIAS)) {
            KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setKeySize(256).build());
            generator.generateKey();
        }
        return (SecretKey) store.getKey(ALIAS, null);
    }
    @PluginMethod public synchronized void get(PluginCall call) {
        try {
            String saved = getContext().getSharedPreferences(ALIAS, Context.MODE_PRIVATE).getString("value", "");
            String value = "";
            if (!saved.isEmpty()) {
                String[] parts = saved.split(":", 2);
                Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
                cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)));
                value = new String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), StandardCharsets.UTF_8);
            }
            JSObject result = new JSObject(); result.put("value", value); call.resolve(result);
        } catch (Exception e) { call.reject("无法读取本机授权，请重新授权"); }
    }
    @PluginMethod public synchronized void set(PluginCall call) {
        try {
            String value = call.getString("value");
            if (value == null || value.getBytes(StandardCharsets.UTF_8).length > 4096) { call.reject("授权格式无效"); return; }
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key());
            String encrypted = Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP) + ":" + Base64.encodeToString(cipher.doFinal(value.getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP);
            if (!getContext().getSharedPreferences(ALIAS, Context.MODE_PRIVATE).edit().putString("value", encrypted).commit()) throw new Exception();
            call.resolve();
        } catch (Exception e) { call.reject("无法保存本机授权"); }
    }
    @PluginMethod public synchronized void clear(PluginCall call) {
        if (getContext().getSharedPreferences(ALIAS, Context.MODE_PRIVATE).edit().clear().commit()) call.resolve();
        else call.reject("无法清除本机授权");
    }
}
