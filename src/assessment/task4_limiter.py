"""SQLite token-aware sliding window using atomic cross-process reservations.

A reserved amount covers prompt tokens and the maximum number of completion tokens.
Its clock is wall-clock UTC seconds; SQLite serializes writers with BEGIN IMMEDIATE.
"""
from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

LIMIT = 50_000
WINDOW = 60.0


def count_prompt_tokens(prompt: str) -> int:
    """Conservative tokenizer-independent admission estimate.

    Charge one token per UTF-8 byte, an intentionally pessimistic upper bound
    for common byte-based BPE encodings. Exact model tokenizers should replace
    this in production; never accept a client-provided token count as truth.
    """
    return len(prompt.encode('utf-8'))


class SQLiteTokenLimiter:
    def __init__(self, path: str | Path, limit: int = LIMIT, window: float = WINDOW):
        self.path = str(path)
        self.limit = limit
        self.window = window
        if limit <= 0 or window <= 0:
            raise ValueError('limit and window must be positive')
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._setup()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.execute('PRAGMA busy_timeout=10000')
        conn.execute('PRAGMA journal_mode=WAL')
        return conn

    def _setup(self) -> None:
        with self._connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS token_events (
                reservation_id TEXT PRIMARY KEY,
                tenant TEXT NOT NULL,
                reserved_tokens INTEGER NOT NULL CHECK (reserved_tokens >= 0),
                recorded_at REAL NOT NULL
            )''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_tenant_time ON token_events (tenant, recorded_at)')

    def _reserve(self, tenant: str, tokens: int, now: float) -> tuple[bool, str | None, int, float]:
        if not isinstance(tenant, str) or not tenant or isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 1:
            raise ValueError('Invalid tenant or tokens')
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            # Evict globally; SELECT below is scoped to the tenant and the exact window.
            conn.execute('DELETE FROM token_events WHERE recorded_at <= ?', (now - self.window,))
            spent = conn.execute('''SELECT COALESCE(SUM(reserved_tokens), 0)
                                    FROM token_events WHERE tenant = ? AND recorded_at > ?''',
                                 (tenant, now - self.window)).fetchone()[0]
            allowed = tokens <= self.limit and spent + tokens <= self.limit
            if allowed:
                reservation_id = uuid4().hex
                conn.execute('INSERT INTO token_events VALUES (?,?,?,?)', (reservation_id, tenant, tokens, now))
                remaining = self.limit - spent - tokens
            else:
                reservation_id = None
                remaining = self.limit - spent
            # Retry is conservative; user can retry when oldest record expires.
            oldest = conn.execute('SELECT MIN(recorded_at) FROM token_events WHERE tenant=? AND recorded_at > ?',
                                  (tenant, now - self.window)).fetchone()[0]
            retry_after = max(0.0, self.window - (now - oldest)) if oldest is not None else self.window
            conn.commit()
            return allowed, reservation_id, remaining, retry_after

    async def reserve(self, tenant: str, tokens: int, *, now: float | None = None) -> tuple[bool, str | None, int, float]:
        timestamp = time.time() if now is None else now
        return await asyncio.to_thread(self._reserve, tenant, tokens, timestamp)

    def _settle(self, reservation_id: str, actual_tokens: int | None, release: bool) -> None:
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if release:
                conn.execute('DELETE FROM token_events WHERE reservation_id=?', (reservation_id,))
            elif actual_tokens is not None and isinstance(actual_tokens, int) and actual_tokens >= 0:
                # Can release unused tokens; never increase a reservation after admission.
                conn.execute('''UPDATE token_events SET reserved_tokens = MIN(reserved_tokens, ?)
                                WHERE reservation_id = ?''', (actual_tokens, reservation_id))
            conn.commit()

    async def settle(self, reservation_id: str, actual_tokens: int | None = None, *, release: bool = False) -> None:
        await asyncio.to_thread(self._settle, reservation_id, actual_tokens, release)

    def _usage(self, tenant: str, now: float) -> int:
        with self._connect() as conn:
            return conn.execute('SELECT COALESCE(SUM(reserved_tokens), 0) FROM token_events WHERE tenant=? AND recorded_at > ?',
                                (tenant, now - self.window)).fetchone()[0]

    async def usage(self, tenant: str, *, now: float | None = None) -> int:
        return await asyncio.to_thread(self._usage, tenant, time.time() if now is None else now)
