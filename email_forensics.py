"""
Email-layer forensics — extra VEC signals beyond the basic header checks.

Adds three detectors that catch tricks designed to fool both humans and
keyword-based filters:

  * Look-alike / typosquat domains — sender domain is a near-miss of a known
    vendor domain (e.g. 'atlantlc-shipping.com' vs 'atlantic-shipping.com').
  * Homoglyph / mixed-script text — Cyrillic/Greek letters that look like Latin
    ones, or IDN/punycode domains (the classic homograph attack).
  * Zero-width / invisible characters — used to break up keywords so naive
    filters miss them, or to hide content.

These return flags in the same shape as email_ingestor's VEC flags so they merge
straight into Layer 1.
"""
from __future__ import annotations

import re

# Curated allow-list of legitimate vendor domains (would come from a vendor
# directory in production). Look-alikes of these are flagged.
KNOWN_VENDOR_DOMAINS = {
    "atlantic-shipping.com",
    "nordic-freight.com",
    "iberian-cargo.com",
}

_FREE_PROVIDERS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "protonmail.com", "live.com", "aol.com", "mail.com",
}

_ZERO_WIDTH = {"​", "‌", "‍", "⁠", "﻿"}


def _domain(addr: str) -> str:
    a = (addr or "").lower()
    if "<" in a:
        a = a.split("<")[-1].replace(">", "").strip()
    return a.split("@")[-1].strip() if "@" in a else ""


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _has_zero_width(s: str) -> bool:
    return any(ch in _ZERO_WIDTH for ch in (s or ""))


def _has_homoglyph(s: str) -> bool:
    # Latin text containing Cyrillic (U+0400–04FF) or Greek (U+0370–03FF) letters
    has_latin = any("a" <= ch.lower() <= "z" for ch in (s or ""))
    has_confusable = any("Ѐ" <= ch <= "ӿ" or "Ͱ" <= ch <= "Ͽ" for ch in (s or ""))
    return has_latin and has_confusable


def analyze(sender: str, reply_to: str = "", subject: str = "",
            known_domains: set | None = None) -> list:
    """Return a list of extra VEC flags: {severity, code, detail}."""
    known = known_domains if known_domains is not None else KNOWN_VENDOR_DOMAINS
    flags = []
    sdom = _domain(sender)

    # --- Look-alike / typosquat domain ---
    if sdom and sdom not in known and sdom not in _FREE_PROVIDERS:
        for good in known:
            dist = _levenshtein(sdom, good)
            if 0 < dist <= 2:
                flags.append({
                    "severity": "HIGH",
                    "code": "LOOKALIKE_DOMAIN",
                    "detail": (
                        f"Sender domain '{sdom}' is a look-alike of known vendor domain "
                        f"'{good}' (edit distance {dist}) — typosquatting / domain spoofing."
                    ),
                })
                break

    # --- Homoglyph / punycode in the sender domain ---
    if sdom.startswith("xn--") or _has_homoglyph(sdom):
        flags.append({
            "severity": "HIGH",
            "code": "HOMOGLYPH_DOMAIN",
            "detail": f"Sender domain '{sdom}' uses internationalised/confusable characters — possible homograph attack.",
        })

    # --- Zero-width / homoglyph in human-visible fields ---
    for label, value in (("subject", subject), ("sender", sender), ("reply-to", reply_to)):
        if value and _has_zero_width(value):
            flags.append({
                "severity": "MEDIUM",
                "code": "ZERO_WIDTH_CHARS",
                "detail": f"The {label} contains invisible zero-width characters — used to evade keyword filters or hide text.",
            })
        if value and label != "sender" and _has_homoglyph(value):
            flags.append({
                "severity": "MEDIUM",
                "code": "HOMOGLYPH_TEXT",
                "detail": f"The {label} mixes Latin with confusable Cyrillic/Greek look-alike letters.",
            })

    return flags
