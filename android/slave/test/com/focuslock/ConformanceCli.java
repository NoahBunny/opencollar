package com.focuslock;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import org.json.JSONObject;

/**
 * JVM conformance CLI for the Collar's VaultCrypto. Lets tests/test_android_conformance.py
 * prove the Collar's /mesh/sync order verification accepts Python-signed orders
 * (and rejects forgeries). Test classpath only — never bundled in an APK.
 *
 * Contract (subcommand on argv, input on stdin, result on stdout):
 *   canonical                       stdin=JSON object        -> canonical_json string
 *   verify-orders <sigB64> <pubB64> stdin=orders JSON        -> "ok" | "fail"
 *   sign-status <privKeyB64>        stdin=status-core JSON    -> base64 signature
 *
 * verify-orders mirrors ControlService.verifyMeshOrdersSignature exactly:
 * re-attach the wire signature as a field, then VaultCrypto.verifySignature
 * canonicalizes (map minus "signature") and verifies — the same input the relay
 * signed via OrdersDocument.sign_orders / canonical_json(orders).
 *
 * sign-status mirrors ControlService.handleMeshStatus's bunny signature over the
 * flat status core (VaultCrypto.signBlob = canonical_json(core minus signature)),
 * so Lion's Share verifyStatusSignature can reject a spoofed direct /mesh/status.
 */
public final class ConformanceCli {
    public static void main(String[] args) throws Exception {
        String cmd = args.length > 0 ? args[0] : "";
        String stdin = readAll(System.in);
        switch (cmd) {
            case "canonical": {
                byte[] out = VaultCrypto.canonicalJson(VaultCrypto.jsonToMap(new JSONObject(stdin)));
                System.out.write(out);
                System.out.flush();
                break;
            }
            case "verify-orders": {
                if (args.length < 3) {
                    System.err.println("verify-orders requires <sigB64> <pubB64>");
                    System.exit(2);
                }
                Map<String, Object> orders = new HashMap<>(VaultCrypto.jsonToMap(new JSONObject(stdin)));
                orders.put("signature", args[1]);
                System.out.print(VaultCrypto.verifySignature(orders, args[2]) ? "ok" : "fail");
                System.out.flush();
                break;
            }
            case "sign-status": {
                if (args.length < 2) {
                    System.err.println("sign-status requires <privKeyB64>");
                    System.exit(2);
                }
                Map<String, Object> core = VaultCrypto.jsonToMap(new JSONObject(stdin));
                System.out.print(VaultCrypto.signBlob(core, args[1]));
                System.out.flush();
                break;
            }
            default:
                System.err.println("unknown cmd: " + cmd);
                System.exit(2);
        }
    }

    private static String readAll(InputStream in) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = in.read(buf)) != -1) {
            b.write(buf, 0, n);
        }
        return new String(b.toByteArray(), StandardCharsets.UTF_8);
    }
}
