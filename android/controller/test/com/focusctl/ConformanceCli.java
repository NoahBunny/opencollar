package com.focusctl;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import org.json.JSONObject;

/**
 * JVM conformance CLI for the controller's VaultCrypto. Lets tests/test_android_conformance.py
 * drive the REAL Java implementation and compare it to the Python reference
 * byte-for-byte. Test classpath only — never bundled in an APK.
 *
 * Contract (subcommand on argv, input on stdin, result on stdout):
 *   canonical                 stdin=JSON object  -> canonical_json string
 *   sign-string <privKeyB64>  stdin=payload      -> base64 RSA-SHA256 signature
 *   status-core               stdin=/mesh/status body -> canonical_json of the
 *                                                  rebuilt status core
 *
 * status-core exposes StatusCore.fromWire — the rebuild MainActivity feeds to
 * VaultCrypto.verifySignature — so the Python side can assert it picks the
 * signed TOP-LEVEL fields and never the same-named copies inside the embedded
 * orders document. The emitted canonical bytes are literally the signing input,
 * so any drift from the Collar's signer shows up as a byte diff, not as a
 * mysterious rejected status on a device.
 *
 * Run as: java -cp <classes>:<json.jar> com.focusctl.ConformanceCli <cmd> [args]
 */
public final class ConformanceCli {
    public static void main(String[] args) throws Exception {
        String cmd = args.length > 0 ? args[0] : "";
        String stdin = readAll(System.in);
        switch (cmd) {
            case "canonical": {
                // jsonToMap recursively converts JSONObject/JSONArray into the
                // plain Map/List tree canonicalJson serializes.
                byte[] out = VaultCrypto.canonicalJson(VaultCrypto.jsonToMap(new JSONObject(stdin)));
                System.out.write(out);
                System.out.flush();
                break;
            }
            case "sign-string": {
                if (args.length < 2) {
                    System.err.println("sign-string requires <privKeyB64> arg");
                    System.exit(2);
                }
                System.out.print(VaultCrypto.signString(stdin, args[1]));
                System.out.flush();
                break;
            }
            case "status-core": {
                // Emit the SIGNING INPUT: the rebuilt core minus "signature",
                // canonicalized — byte-identical to what the Collar signed if
                // the two sides agree on fields, types and scoping.
                java.util.TreeMap<String, Object> core = StatusCore.fromWire(stdin);
                core.remove("signature");
                System.out.write(VaultCrypto.canonicalJson(core));
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
