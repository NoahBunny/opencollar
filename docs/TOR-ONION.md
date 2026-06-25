# A3 — Embedded Tor v3 onion service (cross-network, no account, battery-cheap)

Executable spec for the native Tor layer. The serverless-direct foundation
(A1: direct-first multi-address failover + relay fallback) already ships and
gives a working cross-network path via the shared relay. This layer adds the
**primary** cross-network path: the Collar hosts a client-authed v3 onion
service that Lion's Share dials like any other address — no account, no
port-forward, no public IP — kept battery-cheap by leaving Tor **cold** and
waking it with the existing zero-knowledge ntfy `{"v":N}` push.

> Status (2026-06-26): **code-complete and compiling.** OnionKeys (crypto),
> OnionControl (jtorctl helper), Collar + Lion `TorManager`, the `TorHook`
> reflection bridge, the pairing/SOCKS/ntfy-wake integration, and the build.sh +
> manifest plumbing are all written and build into both APKs against
> tor-android 0.4.9.9.1 (libtor.so for arm64-v8a + x86_64 + armeabi-v7a + x86,
> all Tor classes dexed, signed). The `.onion`/keyblob crypto is unit-verified
> against a Python reference (OnionKeysTest, 6/6). The default-off build is
> proven byte-clean (no libtor.so, no Tor classes; only the inert TorHook stub).
> Requires build-tools >= 36.0.0 (`FOCUSLOCK_BUILD_TOOLS=36.0.0`) — the AAR ships
> Java-24 bytecode. **REMAINING (C3, device/waydroid only):** runtime validation
> of the control port (ADD_ONION + ClientAuthV3 acceptance, ONION_CLIENT_AUTH_ADD,
> bootstrap latency, the foregroundServiceType question, client-auth rejection,
> end-to-end onion round-trip + relay fallback). Keep `FOCUSLOCK_TOR_AAR` unset
> in production until C3 passes.

## Why onion services (recap)

- Onion services make only **outbound** circuits → traverse CGNAT/mobile-data
  for free. No STUN/TURN, no relay needed for the data path.
- v3 **client authorization** keys the onion to Lion only (undiscoverable +
  unconnectable without Lion's x25519 key), layered under the existing
  RSA-signed-order app auth.
- The `.onion` is derived from an ed25519 pubkey **offline** — the Collar can
  generate it at pair time and return it immediately, before Tor ever runs.

## Dependency: Guardian `tor-android` AAR + jtorctl

Java-friendly (kmp-tor is Kotlin and would drag the Kotlin toolchain into the
raw `javac`/`d8` pipeline). Coordinates:

- `info.guardianproject:tor-android:0.4.9.x` (bundles upstream C-tor + `TorService`)
- `info.guardianproject:jtorctl` (control-port client)

Arti onion **hosting** is still "not for production" per the Tor Project — use C-tor.

## Build packaging (no-Gradle)

`android/{slave,controller}/build.sh` gain an **optional, default-off** Tor hook
(already scaffolded — see the `FOCUSLOCK_TOR_AAR` block). To enable:

```bash
export FOCUSLOCK_TOR_AAR=/path/to/tor-android-0.4.9.x.aar
export FOCUSLOCK_JTORCTL_JAR=/path/to/jtorctl.jar
bash android/slave/build.sh        # and android/controller/build.sh
```

The hook unzips the AAR and:
1. puts `classes.jar` (+ jtorctl jar) on the `javac -classpath` and into the
   `d8` inputs, and
2. packages `jni/<abi>/libtor.so` into the APK under `lib/<abi>/` before
   `zipalign`/`apksigner`.

Ship `arm64-v8a` (real phones) **and** `x86_64` (waydroid QA). When
`FOCUSLOCK_TOR_AAR` is unset the build is byte-identical to today and the apps
fall back to LAN/relay (A1) — Tor is strictly additive.

## Collar (`com.focuslock`) — onion host

New `TorManager.java`:

- **Key at pair time:** generate the v3 onion ed25519 keypair locally; derive
  the `.onion` (base32(pubkey ‖ checksum ‖ version)) **without** running Tor;
  persist the key in app-private storage; write the address to
  `Settings.Global` key `focus_lock_onion_addr` (already advertised by
  `selfAddressFields()` in the `/api/pair` response and `/mesh/status`).
- **v3 client auth:** at pairing, Lion sends its x25519 **public** auth key in
  the `/api/pair` request (new field `onion_auth_pub`); store it under
  `<HiddenServiceDir>/authorized_clients/lion.auth`. Only Lion can connect.
- **torrc / control:** bind `TorService`, then via jtorctl (`ADD_ONION` with the
  persisted key, or `HiddenServiceDir`/`HiddenServicePort 8432 127.0.0.1:8432`)
  forward the onion to the existing HTTP server (already on `0.0.0.0:8432`).
- **Cold by default + ntfy-wake:** keep Tor stopped. Extend `ntfySubscribeLoop`
  (already calls `vaultSync()` on wake at ControlService ~4654): on wake, start
  Tor → publish the onion → keep it up for an N-minute **session window** →
  stop. Reuse the foreground-service + 5-min AlarmManager keep-alive.
- **Warm toggle:** `focus_lock_tor_warm` (Settings.Global) keeps the onion
  published 24/7 for instant reachability (~1–3%/hr) vs cold (~30s–2min wake).
- **Battery:** `ReducedConnectionPadding 1` + `ReducedCircuitPadding 1`,
  persistent `DataDirectory`.

## Lion's Share (`com.focusctl`) — onion client

- New `TorManager.java` in **client** mode (SOCKS `127.0.0.1:9050`); add Lion's
  x25519 auth **private** key to `ClientOnionAuthDir`.
- Generate Lion's x25519 auth keypair at pair time; send the pubkey in the
  `/api/pair` body (`onion_auth_pub`). (`pairDirect()` already POSTs
  `{lion_pubkey}` — add the field there.)
- **SOCKS routing:** in `meshPost(...)`/`directGet(...)`, when the target host
  ends in `.onion`, open the `HttpURLConnection` through a `Proxy(SOCKS,
  127.0.0.1:9050)`. The candidate list already carries the `.onion` URL
  (`candidatesFromAdvertisement` / `bunny_direct_urls`), ordered **after** LAN,
  **before** the relay.
- **Cold-wake:** implement `maybeWakeBunny()` (currently a no-op) to fire the
  ntfy bump **only when** LAN candidates have failed and an `.onion` candidate
  is about to be tried — POST `{"v":<ts>}` to `https://ntfy.sh/focuslock-<meshId>`
  (the topic the Collar subscribes to), then give Tor a brief window. Do NOT
  fire it on every action (adds latency on LAN). Show "waking Collar…" + retry;
  on timeout fall through to the relay (A1).
- **Timeouts:** onion connects are slow — give `.onion` candidates a longer
  per-probe timeout (~15s) than LAN (~2–3s). Add a timeout-aware overload of
  `meshPost`/`directGet`.

## Pairing handshake additions

`/api/pair` request (Lion→Collar): add `onion_auth_pub` (Lion's x25519 pubkey).
`/api/pair` response (Collar→Lion): already includes `onion` via
`selfAddressFields()` (empty until the key is provisioned — populate it in
`TorManager` key-gen). The `.onion` rides the HTTP response, **never the QR**
(QR is capped ~130 chars / v6).

## Verification (at C3, on devices/waydroid)

1. Build both apps with `FOCUSLOCK_TOR_AAR` set; confirm `lib/<abi>/libtor.so`
   is in the APKs.
2. Pair on LAN; confirm `focus_lock_onion_addr` is populated and returned in the
   pair response; confirm Lion stored the `.onion` candidate.
3. Disable LAN + relay; confirm ntfy wake → Collar Tor bootstrap → Lion connects
   to `bunny.onion`; lock/unlock round-trips.
4. Confirm client-auth: a second device without Lion's key cannot connect.
5. Measure cold-start wake latency; toggle warm mode; confirm relay auto-fallback
   when Tor can't bootstrap (no silent failure).

## Risk register

- Native lib load / ABI match (ship x86_64 for waydroid).
- Control-port protocol + torrc correctness — compile against the real API.
- Descriptor-publish latency on cold start (~30s–2min) — UI must show progress
  and fall back to relay.
- Don't regress the signed-once-retry replay protection (reuse one signed body
  across LAN/onion candidates — already implemented in `postDirectWithFailover`).

---

# Code-ready recipe (research-grounded, 2026-06-25)

Verified against tor-android 0.4.9.x, jtorctl 0.4.5.7, control-spec, rend-spec-v3.
AAR/jar were downloaded during research — re-fetch on a dev box (Maven Central or
`https://raw.githubusercontent.com/guardianproject/gpmaven/master/info/guardianproject`):
`tor-android/0.4.9.9.1/tor-android-0.4.9.9.1.aar`, `jtorctl/0.4.5.7/jtorctl-0.4.5.7.jar`.
Need bcprov too (raw Ed25519/X25519 scalars; JCA XDH/Ed25519 don't expose them cleanly).
Set `FOCUSLOCK_TOR_AAR`, `FOCUSLOCK_JTORCTL_JAR`, `FOCUSLOCK_BCPROV_JAR`.

## TorService API (org.torproject.jni.TorService — declare in BOTH manifests)
`<service android:name="org.torproject.jni.TorService" android:exported="false"/>`
- start: `startService(new Intent(ctx, TorService.class).setAction(TorService.ACTION_START))`
- bind → `((TorService.LocalBinder)binder).getService()`; watch `ACTION_STATUS` extra `EXTRA_STATUS` ∈ {STARTING,ON,STOPPING,OFF}
- `getTorControlConnection()` → an ALREADY-AUTHENTICATED jtorctl `TorControlConnection`
- `getSocksPort()` (9050), `getTorrc(ctx)` (append torrc BEFORE ACTION_START)

## Collar (com.focuslock) — host client-authed v3 onion
**Offline .onion at pair time (no Tor):** seed=32 rand bytes (persist app-private).
`Ed25519PrivateKeyParameters sk=new Ed25519PrivateKeyParameters(seed,0); pub32=sk.generatePublicKey().getEncoded();`
`.onion = base32_lower_nopad(pub32 || SHA3_256(".onion checksum"||pub32||0x03)[0:2] || 0x03)+".onion"`.
Write to Settings.Global `focus_lock_onion_addr` (already advertised by selfAddressFields()).
**ADD_ONION keyblob (NOT the seed):** h=SHA512(seed); a=h[0:32]; a[0]&=248;a[31]&=127;a[31]|=64; blob=base64(a||h[32:64]).
**Publish (on ntfy-wake/warm):** `ADD_ONION ED25519-V3:<blob> Flags=Detach Port=8432,127.0.0.1:8432 ClientAuthV3=<onion_auth_pub_base32>`
- ClientAuthV3 = base32(raw 32-byte Lion x25519 pubkey). Without it the onion is unconnectable.
- Lion POSTs `onion_auth_pub` (base32) at /api/pair; store it. Assert returned ServiceID == offline .onion.
- Multiple Lion devices → multiple ClientAuthV3= clauses. Teardown: DEL_ONION <ServiceID>.

## jtorctl helper — new file src/net/freehaven/tor/control/OnionControl.java (BOTH apps)
jtorctl's addOnion() can't do ClientAuthV3 and sendAndWaitForResponse is protected; same-package helper:
```java
package net.freehaven.tor.control;
import java.io.IOException;
public final class OnionControl {
    public static String addOnion(TorControlConnection c, String cmd) throws IOException {
        String id=null;
        for (TorControlConnection.ReplyLine l : c.sendAndWaitForResponse(cmd+"\r\n", null)) {
            if (!l.status.startsWith("25")) throw new IOException(l.status+" "+l.msg);
            if (l.msg.startsWith("ServiceID=")) id=l.msg.substring(10);
        }
        return id;
    }
    public static void raw(TorControlConnection c, String cmd) throws IOException {
        for (TorControlConnection.ReplyLine l : c.sendAndWaitForResponse(cmd+"\r\n", null))
            if (!l.status.startsWith("25")) throw new IOException(l.status+" "+l.msg);
    }
}
```
(Verify ReplyLine field names `status`/`msg` compile against the pinned jar.)

## v3 client-auth keys (THE #1 footgun — encodings differ)
x25519 via bcprov: `X25519PrivateKeyParameters cpriv=new X25519PrivateKeyParameters(rng); priv32=cpriv.getEncoded(); pub32=cpriv.generatePublicKey().getEncoded();`
- Collar ADD_ONION `ClientAuthV3=<pub>`: **base32** (no pad)
- Lion control port `ONION_CLIENT_AUTH_ADD <onion-no-suffix> x25519:<priv>`: **base64** (std)  ← NOT base32
- File forms (.auth / .auth_private): base32. Mixing = 512/auth failure.

## Lion (com.focusctl) — client
After Tor ON: `OnionControl.raw(conn, "ONION_CLIENT_AUTH_ADD "+onionNoSuffix+" x25519:"+base64(priv32))` (session-scoped; re-add each start; +Flags=Permanent to persist).
SOCKS route: `Proxy p=new Proxy(SOCKS,new InetSocketAddress("127.0.0.1",getSocksPort())); (HttpURLConnection)new URL("http://"+onion+":8432/..").openConnection(p);` connect/read timeout ~15s.
**.onion resolves correctly** ONLY via HttpURLConnection (OkHttp-backed → unresolved addr → SOCKS5h remote resolve). Do NOT hand-roll java.net.Socket+SOCKS (SOCKS4-only, throws on .onion). Give onion candidates ~15s vs ~2-3s LAN.
maybeWakeBunny(): fire `POST {"v":<ts>}` to `https://ntfy.sh/focuslock-<meshId>` ONLY when LAN failed + onion is next; then retry window; fall to relay on timeout. Not every action.

## torrc (both, via getTorrc): `ReducedConnectionPadding 1` / `ReducedCircuitPadding 1` / persistent `DataDirectory`.
Cold+ntfy-wake: ntfy → ntfySubscribeLoop (~ControlService:4654) → ACTION_START → await ON → addOnion → N-min session window (reuse FG service + 5-min AlarmManager) → DEL_ONION+ACTION_STOP. `focus_lock_tor_warm`=skip teardown (24/7, ~1-3%/hr). Budget cold reachability ~30s-2min (descriptor publish); UI "waking Collar…" + relay fallback, never silent.

## build.sh edits (both): the FOCUSLOCK_TOR_AAR hook exists. Add:
- bcprov to dep block: `if [ -n "${FOCUSLOCK_BCPROV_JAR:-}" ]; then TOR_CP="$TOR_CP:$FOCUSLOCK_BCPROV_JAR"; TOR_DEX_INPUTS="$TOR_DEX_INPUTS $FOCUSLOCK_BCPROV_JAR"; fi`
- javac/d8 globs must include the new package: `javac ... -d classes $(find src -name '*.java')` and `d8 ... $(find classes -name '*.class') $TOR_DEX_INPUTS` (replace the fixed `src/com/<pkg>/*.java` glob).
