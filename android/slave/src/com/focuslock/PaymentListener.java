package com.focuslock;

import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import android.provider.Settings;
import android.util.Log;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Monitors notifications for payments. Supports partial payments —
 * each payment reduces the paywall. When paywall hits 0, auto-unlock.
 *
 * Bank packages and payment keywords are configurable via Settings.Global:
 *   focus_lock_bank_packages  — semicolon-delimited package names
 *   focus_lock_payment_keywords — semicolon-delimited keywords (optional override)
 *
 * If not configured, uses built-in defaults covering major worldwide banks.
 */
public class PaymentListener extends NotificationListenerService {

    private static final String TAG = "FocusLock";

    // Multi-currency amount matching: $, €, £, ¥, ₹ + ISO currency codes
    private static final Pattern AMOUNT_PATTERN = Pattern.compile(
        "[$€£¥₹]\\s?(\\d+[.,]?\\d*)|" +                // Symbol-first: $50, €50.00
        "(\\d+[.,]\\d{2})\\s?[$€£¥₹]|" +                // Symbol-after: 50.00$, 50,00€
        "(\\d+[.,]\\d{2})\\s?(?:CAD|USD|EUR|GBP|AUD|NZD|SGD|INR|JPY|CHF|SEK|NOK|DKK|BRL|MXN|ZAR|HKD|KRW)"
    );

    // Default bank packages — worldwide coverage
    private static final String[] DEFAULT_BANK_PACKAGES = {
        // Canada
        "ca.tangerine.clients.banking.app", "com.td", "com.rbc.mobile.android",
        "com.bmo.mobile", "com.scotiabank.banking", "com.cibc.android.mobi",
        "ca.bnc.android", "com.desjardins.mobile", "com.pcfinancial.mobile",
        "com.eqbank.eqbank", "com.wealthsimple.trade",
        // USA
        "com.chase.sig.android", "com.infonow.bofa", "com.konylabs.capitalone",
        "com.wf.wellsfargomobile", "com.usaa.mobile.android.usaa",
        "com.ally.MobileBanking",
        // Global payment apps
        "com.paypal.android.p2pmobile", "com.venmo", "com.squareup.cash",
        "com.zellepay.zelle", "com.transferwise.android", "com.revolut.revolut",
        // UK
        "com.rbs.mobile.android.natwest", "uk.co.hsbc.hsbcukmobilebanking",
        "com.barclays.android.barclaysmobilebanking", "com.grppl.android.shell.BOS",
        // EU
        "de.number26.android", "com.starlingbank.android", "de.ingdiba.bankingapp",
        "com.commerzbank.photoTAN",
        // Australia
        "org.westpac.bank", "au.com.commbank.commbiz", "au.com.nab.mobile",
        "au.com.anz.android.gomoney",
        // Singapore
        "com.dbs.sg.dbsmbanking",
    };

    // Multi-language transfer keywords
    private static final String[] DEFAULT_TRANSFER_KEYWORDS = {
        // English
        "transfer", "e-transfer", "etransfer", "deposited", "received",
        "sent you money", "autodeposit", "direct deposit", "payment received",
        "paid you", "you've got money", "credited",
        // French
        "virement", "déposé", "reçu", "transfert", "paiement",
        // German
        "überweisung", "eingegangen", "gutgeschrieben", "erhalten",
        // Spanish
        "transferencia", "recibido", "depósito", "pago",
        // Portuguese
        "transferência", "depósito", "pagamento",
        // Italian
        "trasferimento", "ricevuto",
        // Provider-specific
        "interac", "zelle", "venmo", "paypal", "wise",
    };

    private String[] getBankPackages() {
        try {
            String custom = Settings.Global.getString(getContentResolver(), "focus_lock_bank_packages");
            if (custom != null && !custom.isEmpty()) {
                return custom.split(";");
            }
        } catch (Exception e) {}
        return DEFAULT_BANK_PACKAGES;
    }

    private String[] getTransferKeywords() {
        try {
            String custom = Settings.Global.getString(getContentResolver(), "focus_lock_payment_keywords");
            if (custom != null && !custom.isEmpty()) {
                return custom.split(";");
            }
        } catch (Exception e) {}
        return DEFAULT_TRANSFER_KEYWORDS;
    }

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        try {
            checkPayment(sbn);
        } catch (Exception e) {
            Log.e(TAG, "PaymentListener error", e);
        }
    }

    private void checkPayment(StatusBarNotification sbn) {
        int active = Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0);
        if (active != 1) return;

        String paywall = Settings.Global.getString(getContentResolver(), "focus_lock_paywall");
        if (paywall == null || paywall.isEmpty() || paywall.equals("0") || paywall.equals("null")) return;

        // Get notification content
        String pkg = sbn.getPackageName();
        android.app.Notification n = sbn.getNotification();
        if (n == null || n.extras == null) return;

        String title = n.extras.getString("android.title", "");
        String text = n.extras.getString("android.text", "");
        String bigText = n.extras.getString("android.bigText", "");
        String all = title + " " + text + " " + bigText;
        String allLower = all.toLowerCase();

        // Check if notification is from a known banking app
        String[] bankPackages = getBankPackages();
        boolean isBankingApp = false;
        for (String bankPkg : bankPackages) {
            if (pkg.equals(bankPkg.trim())) { isBankingApp = true; break; }
        }
        // Also match packages containing known payment provider names
        if (!isBankingApp) {
            isBankingApp = pkg.contains("interac") || pkg.contains("zelle")
                || pkg.contains("venmo") || pkg.contains("paypal")
                || pkg.contains("wise") || pkg.contains("revolut");
        }

        // For banking apps: require transfer-related keywords to avoid
        // false positives (e.g. credit card due, bill payment reminders)
        if (isBankingApp) {
            boolean hasTransferKeywords = false;
            String[] keywords = getTransferKeywords();
            for (String kw : keywords) {
                if (allLower.contains(kw.trim().toLowerCase())) {
                    hasTransferKeywords = true;
                    break;
                }
            }
            if (!hasTransferKeywords) isBankingApp = false;
        }

        // For email apps: require payment provider name AND transfer keywords
        boolean isBankingEmail = false;
        if (pkg.contains("mail") || pkg.contains("gmail") || pkg.contains("outlook")
                || pkg.contains("yahoo") || pkg.contains("proton")) {
            // Must mention a known payment provider
            boolean hasProvider = allLower.contains("interac") || allLower.contains("zelle")
                || allLower.contains("paypal") || allLower.contains("venmo")
                || allLower.contains("wise") || allLower.contains("transferwise")
                || allLower.contains("revolut");
            // AND a transfer keyword
            boolean hasKeyword = false;
            String[] keywords = getTransferKeywords();
            for (String kw : keywords) {
                if (allLower.contains(kw.trim().toLowerCase())) {
                    hasKeyword = true;
                    break;
                }
            }
            isBankingEmail = hasProvider && hasKeyword;
        }

        if (!isBankingApp && !isBankingEmail) return;

        // Extract amount from notification
        double foundAmount = extractAmount(all);
        if (foundAmount <= 0) {
            Log.d(TAG, "Payment notification but no amount found: " + allLower.substring(0, Math.min(200, allLower.length())));
            return;
        }

        double required;
        try {
            required = Double.parseDouble(paywall);
        } catch (Exception e) { return; }

        Log.i(TAG, "Payment detected: $" + foundAmount + " (required: $" + required + ") from " + pkg);

        if (foundAmount >= required) {
            // Full payment — unlock!
            Log.i(TAG, "FULL PAYMENT — unlocking!");
            Settings.Global.putInt(getContentResolver(), "focus_lock_active", 0);
            Settings.Global.putLong(getContentResolver(), "focus_lock_unlock_at", 0);
            Settings.Global.putString(getContentResolver(), "focus_lock_paywall", "0");
            Settings.Global.putString(getContentResolver(), "focus_lock_message", "Payment received. Good boy.");
        } else {
            // Partial payment — reduce the paywall
            double remaining = required - foundAmount;
            Log.i(TAG, "PARTIAL PAYMENT — $" + foundAmount + " received, $" + remaining + " remaining");
            Settings.Global.putString(getContentResolver(), "focus_lock_paywall",
                String.format("%.0f", remaining));
            Settings.Global.putString(getContentResolver(), "focus_lock_message",
                "Received $" + String.format("%.0f", foundAmount) + ". $" + String.format("%.0f", remaining) + " remaining.");
        }
    }

    private double extractAmount(String text) {
        Matcher m = AMOUNT_PATTERN.matcher(text);
        double maxAmount = 0;
        while (m.find()) {
            String raw = null;
            for (int i = 1; i <= m.groupCount(); i++) {
                if (m.group(i) != null) { raw = m.group(i); break; }
            }
            if (raw != null) {
                try {
                    raw = raw.replace(",", ".").trim();
                    double amount = Double.parseDouble(raw);
                    if (amount > maxAmount) maxAmount = amount;
                } catch (Exception e) {}
            }
        }
        return maxAmount;
    }
}
