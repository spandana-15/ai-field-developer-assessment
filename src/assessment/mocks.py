"""Mock JSON-RPC MCP and LLM provider endpoints, for reproducible demos only.

Run: uvicorn assessment.mocks:app --port 8102
"""
from __future__ import annotations

import asyncio
import json
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

app = FastAPI(title='Assessment Mock Providers')
app.state.downstream_calls = []


def rpc(item: dict) -> dict | None:
    if 'id' not in item:
        return None
    rid = item['id']
    method = item.get('method')
    if method == 'tools/list':
        return {'jsonrpc': '2.0', 'id': rid, 'result': {'tools': [
            {'name': 'get_customer_record', 'inputSchema': {'type': 'object'}},
            {'name': 'admin_reset_key', 'inputSchema': {'type': 'object'}},
        ]}}
    if method == 'tools/call':
        return {'jsonrpc': '2.0', 'id': rid, 'result': {'content': [
            {'type': 'text', 'text': f"Mock tool completed: {item['params']['name']}"}]}}
    return {'jsonrpc': '2.0', 'id': rid, 'error': {'code': -32601, 'message': 'Method not found'}}


@app.post('/mcp')
async def mcp(request: Request):
    payload = await request.json()
    items = payload if isinstance(payload, list) else [payload]
    app.state.downstream_calls.extend(items)
    results = [result for item in items if (result := rpc(item)) is not None]
    if not results:
        return Response(status_code=204)
    return JSONResponse(results if isinstance(payload, list) else results[0])


@app.post('/primary')
async def primary(request: Request):
    data = await request.json()
    if data.get('model') == 'force_429':
        return JSONResponse({'detail': 'provider secret SHOULD NOT LEAK'}, status_code=429)
    if data.get('model') == 'force_timeout':
        await asyncio.sleep(5)
    if data.get('model') == 'force_error':
        return JSONResponse({'detail': 'secret upstream stack'}, status_code=500)
    return {'text': f"Primary response to {data['prompt']}", 'usage': {'total_tokens': 8}}


@app.post('/backup')
async def backup(request: Request):
    data = await request.json()
    if data.get('model') == 'force_backup_error':
        return JSONResponse({'detail': 'Internal provider secret'}, status_code=503)
    return {'text': f"Backup response to {data['prompt']}", 'usage': {'total_tokens': 9}}


@app.post('/v1/chat/completions')
async def stream_chat(request: Request):
    data = await request.json()
    # Deliberate PII fragments and variable wire frame boundaries.
    sample = data.get('mock_chunks', [
        'Hello! Reach me at ali', 'ce@example.', 'com or use 4111 ',
        '1111 1111 1111.', ' SSN: 123-', '45-6789. Thanks!',
    ])

    async def chunks():
        for piece in sample:
            event = 'data: ' + json.dumps({'choices': [{'delta': {'content': piece}}]}) + '\n\n'
            midpoint = len(event) // 2
            yield event[:midpoint].encode('utf-8')
            yield event[midpoint:].encode('utf-8')
            await asyncio.sleep(0.003)
        yield b'data: [DONE]\n\n'

    return StreamingResponse(chunks(), media_type='text/event-stream')


@app.get('/health')
async def health():
    return {'status': 'ok', 'mock_calls': len(app.state.downstream_calls)}
