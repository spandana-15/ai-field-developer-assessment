"""Stage 3 PII redactor: bounded state and chunk-boundary-safe streaming.

We do not flush an unfinished token. Unlike a naive regex-per-delta algorithm,
this catches 'alice@exa' + 'mple.com' and '4111 1111' + ' 1111 1111'.
Sensitive candidate runs longer than MAX_PENDING are redacted fail-closed.
"""
from __future__ import annotations

import re

EMAIL = r"(?<![\w.+%-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}(?![A-Za-z])"
SSN = r"(?<!\d)\d{3}[- ]?\d{2}[- ]?\d{4}(?!\d)"
CARD = r"(?<![\d-])(?:\d[ -]?){12,18}\d(?!\d)"
PII = re.compile(f"(?P<email>{EMAIL})|(?P<ssn>{SSN})|(?P<card>{CARD})", re.IGNORECASE)


def luhn(value: str) -> bool:
    digits = [int(ch) for ch in value if ch.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, num in enumerate(reversed(digits)):
        if i % 2:
            num *= 2
            if num > 9:
                num -= 9
        total += num
    return total % 10 == 0


def redact(text: str) -> str:
    def sub(match: re.Match[str]) -> str:
        if match.lastgroup == "card" and not luhn(match.group(0)):
            return match.group(0)
        return "[REDACTED]"
    return PII.sub(sub, text)


def safe_boundary(text: str) -> int:
    """Last delimiter not inside a potential space-separated card/SSN."""
    cut = 0
    for i, char in enumerate(text):
        if char in ",;!?()[]{}<>:\n\r\t ":
            if char.isspace():
                left = i - 1
                right = i + 1
                while left >= 0 and text[left].isspace():
                    left -= 1
                while right < len(text) and text[right].isspace():
                    right += 1
                if left >= 0 and right < len(text) and text[left].isdigit() and text[right].isdigit():
                    continue
                # At chunk end, space after a digit may still be a card continuation.
                if left >= 0 and text[left].isdigit() and right == len(text):
                    continue
            cut = i + 1
    return cut


class StreamingRedactor:
    MAX_PENDING = 512
    INPUT_PIECE = 128

    def __init__(self) -> None:
        self.pending = ""
        self.dropping_oversized_token = False

    def feed(self, chunk: str) -> list[str]:
        outputs: list[str] = []
        for offset in range(0, len(chunk), self.INPUT_PIECE):
            part = chunk[offset:offset + self.INPUT_PIECE]
            if self.dropping_oversized_token:
                # Find the FIRST safe delimiter, not the last: otherwise
                # innocuous words following the oversized token are lost.
                cut = next((i for i in range(1, len(part) + 1)
                            if safe_boundary(part[:i]) == i), 0)
                if cut == 0:
                    continue
                self.dropping_oversized_token = False
                part = part[cut:]
            self.pending += part
            cut = safe_boundary(self.pending)
            if cut:
                outputs.append(redact(self.pending[:cut]))
                self.pending = self.pending[cut:]
            if len(self.pending) > self.MAX_PENDING:
                outputs.append("[REDACTED]")
                self.pending = ""
                self.dropping_oversized_token = True
        return [part for part in outputs if part]

    def finish(self) -> list[str]:
        if self.dropping_oversized_token:
            self.dropping_oversized_token = False
            return []
        result = redact(self.pending)
        self.pending = ""
        return [result] if result else []
