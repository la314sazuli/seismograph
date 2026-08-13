from __future__ import annotations

import pytest

from seismograph.privacy import hash_author, redact


def test_hash_is_stable_for_the_same_secret():
    assert hash_author(123456789, "salt-of-sufficient-length") == hash_author(
        "123456789", "salt-of-sufficient-length"
    )


def test_different_secrets_produce_different_hashes():
    first = hash_author(123456789, "salt-one-of-sufficient-length")
    second = hash_author(123456789, "salt-two-of-sufficient-length")
    assert first != second


def test_different_authors_produce_different_hashes():
    salt = "salt-of-sufficient-length"
    assert hash_author(1, salt) != hash_author(2, salt)


def test_hash_does_not_reveal_the_author_id():
    digest = hash_author(987654321, "salt-of-sufficient-length")
    assert "987654321" not in digest
    assert len(digest) == 32
    assert all(character in "0123456789abcdef" for character in digest)


def test_empty_salt_is_refused():
    with pytest.raises(ValueError):
        hash_author(1, "")


@pytest.mark.parametrize(
    ("text", "secret", "placeholder"),
    [
        ("write to nora.example@example.invalid please", "nora.example@example.invalid", "[email]"),
        ("call +1 (555) 010-7788 today", "555", "[phone]"),
        ("token sk_live_4kQb92ZfTn10xYwPla leaked", "sk_live_4kQb92ZfTn10xYwPla", "[token]"),
        ("header Bearer abcdefgh12345678ijkl", "abcdefgh12345678ijkl", "[token]"),
        ("host at 203.0.113.42 responded", "203.0.113.42", "[ip]"),
        ("thanks <@!123456789012345678> for this", "123456789012345678", "[user]"),
        ("join discord.gg/abc123xyz now", "discord.gg/abc123xyz", "[invite]"),
        (
            "key aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5bC7d here",
            "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5bC7d",
            "[token]",
        ),
    ],
)
def test_sensitive_strings_are_redacted(text, secret, placeholder):
    cleaned = redact(text)
    assert secret not in cleaned
    assert placeholder in cleaned


def test_redaction_preserves_ordinary_text():
    text = "Saved filters reset after reopening the workspace, version 2.4 on Linux."
    assert redact(text) == text


def test_redaction_handles_empty_input():
    assert redact("") == ""


def test_multiple_secrets_in_one_message_are_all_redacted():
    cleaned = redact(
        "reach me at a.b@example.invalid or +15550107788, token ghp_abcdefghijklmnop1234"
    )
    assert "a.b@example.invalid" not in cleaned
    assert "15550107788" not in cleaned
    assert "ghp_abcdefghijklmnop1234" not in cleaned
