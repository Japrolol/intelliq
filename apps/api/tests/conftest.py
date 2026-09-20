"""Never let an engineer's local provider keys turn unit tests into paid live calls."""

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime_environment(monkeypatch):
    for name, value in {
        "JOBS_ENABLED": "false",
        "SESSION_ENCRYPTION_KEY": "",
        "ALLOW_EXTERNAL_TEXT_PROCESSING": "false",
        "LLM_API_KEY": "",
        "OPENAI_API_KEY": "",
        "WEATHERAPI_API_KEY": "",
        "OPENROUTESERVICE_API_KEY": "",
    }.items():
        monkeypatch.setenv(name, value)
