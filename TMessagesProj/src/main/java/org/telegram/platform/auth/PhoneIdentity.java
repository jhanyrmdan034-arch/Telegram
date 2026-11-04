package org.telegram.platform.auth;

/**
 * The login screens keep using phone numbers, exactly as before. The backend
 * identifies accounts by username, so the phone number is mapped to a stable,
 * collision-free username: every digit is kept, prefixed with {@code ph}.
 *
 * <pre>
 *   "+49 151 1234 5678"  ->  digits "4915112345678"  ->  username "ph4915112345678"
 * </pre>
 *
 * The mapping is deterministic, so the same phone number always resolves to the
 * same account, on every device, without an extra lookup table.
 */
public final class PhoneIdentity {

    /** E.164 allows at most 15 digits; fewer than 5 cannot be a real number. */
    private static final int MIN_DIGITS = 5;
    private static final int MAX_DIGITS = 15;
    private static final String PREFIX = "ph";

    private PhoneIdentity() {
    }

    /** Strips formatting; returns {@code null} when the number cannot be valid. */
    public static String digits(String raw) {
        if (raw == null) {
            return null;
        }
        StringBuilder builder = new StringBuilder(raw.length());
        for (int i = 0; i < raw.length(); i++) {
            char c = raw.charAt(i);
            if (c >= '0' && c <= '9') {
                builder.append(c);
            }
        }
        String digits = builder.toString();
        if (digits.length() < MIN_DIGITS || digits.length() > MAX_DIGITS) {
            return null;
        }
        return digits;
    }

    /** Backend username for a phone number, or {@code null} if the number is unusable. */
    public static String usernameFor(String raw) {
        String digits = digits(raw);
        return digits == null ? null : PREFIX + digits;
    }

    /** Human readable form used when a TL_user has to be synthesised. */
    public static String e164(String raw) {
        String digits = digits(raw);
        return digits == null ? raw : "+" + digits;
    }

    /**
     * Opaque value returned to the login UI as {@code phone_code_hash}. The UI only
     * echoes it back, so it doubles as a check that the code was requested for this
     * very number.
     */
    public static String codeHash(String raw) {
        String digits = digits(raw);
        return digits == null ? "invalid" : "platform:" + digits;
    }

    public static boolean matchesCodeHash(String raw, String codeHash) {
        return codeHash != null && codeHash.equals(codeHash(raw));
    }
}
