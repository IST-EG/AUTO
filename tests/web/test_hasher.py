"""
Unit tests for PasswordHasher abstraction and password complexity validation.
"""

import pytest
from app.web.security.hasher import BcryptPasswordHasher, validate_password_policy


def test_bcrypt_hasher_hash_and_verify():
    hasher = BcryptPasswordHasher(rounds=10)
    plain = "SuperSecret123!#"
    hashed = hasher.hash(plain)

    assert hashed != plain
    assert hashed.startswith("$2b$")
    assert hasher.verify(plain, hashed) is True
    assert hasher.verify("WrongPassword", hashed) is False


def test_bcrypt_hasher_invalid_inputs():
    hasher = BcryptPasswordHasher(rounds=10)
    assert hasher.verify("", "$2b$10$invalid") is False
    assert hasher.verify("test", "") is False
    assert hasher.verify("test", "not_a_hash") is False

    with pytest.raises(ValueError):
        hasher.hash("")


def test_bcrypt_hasher_needs_rehash():
    low_cost_hasher = BcryptPasswordHasher(rounds=6)
    high_cost_hasher = BcryptPasswordHasher(rounds=12)

    plain = "CompliantPass123!#"
    low_cost_hash = low_cost_hasher.hash(plain)

    # Low cost hash verified by high cost hasher should report needing rehash
    assert high_cost_hasher.needs_rehash(low_cost_hash) is True

    # Hash created with current cost does not need rehash
    high_cost_hash = high_cost_hasher.hash(plain)
    assert high_cost_hasher.needs_rehash(high_cost_hash) is False
    assert high_cost_hasher.needs_rehash("corrupted_hash") is True


def test_validate_password_policy():
    # Valid
    ok, msg = validate_password_policy("StrongPassword123!@")
    assert ok is True

    # Too short (< 12 chars)
    ok, msg = validate_password_policy("Short1!Aa")
    assert ok is False
    assert "at least 12" in msg

    # Missing uppercase
    ok, msg = validate_password_policy("nouppercase123!@#")
    assert ok is False
    assert "uppercase" in msg

    # Missing lowercase
    ok, msg = validate_password_policy("NOLOWERCASE123!@#")
    assert ok is False
    assert "lowercase" in msg

    # Missing number
    ok, msg = validate_password_policy("NoDigitsHere!@#$Aa")
    assert ok is False
    assert "number" in msg

    # Missing special symbol
    ok, msg = validate_password_policy("NoSpecialChar1234Aa")
    assert ok is False
    assert "special character" in msg
