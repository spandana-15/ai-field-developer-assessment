"""Pure validation tests always run; official SDK integration requires `mcp>=2`."""
import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from assessment.task1_core import CustomerRequest, RefundRequest, get_customer_record, trigger_refund


@pytest.mark.parametrize('bad', ['CUST-1234', 'CUST-123456', 'cust-12345', 'CUST-12ABC', ' CUST-00001', None, 12345])
def test_customer_id_rejected(bad):
    with pytest.raises(ValidationError):
        CustomerRequest.model_validate({'customer_id': bad})


def test_customer_rejects_extra_fields():
    with pytest.raises(ValidationError):
        CustomerRequest.model_validate({'customer_id': 'CUST-00001', 'privileged': True})


@pytest.mark.parametrize('amount', [0, -2.1, True, '15', float('nan'), float('inf'), None])
def test_refund_amount_rejected(amount):
    with pytest.raises(ValidationError):
        RefundRequest.model_validate({'customer_id': 'CUST-00001', 'amount': amount,
                                      'reason': 'Customer returned the item'})


@pytest.mark.parametrize('reason', ['', 'short', '         ', None, 12])
def test_refund_reason_rejected(reason):
    with pytest.raises(ValidationError):
        RefundRequest.model_validate({'customer_id': 'CUST-00001', 'amount': 19.99, 'reason': reason})


def test_valid_domain_operations():
    customer = get_customer_record(CustomerRequest(customer_id='CUST-00001'))
    assert customer['status'] == 'active'
    receipt = trigger_refund(RefundRequest(customer_id='CUST-00001', amount=15.0,
                                           reason='Purchased by mistake'))
    assert receipt['status'] == 'simulated'
    assert receipt['refund_id'].startswith('REF-')


def test_missing_customer():
    with pytest.raises(LookupError):
        get_customer_record(CustomerRequest(customer_id='CUST-99999'))


@pytest.mark.asyncio
async def test_official_mcp_protocol_and_stdio():
    mcp = pytest.importorskip('mcp', reason='mcp v2 must be installed to run wire-level tests')
    from mcp import Client, MCPError
    from mcp.client.stdio import StdioServerParameters
    from mcp.types import INVALID_PARAMS
    import assessment.task1_mcp_server as module

    async with Client(module.server, mode='legacy') as client:
        listing = await client.list_tools()
        assert {'get_customer_record', 'trigger_refund'} == {tool.name for tool in listing.tools}
        result = await client.call_tool('get_customer_record', {'customer_id': 'CUST-00001'})
        assert result.structured_content['customer_id'] == 'CUST-00001'
        with pytest.raises(MCPError) as error:
            await client.call_tool('trigger_refund', {'customer_id': 'oops', 'amount': 0, 'reason': 'short'})
        assert error.value.code == INVALID_PARAMS

    project_root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(command=sys.executable, args=['-m', 'assessment.task1_mcp_server'],
                                   env={**os.environ, 'PYTHONPATH': str(project_root / 'src')})
    async with Client(params, mode='legacy', read_timeout_seconds=6.0) as client:
        listed = await client.list_tools()
        assert len(listed.tools) == 2
        result = await client.call_tool('get_customer_record', {'customer_id': 'CUST-00002'})
        assert result.structured_content['name'] == 'Taylor Brooks'
