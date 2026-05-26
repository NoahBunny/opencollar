package android.util;

/**
 * JVM test-support shim for android.util.Log — no-op sink.
 *
 * android.jar's Log is a stub that throws on a plain JVM. The crypto classes
 * call Log.w/Log.e on failure paths; in tests we just swallow them. Test
 * classpath only — never bundled in an APK.
 */
public final class Log {
    private Log() {}

    public static int d(String tag, String msg) {
        return 0;
    }

    public static int d(String tag, String msg, Throwable tr) {
        return 0;
    }

    public static int i(String tag, String msg) {
        return 0;
    }

    public static int w(String tag, String msg) {
        return 0;
    }

    public static int w(String tag, String msg, Throwable tr) {
        return 0;
    }

    public static int e(String tag, String msg) {
        return 0;
    }

    public static int e(String tag, String msg, Throwable tr) {
        return 0;
    }
}
