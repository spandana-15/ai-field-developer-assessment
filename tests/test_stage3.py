import asyncio
import json
from types import SimpleNamespace

import httpx

import pytest

from assessment.task3_redactor import StreamingRedactor, redact, luhn
from assessment.task3_stream_gateway import sanitize_sse


def stream(chunks):
    redactor = StreamingRedactor()
    result = []
    for chunk in chunks:
        result.extend(redactor.feed(chunk))
    result.extend(redactor.finish())
    return ''.join(result)


@pytest.mark.parametrize('chunks', [
    ['mail alice@', 'example.com now'],
    ['email alice@example', '.', 'com please'],
    ['email alice', '@example.com please'],
])
def test_email_split_chunks(chunks):
    actual = stream(chunks)
    assert '[REDACTED]' in actual
    assert 'alice' not in actual
    assert 'example' not in actual


def test_redacts_ssn_card_and_preserves_safe_text():
    result = stream(['Welcome. SSN 123-', '45-6789, card 4111 ', '1111 1111 1111! Done.'])
    assert result == 'Welcome. SSN [REDACTED], card [REDACTED]! Done.'


def test_non_card_number_not_redacted():
    assert '1234 5678' in stream(['1234 ', '5678 is not a credit card.'])
    assert not luhn('4111111111111112')
    assert luhn('4111111111111111')


def test_bounded_memory_on_big_input():
    redactor = StreamingRedactor()
    outputs = []
    for _ in range(500):
        outputs += redactor.feed('hello, ' * 100 + '4111 1111 1111 1111 ')
        assert len(redactor.pending) <= redactor.MAX_PENDING
    outputs += redactor.finish()
    assert '[REDACTED]' in ''.join(outputs)


def test_fail_closed_on_oversized_candidate():
    result = stream(['a' * 600, ' finish'])
    assert '[REDACTED]' in result
    assert 'a' * 100 not in result


@pytest.mark.asyncio
async def test_sse_wire_split_and_unicode():
    values = ['Hello 🙂 mail bo', 'b@exam', 'ple.com and card 4111 ', '1111 1111 1111.']
    events = [(f'data: {json.dumps({"choices": [{"delta": {"content": value}}]}, ensure_ascii=False)}\n\n').encode('utf-8')
              for value in values]
    chunks = [packet[i:i + 3] for packet in events for i in range(0, len(packet), 3)]
    chunks.extend([b'data: [D', b'ONE]\n\n'])

    async def generate():
        for chunk in chunks:
            yield chunk

    output = b''.join([part async for part in sanitize_sse(generate())]).decode('utf-8')
    assert 'bob@example.com' not in output
    assert '4111' not in output
    assert '[REDACTED]' in output
    assert output.endswith('data: [DONE]\n\n')
    assert '🙂' in output


@pytest.mark.asyncio
async def test_bad_upstream_sse_sanitized():
    async def generate():
        yield b'data: {not valid}\n\n'
    result = b''.join([part async for part in sanitize_sse(generate())]).decode()
    assert 'STREAM_FAILED' in result
    assert 'not valid' not in result


def test_plain_ssn_masked():
    assert stream(['my ssn is 1234', '56789.']) == 'my ssn is [REDACTED].'


def test_oversized_token_does_not_swallow_later_safe_words():
    result = stream(['x' * 600, '; here are safe words.'])
    assert '[REDACTED]' in result
    assert 'here are safe words.' in result


@pytest.mark.asyncio
async def test_stream_proxy_endpoint_integration(monkeypatch):
    from assessment import mocks, task3_stream_gateway as gateway
    actual = httpx.AsyncClient
    monkeypatch.setattr(gateway, 'httpx', SimpleNamespace(
        AsyncClient=lambda *args, **kwargs: actual(transport=httpx.ASGITransport(app=mocks.app),
                                                   base_url='http://provider'),
        HTTPError=httpx.HTTPError, Timeout=httpx.Timeout))
    async with actual(transport=httpx.ASGITransport(app=gateway.app), base_url='http://client') as client:
        response = await client.post('/v1/chat/completions', json={'stream': True, 'messages': []})
    output = response.text
    assert response.status_code == 200
    assert output.endswith('data: [DONE]\n\n')
    assert 'alice@example.com' not in output
    assert '4111' not in output
    assert '123-45-6789' not in output
    assert output.count('[REDACTED]') >= 3
