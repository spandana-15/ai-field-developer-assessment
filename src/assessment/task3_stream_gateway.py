"""Stage 3 SSE proxy. Intercepts provider deltas, emits redacted SSE deltas."""
from __future__ import annotations

import codecs
import json
import os
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .task3_redactor import StreamingRedactor

app = FastAPI(title="Stage 3 LLM Streaming Guardrail")
PROVIDER_URL = os.getenv("LLM_STREAM_URL", "http://127.0.0.1:8102/v1/chat/completions")


def event(text: str) -> bytes:
    data = {"choices": [{"delta": {"content": text}}]}
    return ("data: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n\n").encode("utf-8")


def frame_to_text(frame: str) -> tuple[str | None, bool]:
    lines = [line[5:].lstrip(" ") for line in frame.split("\n") if line.startswith("data:")]
    if not lines:
        return None, False
    data = "\n".join(lines)
    if data == "[DONE]":
        return None, True
    obj = json.loads(data)
    choices = obj["choices"]
    if not isinstance(choices, list) or not choices:
        return None, False
    delta = choices[0].get("delta", {})
    value = delta.get("content")
    if value is not None and not isinstance(value, str):
        raise ValueError("Provider sent non-text content")
    return value, False


async def sanitize_sse(upstream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    redactor = StreamingRedactor()
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    wire_buffer = ""
    try:
        async for chunk in upstream:
            wire_buffer += decoder.decode(chunk)
            if len(wire_buffer) > 131072:  # Malformed or abusive SSE frame.
                raise ValueError("SSE frame too long")
            # Normalize CRLF after decoding, including across transport chunks.
            wire_buffer = wire_buffer.replace("\r\n", "\n")
            while "\n\n" in wire_buffer:
                frame, wire_buffer = wire_buffer.split("\n\n", 1)
                content, done = frame_to_text(frame)
                if content:
                    for piece in redactor.feed(content):
                        yield event(piece)
                if done:
                    for piece in redactor.finish():
                        yield event(piece)
                    yield b"data: [DONE]\n\n"
                    return
        wire_buffer += decoder.decode(b"", final=True)
        if wire_buffer.strip():
            # Incomplete frame is not trusted; do not expose its content.
            raise ValueError("Provider closed with incomplete SSE frame")
        for piece in redactor.finish():
            yield event(piece)
        yield b"data: [DONE]\n\n"
    except (ValueError, UnicodeError, KeyError, TypeError, json.JSONDecodeError, httpx.HTTPError):
        # Nothing from the malformed upstream is copied into the error event.
        yield b'data: {"error":{"code":"STREAM_FAILED","message":"Stream processing failed"}}\n\n'


@app.post("/v1/chat/completions")
async def complete(request: Request):
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"error": {"code": "INVALID_JSON", "message": "Invalid JSON"}}, status_code=400)
    if not isinstance(payload, dict) or payload.get("stream") is not True:
        return JSONResponse({"error": {"code": "INVALID_REQUEST", "message": "stream=true required"}}, status_code=400)

    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=3.0))
    try:
        req = client.build_request("POST", PROVIDER_URL, json=payload,
                                   headers={"accept": "text/event-stream"})
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        return JSONResponse({"error": {"code": "PROVIDER_UNAVAILABLE", "message": "Provider unavailable"}}, status_code=502)
    if upstream.status_code != 200:
        await upstream.aclose()
        await client.aclose()
        return JSONResponse({"error": {"code": "PROVIDER_ERROR", "message": "Provider failed"}}, status_code=502)

    async def response_body() -> AsyncIterator[bytes]:
        try:
            async for output in sanitize_sse(upstream.aiter_bytes()):
                yield output
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(response_body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
