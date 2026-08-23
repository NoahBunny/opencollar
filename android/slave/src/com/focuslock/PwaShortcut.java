package com.focuslock;

/**
 * Decisions about pinned shortcuts — chiefly the PWAs Chrome asks to install.
 *
 * <p><b>Why the Collar is involved at all.</b> It registers FocusActivity for
 * {@code CATEGORY_HOME} so the home button lands in the cage during a lock;
 * when nothing is locked it trampolines straight to the wearer's real launcher
 * ({@code focus_lock_prior_home_pkg}). That makes it a home *interceptor*, not a
 * launcher — but Android does not know the difference. It asks the DEFAULT home
 * app whether pinning is supported, and a Collar that answers nothing makes
 * {@code ShortcutManager.isRequestPinShortcutSupported()} false for the whole
 * device. Chrome then cannot finish "Add to Home screen", so the wearer cannot
 * install a PWA at all — a restriction nobody chose, imposed by a launcher that
 * is not really acting as one.
 *
 * <p>The rules are separated from the Activity so they can be tested off a
 * device, the same shape as {@link SigVerifier} and {@link MeshOrderApply}.
 */
final class PwaShortcut {

    private PwaShortcut() {}

    /** Package prefixes Chrome mints a real installed PWA under. */
    private static final String[] WEBAPK_PREFIXES = {
        "org.chromium.webapk.",
        "com.google.android.webapk.",
    };

    /**
     * True when the package is a Chrome-minted WebAPK — a PWA installed as its
     * own package.
     *
     * <p>Worth distinguishing from a plain shortcut because the blast radius
     * differs: a WebAPK is one site in one package, so allowing or bouncing it
     * affects exactly that site, whereas a shortcut just opens the browser and
     * anything permitting it permits the entire web.
     */
    static boolean isWebApk(String pkg) {
        if (pkg == null) return false;
        for (String p : WEBAPK_PREFIXES) {
            if (pkg.startsWith(p)) return true;
        }
        return false;
    }

    /**
     * Whether a pin request may be accepted.
     *
     * <p>Refused outright while a lock is active. Installing something to the
     * home screen mid-lock is a change to what the phone can do, made by the
     * person the lock exists to constrain, at the one moment they are supposed
     * to be constrained — and a wearer who can add a launcher tile during a
     * lock has an escape hatch that costs them nothing.
     *
     * <p>It is not a punishment: the request is refused, not counted as an
     * escape, and it works normally the moment the lock lifts.
     */
    static boolean mayAccept(boolean lockActive) {
        return !lockActive;
    }

    /** Human label for logs and the wearer-facing toast; never null or blank. */
    static String describe(String label, String pkg) {
        String name = label == null ? "" : label.trim();
        if (name.isEmpty()) name = isWebApk(pkg) ? "Web app" : "Shortcut";
        // Keep one line: this goes into a toast and a log line, and a
        // multi-line label from a hostile site should not reshape either.
        name = name.replace('\n', ' ').replace('\r', ' ');
        return name.length() > 64 ? name.substring(0, 64) + "…" : name;
    }
}
