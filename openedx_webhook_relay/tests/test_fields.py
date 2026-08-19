"""Unit tests for EncryptedCharField."""

# pylint: disable=missing-function-docstring,redefined-outer-name

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings

from openedx_webhook_relay.fields import (
    EncryptedCharField,
    SecretConfigurationError,
    mask_secret,
)


@pytest.fixture
def field():
    return EncryptedCharField(max_length=1024)


def test_round_trip_encrypts_and_decrypts(field):
    plaintext = "super-secret-value"
    ciphertext = field.get_prep_value(plaintext)

    assert ciphertext != plaintext
    assert ciphertext.startswith("enc::")

    decrypted = field.from_db_value(ciphertext, None, None)
    assert decrypted == plaintext


def test_blank_value_passes_through(field):
    assert field.get_prep_value("") == ""
    assert field.from_db_value("", None, None) == ""
    assert field.from_db_value(None, None, None) is None


def test_get_prep_value_is_idempotent_for_already_encrypted(field):
    plaintext = "another-secret"
    once = field.get_prep_value(plaintext)
    twice = field.get_prep_value(once)
    assert once == twice


def test_from_db_value_warns_on_legacy_plaintext(field, caplog):
    value = field.from_db_value("legacy-plaintext-secret", None, None)
    assert value == "legacy-plaintext-secret"
    assert "plaintext value" in caplog.text


def test_from_db_value_returns_empty_on_wrong_key(field):
    plaintext = "secret-under-key-a"
    ciphertext = field.get_prep_value(plaintext)

    other_key = Fernet.generate_key().decode("utf-8")
    with override_settings(OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY=other_key):
        result = field.from_db_value(ciphertext, None, None)
    assert result == ""


def test_missing_encryption_key_raises(field):
    with (
        override_settings(OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY=None),
        pytest.raises(SecretConfigurationError),
    ):
        field.get_prep_value("value")


def test_invalid_encryption_key_raises(field):
    with (
        override_settings(OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY="not-a-valid-fernet-key"),
        pytest.raises(SecretConfigurationError),
    ):
        field.get_prep_value("value")


def test_mask_secret():
    assert mask_secret("") == ""
    # Secrets of 4 chars or fewer reveal only the last character: showing the
    # last 4 of a 4-char secret would disclose the whole thing.
    assert mask_secret("abcd") == f"{'•' * 8}d"
    assert mask_secret("a-very-long-secret").endswith("cret")
    assert mask_secret("a-very-long-secret").startswith("•" * 8)
