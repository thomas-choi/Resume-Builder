/**
 * The password rule, mirrored from the server (src/utils/passwords.py).
 *
 * The server is the authority; this is only the fast, offline UX check the
 * sign-up and change-password screens run before posting. Keep the two in step.
 */

/** The special characters a valid password must contain at least one of. */
export const PASSWORD_SPECIALS = "_$,-";
/** Human-readable description of the rule, shown as helper text. */
export const PASSWORD_RULE_TEXT =
  "More than 8 characters, and at least one of _ $ , -";

/** Returns an error string if the password breaks the rule, else null. */
export function validatePassword(password: string): string | null {
  if (password.length < 9) {
    return "Password must be more than 8 characters long.";
  }
  if (![...password].some((ch) => PASSWORD_SPECIALS.includes(ch))) {
    return "Password must contain at least one special character: _ $ , -";
  }
  return null;
}
