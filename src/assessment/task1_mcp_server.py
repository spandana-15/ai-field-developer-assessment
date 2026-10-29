"""Stage 1: official MCP v2 low-level SDK, strict Pydantic validation, stdio-only.

Start with: python -m assessment.task1_mcp_server
No logging or other stdout writes: the MCP transport owns stdout.
"""
import asyncio
import json
import logging
import sys

from mcp import MCPError
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    INVALID_PARAMS,
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)
from pydantic import ValidationError

from .task1_core import (
    CustomerRequest,
    RefundRequest,
    get_customer_record,
    trigger_refund,
)

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("assessment.mcp")

TOOLS = [
    Tool(
        name="get_customer_record",
        description="Read a simulated customer record by CUST-XXXXX ID.",
        input_schema=CustomerRequest.model_json_schema(),
    ),
    Tool(
        name="trigger_refund",
        description="Simulate (do not execute) a customer refund.",
        input_schema=RefundRequest.model_json_schema(),
    ),
]


async def list_tools(
    ctx: ServerRequestContext, params: PaginatedRequestParams | None
) -> ListToolsResult:
    return ListToolsResult(tools=TOOLS)


async def call_tool(ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
    try:
        if params.name == "get_customer_record":
            validated = CustomerRequest.model_validate(params.arguments or {})
            record = get_customer_record(validated)
        elif params.name == "trigger_refund":
            validated = RefundRequest.model_validate(params.arguments or {})
            record = trigger_refund(validated)
        else:
            raise MCPError(INVALID_PARAMS, "Unknown tool")
    except ValidationError as exc:
        # Do not echo user supplied values into the error message.
        problems = [{"field": ".".join(map(str, e["loc"])), "reason": e["type"]} for e in exc.errors()]
        raise MCPError(INVALID_PARAMS, "Invalid tool arguments", data=problems) from None
    except LookupError:
        return CallToolResult(content=[TextContent(type="text", text="Customer not found")], is_error=True)
    logger.info("Handled MCP tool %s", params.name)
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(record, separators=(",", ":")))],
        structured_content=record,
    )


server = Server("assessment-customer-server", on_list_tools=list_tools, on_call_tool=call_tool)


async def main() -> None:
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
