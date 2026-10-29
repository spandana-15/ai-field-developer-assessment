"""Exercise the actual stdio MCP transport with the official v2 SDK.

Run: python scripts/stage1_demo.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client, MCPError
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    params = StdioServerParameters(command=sys.executable,
                                   args=['-m', 'assessment.task1_mcp_server'],
                                   env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')})
    async with Client(params, mode='legacy', read_timeout_seconds=10) as client:
        listing = await client.list_tools()
        print('Published tools:', [tool.name for tool in listing.tools])
        result = await client.call_tool('get_customer_record', {'customer_id': 'CUST-00001'})
        print('Customer:', json.dumps(result.structured_content, indent=2))
        result = await client.call_tool('trigger_refund', {
            'customer_id': 'CUST-00001', 'amount': 25.5,
            'reason': 'Customer returned item for refund',
        })
        print('Refund simulation:', json.dumps(result.structured_content, indent=2))
        try:
            await client.call_tool('trigger_refund', {'customer_id': 'BAD', 'amount': -10, 'reason': 'no'})
        except MCPError as exc:
            print(f'Invalid params rejected with standard JSON-RPC code: {exc.code}')
            assert exc.code == -32602


if __name__ == '__main__':
    asyncio.run(main())
