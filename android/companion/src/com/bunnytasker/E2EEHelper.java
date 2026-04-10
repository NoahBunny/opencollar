package com.bunnytasker;

import android.util.Base64;
import java.security.KeyFactory;
import java.security.PublicKey;
import java.security.PrivateKey;
import java.security.spec.X509EncodedKeySpec;
import java.security.spec.PKCS8EncodedKeySpec;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * E2EE helper — hybrid RSA+AES-GCM encryption.
 * Encrypt: generate random AES-256 key, encrypt message with AES-GCM,
 *          encrypt AES key with recipient's RSA public key.
 * Decrypt: decrypt AES key with own RSA private key, decrypt message with AES-GCM.
 */
public class E2EEHelper {

    public static class EncryptedMessage {
        public String ciphertext;   // base64 AES-GCM encrypted message
        public String encryptedKey; // base64 RSA encrypted AES key
        public String iv;           // base64 AES-GCM IV
    }

    /**
     * Encrypt a message for the given recipient's RSA public key.
     */
    public static EncryptedMessage encrypt(String plaintext, String recipientPubKey) {
        try {
            // Parse public key (PEM or raw base64)
            String keyStr = recipientPubKey
                .replace("-----BEGIN PUBLIC KEY-----", "")
                .replace("-----END PUBLIC KEY-----", "")
                .replaceAll("[\\s|]+", "");
            byte[] keyBytes = Base64.decode(keyStr, Base64.DEFAULT);
            PublicKey pubKey = KeyFactory.getInstance("RSA")
                .generatePublic(new X509EncodedKeySpec(keyBytes));

            // Generate random AES-256 key
            KeyGenerator kg = KeyGenerator.getInstance("AES");
            kg.init(256);
            SecretKey aesKey = kg.generateKey();

            // Encrypt message with AES-GCM
            Cipher aesCipher = Cipher.getInstance("AES/GCM/NoPadding");
            aesCipher.init(Cipher.ENCRYPT_MODE, aesKey);
            byte[] iv = aesCipher.getIV();
            byte[] ciphertext = aesCipher.doFinal(plaintext.getBytes("UTF-8"));

            // Encrypt AES key with RSA
            Cipher rsaCipher = Cipher.getInstance("RSA/ECB/PKCS1Padding");
            rsaCipher.init(Cipher.ENCRYPT_MODE, pubKey);
            byte[] encryptedKey = rsaCipher.doFinal(aesKey.getEncoded());

            EncryptedMessage result = new EncryptedMessage();
            result.ciphertext = Base64.encodeToString(ciphertext, Base64.NO_WRAP);
            result.encryptedKey = Base64.encodeToString(encryptedKey, Base64.NO_WRAP);
            result.iv = Base64.encodeToString(iv, Base64.NO_WRAP);
            return result;
        } catch (Exception e) {
            android.util.Log.e("E2EE", "Encrypt failed", e);
            return null;
        }
    }

    /**
     * Decrypt a message using own RSA private key.
     */
    public static String decrypt(String ciphertextB64, String encryptedKeyB64,
                                  String ivB64, String ownPrivKey) {
        try {
            // Parse private key (PEM or raw base64)
            String keyStr = ownPrivKey
                .replace("-----BEGIN PRIVATE KEY-----", "")
                .replace("-----END PRIVATE KEY-----", "")
                .replace("-----BEGIN RSA PRIVATE KEY-----", "")
                .replace("-----END RSA PRIVATE KEY-----", "")
                .replaceAll("[\\s|]+", "");
            byte[] keyBytes = Base64.decode(keyStr, Base64.DEFAULT);
            PrivateKey privKey = KeyFactory.getInstance("RSA")
                .generatePrivate(new PKCS8EncodedKeySpec(keyBytes));

            // Decrypt AES key with RSA
            Cipher rsaCipher = Cipher.getInstance("RSA/ECB/PKCS1Padding");
            rsaCipher.init(Cipher.DECRYPT_MODE, privKey);
            byte[] aesKeyBytes = rsaCipher.doFinal(Base64.decode(encryptedKeyB64, Base64.DEFAULT));
            SecretKey aesKey = new SecretKeySpec(aesKeyBytes, "AES");

            // Decrypt message with AES-GCM
            byte[] iv = Base64.decode(ivB64, Base64.DEFAULT);
            byte[] ciphertext = Base64.decode(ciphertextB64, Base64.DEFAULT);
            Cipher aesCipher = Cipher.getInstance("AES/GCM/NoPadding");
            aesCipher.init(Cipher.DECRYPT_MODE, aesKey, new GCMParameterSpec(128, iv));
            byte[] plaintext = aesCipher.doFinal(ciphertext);
            return new String(plaintext, "UTF-8");
        } catch (Exception e) {
            android.util.Log.e("E2EE", "Decrypt failed", e);
            return null;
        }
    }

    /**
     * Check if we have the keys needed for E2EE.
     */
    public static boolean canEncrypt(String recipientPubKey) {
        return recipientPubKey != null && !recipientPubKey.isEmpty()
            && !"null".equals(recipientPubKey) && recipientPubKey.length() > 20;
    }

    public static boolean canDecrypt(String ownPrivKey) {
        return ownPrivKey != null && !ownPrivKey.isEmpty()
            && !"null".equals(ownPrivKey) && ownPrivKey.length() > 20;
    }
}
