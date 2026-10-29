# Test verification report

Verification performed: 29 October 2026

## Automated suite

```text
50 passed, 1 skipped
```

- **Stage 1:** strict Pydantic validation scenarios passed. The official `mcp` v2 dependency is unavailable in the validation container, so the one real MCP SDK / stdio client test is **skipped, not claimed as tested**. Run `pip install -e '.[dev]'` and `pytest -q` on a network-enabled machine to execute it.
- **Stage 2:** validated tool list forwarding, admin/viewer role enforcement, invalid tokens, JSON-RPC error mapping, mixed batch filtering and ensuring blocked tools never reached the downstream mock.
- **Stage 3:** validated split email/SSN/card patterns, Luhn check, byte-split SSE and Unicode, full ASGI endpoint integration, malformed upstream data, bounded pending text and overlong candidate handling.
- **Stage 4:** validated atomic SQLite admission of 100 concurrent 1,000-token reservations under 50,000-token quota (exactly 50 admitted), rolling 60-second expiry, independent tenant quotas, quota release, primary completion, 429 failover, injected timeout failover, 500 sanitized error and signed tenant isolation.

## Real HTTP end-to-end verification

The four services were started on localhost and smoke requests were issued over HTTP using `scripts/run_all.py` and `scripts/demo_requests.py`:

```text
Stage 2: tool listing forwarded successfully
Stage 2: viewer admin call BLOCKED, admin call ALLOWED
Stage 3: streaming email, card and SSN redaction PASSED
  Sample: Hello! Reach me at [REDACTED] or use [REDACTED]. SSN: [REDACTED]. Thanks!
Stage 4: primary routing PASSED; HTTP 429 fallback PASSED
All local HTTP smoke tests passed.
```

A separate live test against the mock `force_timeout` model returned HTTP 200 from the backup provider in **3.09 seconds** after a 3-second primary timeout.

## Remaining deployment verification

1. Install official MCP SDK v2 from a network-connected Python environment.
2. Execute `python scripts/stage1_demo.py` and the complete pytest suite; confirm no skips.
3. For any real provider, configure the base URL, auth, model tokenizer, and schema contract; replace assessment tokens with real identity verification.

These are assessment demos, not production identity or payment systems.
