"""Smoke-test Stage 2, Stage 3 and Stage 4 over local HTTP.

Requires: services launched with `python scripts/run_all.py`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from assessment.auth import create_token


def main() -> None:
    viewer = {'Authorization': f'Bearer {create_token("viewer", "demo-tenant")}' }
    admin = {'Authorization': f'Bearer {create_token("admin", "demo-tenant")}' }
    call = {'jsonrpc': '2.0', 'id': 10, 'method': 'tools/call',
            'params': {'name': 'admin_reset_key', 'arguments': {}}}

    with httpx.Client(timeout=10.0) as client:
        listing = client.post('http://127.0.0.1:8101/mcp',
                              json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, headers=viewer)
        listing.raise_for_status()
        assert listing.json()['result']['tools']
        print('Stage 2: tool listing forwarded successfully')

        blocked = client.post('http://127.0.0.1:8101/mcp', json=call, headers=viewer)
        assert blocked.status_code == 403 and blocked.json()['error']['code'] == -32001
        allowed = client.post('http://127.0.0.1:8101/mcp', json=call, headers=admin)
        assert allowed.status_code == 200
        print('Stage 2: viewer admin call BLOCKED, admin call ALLOWED')

        with client.stream('POST', 'http://127.0.0.1:8103/v1/chat/completions',
                           json={'stream': True, 'messages': [{'role': 'user', 'content': 'Say hello'}]}) as result:
            result.raise_for_status()
            contents = []
            for line in result.iter_lines():
                if line.startswith('data: ') and line != 'data: [DONE]':
                    data = json.loads(line[6:])
                    contents.append(data.get('choices', [{}])[0].get('delta', {}).get('content', ''))
            sanitized = ''.join(contents)
        assert sanitized.count('[REDACTED]') >= 3
        assert 'alice@example.com' not in sanitized and '4111 1111' not in sanitized
        print('Stage 3: streaming email, card and SSN redaction PASSED')
        print('  Sample:', sanitized)

        primary = client.post('http://127.0.0.1:8104/v1/completions',
                              json={'prompt': 'Hello from the test', 'max_tokens': 20}, headers=viewer)
        assert primary.status_code == 200 and primary.json()['provider'] == 'primary', primary.text
        backup = client.post('http://127.0.0.1:8104/v1/completions',
                             json={'prompt': 'Hello again', 'max_tokens': 20, 'model': 'force_429'}, headers=viewer)
        assert backup.status_code == 200 and backup.json()['provider'] == 'backup', backup.text
        print('Stage 4: primary routing PASSED; HTTP 429 fallback PASSED')

    print('\nAll local HTTP smoke tests passed.')


if __name__ == '__main__':
    main()
