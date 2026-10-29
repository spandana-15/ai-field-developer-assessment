import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from assessment.auth import create_token
from assessment import task2_gateway


@pytest.fixture
def downstream():
    server = FastAPI()
    server.state.calls = []

    @server.post('/mcp')
    async def endpoint(request: Request):
        body = await request.json()
        server.state.calls.append(body)
        def reply(x):
            if 'id' not in x:
                return None
            return {'jsonrpc': '2.0', 'id': x['id'], 'result': {'echo_method': x['method']}}
        result = [value for item in (body if isinstance(body, list) else [body])
                  if (value := reply(item)) is not None]
        if not result:
            return Response(status_code=204)
        return JSONResponse(result if isinstance(body, list) else result[0], headers={'mcp-session-id': 'session-123'})

    return server


@pytest.fixture
def proxy(monkeypatch, downstream):
    actual = httpx.AsyncClient
    monkeypatch.setattr(task2_gateway, 'httpx', SimpleNamespace(
        AsyncClient=lambda *a, **kw: actual(transport=httpx.ASGITransport(app=downstream), base_url='http://downstream'),
        HTTPError=httpx.HTTPError))
    return task2_gateway.app


def msg(method, rid=1, name=None):
    value = {'jsonrpc': '2.0', 'id': rid, 'method': method}
    if name:
        value['params'] = {'name': name, 'arguments': {}}
    return value


@pytest.mark.asyncio
async def test_list_transparent_and_viewer_allowed(proxy, downstream):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url='http://gateway') as client:
        res = await client.post('/mcp', json=msg('tools/list'),
                                headers={'Authorization': f'Bearer {create_token("viewer")}'})
    assert res.status_code == 200
    assert res.json()['result']['echo_method'] == 'tools/list'
    assert res.headers.get('mcp-session-id') == 'session-123'
    assert len(downstream.state.calls) == 1


@pytest.mark.asyncio
async def test_viewer_cannot_call_admin_tool(proxy, downstream):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url='http://gateway') as client:
        res = await client.post('/mcp', json=msg('tools/call', 22, 'admin_reset_key'),
                                headers={'Authorization': f'Bearer {create_token("viewer")}'})
    assert res.status_code == 403
    assert res.json()['error']['code'] == -32001
    assert res.json()['id'] == 22
    assert downstream.state.calls == []


@pytest.mark.asyncio
async def test_admin_call_is_forwarded(proxy, downstream):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url='http://gateway') as client:
        res = await client.post('/mcp', json=msg('tools/call', 9, 'admin_reset_key'),
                                headers={'Authorization': f'Bearer {create_token("admin")}'})
    assert res.status_code == 200
    assert len(downstream.state.calls) == 1


@pytest.mark.asyncio
async def test_bad_or_unsigned_token_denied(proxy, downstream):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url='http://gateway') as client:
        res = await client.post('/mcp', json=msg('tools/call', 7, 'admin_reset_key'),
                                headers={'Authorization': 'Bearer admin'})
    assert res.json()['error']['code'] == -32001
    assert not downstream.state.calls


@pytest.mark.asyncio
async def test_mixed_batch_filters_admin_only(proxy, downstream):
    batch = [msg('tools/list', 1), msg('tools/call', 2, 'admin_reset_key'), msg('tools/call', 3, 'read_customer')]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url='http://gateway') as client:
        res = await client.post('/mcp', json=batch,
                                headers={'Authorization': f'Bearer {create_token("viewer")}'})
    result = {item['id']: item for item in res.json()}
    assert result[2]['error']['code'] == -32001
    assert result[1]['result'] and result[3]['result']
    assert len(downstream.state.calls) == 1
    assert {item['id'] for item in downstream.state.calls[0]} == {1, 3}


@pytest.mark.asyncio
async def test_invalid_protocol_rejected(proxy, downstream):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url='http://gateway') as client:
        res = await client.post('/mcp', json={'jsonrpc': '1.0', 'id': 1, 'method': 'tools/list'})
        parse = await client.post('/mcp', content=b'{broken', headers={'Content-Type': 'application/json'})
        bad = await client.post('/mcp', json=msg('tools/call'),
                                headers={'Authorization': f'Bearer {create_token("admin")}'})
    assert res.json()['error']['code'] == -32600
    assert parse.json()['error']['code'] == -32700
    assert bad.json()['error']['code'] == -32602
    assert downstream.state.calls == []
