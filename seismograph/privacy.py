"""Author anonymization and content redaction.

Nothing that identifies a person should reach storage, the LLM, or a report.
"""

from __future__ import annotations

import hashlib
import hmac
import re

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{7,}\d(?![\w.])")
_TOKEN = re.compile(
    r"\b(?:sk|pk|rk|api|key|tok|ghp|gho|xox[abps])[-_][A-Za-z0-9_-]{12,}\b",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._~+/=-]{12,}")
_LONG_SECRET = re.compile(r"\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{40,}\b")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_MENTION = re.compile(r"<@!?\d+>")
_INVITE = re.compile(r"\b(?:discord\.gg|discord\.com/invite)/[\w-]+", re.IGNORECASE)


def hash_author(author_id: int | str, salt: str) -> str:
    """Return a stable, salt-dependent pseudonym for a Discord author id.

    HMAC-SHA256 keyed with AUTHOR_HASH_SALT. The same author yields the same
    value while the salt is unchanged; rotating the salt breaks all linkage.
    """
    if not salt:
        raise ValueError("author hash salt must not be empty")
    digest = hmac.new(salt.encode("utf-8"), str(author_id).encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:32]


def redact(text: str) -> str:
    """Remove obvious sensitive strings before storage or analysis."""
    if not text:
        return ""
    text = _MENTION.sub("[user]", text)
    text = _INVITE.sub("[invite]", text)
    text = _EMAIL.sub("[email]", text)
    text = _BEARER.sub("[token]", text)
    text = _TOKEN.sub("[token]", text)
    text = _LONG_SECRET.sub("[token]", text)
    text = _IPV4.sub("[ip]", text)
    text = _PHONE.sub("[phone]", text)
    return text
