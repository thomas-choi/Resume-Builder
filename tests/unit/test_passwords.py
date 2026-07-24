"""Tests for the password rule, hashing and verification (Phase 7.f)."""

import pytest

from src.utils import passwords


# --- rule -------------------------------------------------------------------


@pytest.mark.parametrize("special", ["_", "$", ",", "-"])
def test_rule_accepts_long_password_with_each_special(special):
    assert passwords.validate_password_rule(f"abcdefgh{special}") is None


def test_rule_rejects_eight_chars_even_with_special():
    # Exactly 8 is not "more than 8".
    assert passwords.validate_password_rule("abcdef_1") is not None


def test_rule_accepts_nine_chars_with_special():
    assert passwords.validate_password_rule("abcdefg_1") is None


def test_rule_rejects_long_password_without_special():
    assert passwords.validate_password_rule("abcdefghijk") is not None


def test_rule_rejects_special_not_in_set():
    # '!' and '@' are not in the allowed set _ $ , -
    assert passwords.validate_password_rule("abcdefgh!") is not None
    assert passwords.validate_password_rule("abcdefgh@") is not None


# --- hashing ----------------------------------------------------------------


def test_hash_and_verify_roundtrip():
    hashed = passwords.hash_password("s3cret_pw")
    assert hashed != "s3cret_pw"  # never the raw password
    assert passwords.verify_password("s3cret_pw", hashed) is True
    assert passwords.verify_password("wrong_pw", hashed) is False


def test_verify_tolerates_empty_hash():
    assert passwords.verify_password("anything_", "") is False
    assert passwords.verify_password("anything_", "not-a-bcrypt-hash") is False


def test_hashes_are_salted():
    assert passwords.hash_password("same_pw12") != passwords.hash_password("same_pw12")
