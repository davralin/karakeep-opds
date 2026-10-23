from __future__ import annotations

import pytest

from karakeep_opds.config import Settings


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KARAKEEP_BASE_URL", "https://karakeep.example/")
    monkeypatch.setenv("KARAKEEP_API_TOKEN", "token")
    monkeypatch.setenv("OPDS_USERNAME", "user")
    monkeypatch.setenv("OPDS_PASSWORD", "pass")
    monkeypatch.setenv("KARAKEEP_API_PATH", "api/v1/")
    monkeypatch.setenv("OPDS_PAGE_SIZE", "25")
    monkeypatch.setenv("SERVICE_BASE_URL", "https://opds.example/")

    settings = Settings.from_env()

    assert settings.karakeep_base_url == "https://karakeep.example"
    assert settings.karakeep_api_path == "/api/v1"
    assert settings.karakeep_api_base_url == "https://karakeep.example/api/v1"
    assert settings.opds_page_size == 25
    assert settings.service_base_url == "https://opds.example"


def test_settings_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KARAKEEP_BASE_URL", raising=False)
    monkeypatch.delenv("KARAKEEP_API_TOKEN", raising=False)
    monkeypatch.delenv("OPDS_USERNAME", raising=False)
    monkeypatch.delenv("OPDS_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="Missing required"):
        Settings.from_env()


@pytest.mark.parametrize("value", ["0", "101", "invalid"])
def test_settings_rejects_invalid_page_size(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("KARAKEEP_BASE_URL", "https://karakeep.example")
    monkeypatch.setenv("KARAKEEP_API_TOKEN", "token")
    monkeypatch.setenv("OPDS_USERNAME", "user")
    monkeypatch.setenv("OPDS_PASSWORD", "pass")
    monkeypatch.setenv("OPDS_PAGE_SIZE", value)

    with pytest.raises(RuntimeError, match="OPDS_PAGE_SIZE"):
        Settings.from_env()


def test_settings_rejects_invalid_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KARAKEEP_BASE_URL", "https://karakeep.example")
    monkeypatch.setenv("KARAKEEP_API_TOKEN", "token")
    monkeypatch.setenv("OPDS_USERNAME", "user")
    monkeypatch.setenv("OPDS_PASSWORD", "pass")
    monkeypatch.setenv("LOG_LEVEL", "verbose")

    with pytest.raises(RuntimeError, match="LOG_LEVEL"):
        Settings.from_env()
