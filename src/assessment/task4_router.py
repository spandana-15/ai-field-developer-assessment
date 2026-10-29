"""Stage 4: SQLite-backed token reservations, 429/3s primary failover.

Run: uvicorn assessment.task4_router:app --port 8104
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .auth import verify_bearer
from .task4_limiter import SQLiteTokenLimiter, count_prompt_tokens

PRIMARY_URL = os.getenv('LLM_PRIMARY_URL', 'http://127.0.0.1:8102/primary')
BACKUP_URL = os.getenv('LLM_BACKUP_URL', 'http://127.0.0.1:8102/backup')
DB_PATH = os.getenv('RATE_DB_PATH', 'data/rate_limits.sqlite3')


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    prompt: str = Field(min_length=1, max_length=100_000)
    max_tokens: int = Field(ge=1, le=8192)
    model: str = Field(default='demo', min_length=1, max_length=100)


def gateway_error(code: str, message: str, status: int, **extra: Any) -> JSONResponse:
    return JSONResponse({'error': {'code': code, 'message': message, **extra}}, status_code=status)


def build_app(limiter: SQLiteTokenLimiter | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    application = FastAPI(title='Stage 4 LLM Rate-Limited Fallback Router')
    application.state.limiter = limiter or SQLiteTokenLimiter(DB_PATH)
    application.state.transport = transport

    async def try_provider(client: httpx.AsyncClient, url: str, payload: dict, timeout: float) -> httpx.Response:
        return await client.post(url, json=payload, timeout=timeout)

    @application.post('/v1/completions')
    async def route(request: Request):
        claims = verify_bearer(request.headers.get('authorization'))
        if claims is None:
            return gateway_error('UNAUTHORIZED', 'Valid bearer token required', 401)
        try:
            data = await request.json()
            parsed = CompletionRequest.model_validate(data)
        except (ValueError, ValidationError):
            return gateway_error('INVALID_REQUEST', 'Provide prompt and max_tokens with valid types and bounds', 400)

        # Key is taken ONLY from signed token; never from caller-supplied JSON.
        tenant = claims['tenant']
        quota = count_prompt_tokens(parsed.prompt) + parsed.max_tokens
        allowed, reservation_id, remaining, retry_after = await application.state.limiter.reserve(tenant, quota)
        if not allowed:
            return JSONResponse({'error': {'code': 'RATE_LIMITED', 'message': 'Tenant token quota exceeded'}},
                                status_code=429,
                                headers={'Retry-After': str(max(1, int(retry_after + 0.999)))})
        assert reservation_id is not None
        payload = parsed.model_dump()
        used_backup = False
        provider_response: httpx.Response | None = None
        try:
            async with httpx.AsyncClient(transport=application.state.transport) as client:
                try:
                    provider_response = await try_provider(client, PRIMARY_URL, payload, timeout=3.0)
                except httpx.TimeoutException:
                    used_backup = True
                except httpx.HTTPError:
                    # Network failures are treated as safe availability failures.
                    used_backup = True
                else:
                    if provider_response.status_code == 429:
                        used_backup = True
                if used_backup:
                    try:
                        provider_response = await try_provider(client, BACKUP_URL, payload, timeout=5.0)
                    except httpx.HTTPError:
                        provider_response = None

            if provider_response is None or provider_response.status_code != 200:
                # Releasing capacity is appropriate for unsuccessful upstream attempts.
                await application.state.limiter.settle(reservation_id, release=True)
                return gateway_error('PROVIDER_UNAVAILABLE', 'All eligible model routes failed', 502)
            try:
                body = provider_response.json()
                if not isinstance(body, dict) or not isinstance(body.get('text'), str):
                    raise ValueError('Invalid provider schema')
                usage = body.get('usage')
                actual = usage.get('total_tokens') if isinstance(usage, dict) else None
                if type(actual) is not int or actual < 0:
                    actual = None
            except (ValueError, TypeError):
                await application.state.limiter.settle(reservation_id, release=True)
                return gateway_error('PROVIDER_BAD_RESPONSE', 'Provider response was invalid', 502)
            await application.state.limiter.settle(reservation_id, actual_tokens=actual)
            return JSONResponse({'text': body['text'], 'provider': 'backup' if used_backup else 'primary',
                                 'usage': usage if isinstance(usage, dict) else {'reserved_tokens': quota}},
                                headers={'X-RateLimit-Remaining': str(remaining)})
        except Exception:
            # No exception strings/tracebacks are serialized into the response.
            await application.state.limiter.settle(reservation_id, release=True)
            return gateway_error('GATEWAY_ERROR', 'The request could not be completed', 502)

    @application.get('/health')
    async def health():
        return {'status': 'ok', 'stage': 4}

    return application


app = build_app()
