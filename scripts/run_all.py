"""Start the three HTTP gateway stages and their mock provider on localhost.

Stage 1 is stdio; run it independently with stage1_demo.py, or pytest.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = [
    ('Mock provider/MCP downstream', 'assessment.mocks:app', '8102'),
    ('Stage 2 MCP Security Gateway', 'assessment.task2_gateway:app', '8101'),
    ('Stage 3 Streaming PII Gateway', 'assessment.task3_stream_gateway:app', '8103'),
    ('Stage 4 Rate Limit/Fallback Gateway', 'assessment.task4_router:app', '8104'),
]


def main() -> None:
    key = os.getenv('ASSESSMENT_TOKEN_SECRET')
    if key is None or len(key) < 32:
        raise SystemExit('First set ASSESSMENT_TOKEN_SECRET to a random value with >=32 characters. See README.')
    env = dict(os.environ)
    env['PYTHONPATH'] = str(ROOT / 'src') + os.pathsep + env.get('PYTHONPATH', '')
    env.setdefault('MCP_DOWNSTREAM_URL', 'http://127.0.0.1:8102/mcp')
    env.setdefault('LLM_STREAM_URL', 'http://127.0.0.1:8102/v1/chat/completions')
    env.setdefault('LLM_PRIMARY_URL', 'http://127.0.0.1:8102/primary')
    env.setdefault('LLM_BACKUP_URL', 'http://127.0.0.1:8102/backup')
    env.setdefault('RATE_DB_PATH', str(ROOT / 'data' / 'rate_limits.sqlite3'))

    processes = []
    try:
        for label, app, port in SERVICES:
            proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', app, '--host', '127.0.0.1', '--port', port],
                                    cwd=ROOT, env=env)
            processes.append(proc)
            print(f'{label}: http://127.0.0.1:{port} (pid {proc.pid})', flush=True)
        print('\nRun `python scripts/demo_requests.py` from another terminal with the same secret.', flush=True)
        while all(proc.poll() is None for proc in processes):
            time.sleep(0.2)
        raise RuntimeError('One of the services exited; see log output above')
    except KeyboardInterrupt:
        print('\nStopping local demo services...', flush=True)
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == '__main__':
    main()
