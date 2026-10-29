import asyncio
import httpx
import pytest

from assessment.auth import create_token
from assessment.task4_limiter import SQLiteTokenLimiter, count_prompt_tokens
from assessment.task4_router import build_app


@pytest.mark.asyncio
async def test_sqlite_limit_exact_window_and_tenant_isolation(tmp_path):
    limiter = SQLiteTokenLimiter(tmp_path / 'rate.sqlite3', limit=10, window=60)
    accepted, rid, remain, _ = await limiter.reserve('tenant-a', 7, now=100)
    assert accepted and remain == 3
    accepted, _, _, retry = await limiter.reserve('tenant-a', 4, now=101)
    assert not accepted and retry > 0
    accepted, _, _, _ = await limiter.reserve('tenant-b', 9, now=101)
    assert accepted
    accepted, _, remain, _ = await limiter.reserve('tenant-a', 10, now=160)
    assert accepted and remain == 0


@pytest.mark.asyncio
async def test_concurrent_reservations_atomic(tmp_path):
    limiter = SQLiteTokenLimiter(tmp_path / 'rate.sqlite3', limit=50_000)
    results = await asyncio.gather(*(limiter.reserve('shared-tenant', 1000) for _ in range(100)))
    assert sum(result[0] for result in results) == 50
    assert await limiter.usage('shared-tenant') == 50_000


@pytest.mark.asyncio
async def test_settle_and_release(tmp_path):
    limiter = SQLiteTokenLimiter(tmp_path / 'rate.sqlite3', limit=100)
    _, rid, _, _ = await limiter.reserve('customer', 30)
    await limiter.settle(rid, actual_tokens=12)
    assert await limiter.usage('customer') == 12
    await limiter.settle(rid, release=True)
    assert await limiter.usage('customer') == 0


def transport_for(behavior):
    calls = []
    async def fake(request: httpx.Request):
        calls.append(str(request.url))
        if request.url.path == '/primary':
            if behavior == '429':
                return httpx.Response(429, json={'debug': 'secret upstream'})
            if behavior == 'timeout':
                raise httpx.ReadTimeout('private provider exception', request=request)
            if behavior == '500':
                return httpx.Response(500, json={'private': 'server stack trace'})
        if request.url.path == '/backup' and behavior == 'backup_fail':
            return httpx.Response(503, text='sensitive traceback')
        return httpx.Response(200, json={'text': 'provider says hello', 'usage': {'total_tokens': 2}})
    return httpx.MockTransport(fake), calls


async def call_router(tmp_path, behavior, token_role='viewer'):
    limiter = SQLiteTokenLimiter(tmp_path / 'rate.sqlite3')
    transport, calls = transport_for(behavior)
    app = build_app(limiter, transport)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        res = await client.post('/v1/completions', json={'prompt': 'Hello', 'max_tokens': 50},
                                headers={'Authorization': f'Bearer {create_token(token_role, "tenant-a")}'})
    return res, calls, limiter


@pytest.mark.asyncio
@pytest.mark.parametrize('behavior,expected', [('ok', 'primary'), ('429', 'backup'), ('timeout', 'backup')])
async def test_route_success_and_fallback(tmp_path, behavior, expected):
    res, calls, limiter = await call_router(tmp_path, behavior)
    assert res.status_code == 200, res.text
    assert res.json()['provider'] == expected
    assert len(calls) == (2 if expected == 'backup' else 1)
    assert await limiter.usage('tenant-a') == 2


@pytest.mark.asyncio
async def test_non_429_server_error_does_not_fallback_and_is_sanitized(tmp_path):
    res, calls, _ = await call_router(tmp_path, '500')
    assert res.status_code == 502
    assert 'server stack' not in res.text
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_rate_limit_and_no_provider_call(tmp_path):
    limiter = SQLiteTokenLimiter(tmp_path / 'rate.sqlite3', limit=15)
    transport, calls = transport_for('ok')
    app = build_app(limiter, transport)
    headers = {'Authorization': f'Bearer {create_token("viewer", "tenant-a")}' }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        res = await client.post('/v1/completions', json={'prompt': 'Hello', 'max_tokens': 20}, headers=headers)
    assert res.status_code == 429
    assert res.json()['error']['code'] == 'RATE_LIMITED'
    assert res.headers.get('Retry-After')
    assert not calls


@pytest.mark.asyncio
async def test_tenant_is_signed_not_request_controlled(tmp_path):
    limiter = SQLiteTokenLimiter(tmp_path / 'rate.sqlite3', limit=20)
    transport, calls = transport_for('ok')
    app = build_app(limiter, transport)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        res = await client.post('/v1/completions', json={'prompt': 'Hi', 'max_tokens': 5, 'tenant': 'another'},
                                headers={'Authorization': f'Bearer {create_token("viewer")}'})
    assert res.status_code == 400
    assert not calls


def test_unicode_prompt_conservative_count():
    assert count_prompt_tokens('🙂') == len('🙂'.encode('utf-8'))
