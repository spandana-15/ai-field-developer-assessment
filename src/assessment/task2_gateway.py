"""Stage 2: MCP JSON-RPC proxy with role-based tool-call interception."""
from __future__ import annotations

import os
import json
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .auth import verify_bearer

app = FastAPI(title="Stage 2 MCP Security Gateway")
DOWNSTREAM = os.getenv("MCP_DOWNSTREAM_URL", "http://127.0.0.1:8101/mcp")


def error(code: int, message: str, request_id: Any = None) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def check_envelope(obj: object) -> dict | None:
    if not isinstance(obj, dict) or obj.get("jsonrpc") != "2.0" or not isinstance(obj.get("method"), str):
        return error(-32600, "Invalid Request")
    request_id = obj.get("id")
    if "id" in obj and (isinstance(request_id, bool) or not (isinstance(request_id, (int, str)) or request_id is None)):
        return error(-32600, "Invalid Request")
    if "params" in obj and not isinstance(obj["params"], (dict, list)):
        return error(-32602, "Invalid params", request_id)
    if obj["method"] == "tools/call":
        params = obj.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return error(-32602, "Invalid params", request_id)
    return None


def denied_request(obj: dict, role: str) -> bool:
    return (obj.get("method") == "tools/call"
            and obj["params"]["name"].startswith("admin_")
            and role != "admin")


async def forward(payload: Any, request: Request) -> Response:
    # Fixed downstream configured by deployment; never from user input.
    headers = {"content-type": "application/json"}
    for name in ("mcp-session-id", "mcp-protocol-version", "accept"):
        if value := request.headers.get(name):
            headers[name] = value
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            upstream = await client.post(DOWNSTREAM, json=payload, headers=headers)
    except httpx.HTTPError:
        return JSONResponse(error(-32002, "Downstream unavailable", payload.get("id") if isinstance(payload, dict) else None), status_code=502)
    filtered_headers = {
        key: val for key, val in upstream.headers.items()
        if key.lower() in {"content-type", "mcp-session-id", "mcp-protocol-version"}
    }
    return Response(content=upstream.content, status_code=upstream.status_code, headers=filtered_headers)


@app.post("/mcp")
async def mcp_gateway(request: Request) -> Response:
    try:
        data = await request.json()
    except ValueError:
        return JSONResponse(error(-32700, "Parse error"), status_code=400)

    if isinstance(data, list):
        if not data:
            return JSONResponse(error(-32600, "Invalid Request"), status_code=400)
        # Validate/authorize every element before forwarding any of the batch.
        # A mixed batch is split so rejected tool calls are never leaked downstream.
        claims = verify_bearer(request.headers.get("authorization"))
        role = claims["role"] if claims else ""
        responses, allowed = [], []
        for item in data:
            invalid = check_envelope(item)
            if invalid:
                responses.append(invalid)
            elif not claims or denied_request(item, role):
                if "id" in item:
                    responses.append(error(-32001, "Unauthorized Tool Call", item["id"]))
            else:
                allowed.append(item)
        if not allowed:
            return JSONResponse(responses) if responses else Response(status_code=204)
        upstream = await forward(allowed, request)
        if upstream.status_code == 204:
            return JSONResponse(responses) if responses else Response(status_code=204)
        try:
            forwarded = json.loads(upstream.body)
        except ValueError:
            return JSONResponse(error(-32002, "Invalid downstream response"), status_code=502)
        if isinstance(forwarded, list):
            responses.extend(forwarded)
        else:
            responses.append(forwarded)
        return JSONResponse(responses)

    invalid = check_envelope(data)
    if invalid:
        return JSONResponse(invalid, status_code=400)
    claims = verify_bearer(request.headers.get("authorization"))
    if claims is None or denied_request(data, claims["role"]):
        if "id" not in data:  # JSON-RPC notifications must not receive errors.
            return Response(status_code=204)
        return JSONResponse(error(-32001, "Unauthorized Tool Call", data["id"]), status_code=403)
    return await forward(data, request)
