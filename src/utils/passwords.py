"""Password rule, hashing and verification (design doc §14.4, Phase 7.f).

Passwords replace the passwordless email code/link flow: the password is the
credential presented at sign-in, and the account is usable the moment sign-up
succeeds. Only a bcrypt hash is ever persisted (in the ``User`` record) — the
raw password never touches disk, mirroring the "no raw secret on disk" rule the
challenge store already followed.

The rule is deliberately small and enforced **server-side** (the authority); the
sign-up screen mirrors it for a fast, offline UX check, but this module is what
actually gates account creation and password changes.
"""

import bcrypt

# The special characters a valid password must contain at least one of.
PASSWORD_SPECIALS = "_$,-"
# Minimum length is "> 8", i.e. 9 or more characters.
PASSWORD_MIN_LENGTH = 9


def validate_password_rule(password: str) -> str | None:
    """Check a candidate password against the rule (§14.4).

    Rule: **more than 8 characters** and **at least one** of ``_ $ , -``.

    Args:
        password: The raw candidate password.

    Returns:
        ``None`` if the password is acceptable, otherwise a short human-readable
        reason it was rejected (safe to show the user).
    """
    if len(password) < PASSWORD_MIN_LENGTH:
        return "Password must be more than 8 characters long."
    if not any(ch in PASSWORD_SPECIALS for ch in password):
        return "Password must contain at least one special character: _ $ , -"
    return None


def hash_password(password: str) -> str:
    """Hash a password with bcrypt, returning the encoded hash string to store."""
    # bcrypt caps the input at 72 bytes; that is far beyond any realistic
    # password, so truncation here is a non-issue and we keep the call simple.
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Whether ``password`` matches the stored bcrypt ``password_hash``.

    Tolerates a malformed/empty stored hash (a legacy account with no password)
    by returning ``False`` rather than raising, so sign-in fails cleanly.
    """
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False
