import pytest


@pytest.fixture(autouse=True)
def assessment_secret(monkeypatch):
    monkeypatch.setenv('ASSESSMENT_TOKEN_SECRET', 'local-testing-secret-with-at-least-32-bytes-123')
