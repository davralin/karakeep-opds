from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    karakeep_base_url: str
    karakeep_api_token: str
    opds_username: str
    opds_password: str
    karakeep_api_path: str = "/api/v1"
    opds_page_size: int = 50
    service_base_url: str = ""
    log_level: str = "INFO"

    @property
    def karakeep_api_base_url(self) -> str:
        return f"{self.karakeep_base_url}{self.karakeep_api_path}"

    @classmethod
    def from_env(cls) -> Settings:
        missing = [
            name
            for name in [
                "KARAKEEP_BASE_URL",
                "KARAKEEP_API_TOKEN",
                "OPDS_USERNAME",
                "OPDS_PASSWORD",
            ]
            if not os.environ.get(name)
        ]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

        page_size_raw = os.environ.get("OPDS_PAGE_SIZE", "50")
        try:
            page_size = int(page_size_raw)
        except ValueError as exc:
            raise RuntimeError("OPDS_PAGE_SIZE must be an integer") from exc
        if page_size < 1 or page_size > 100:
            raise RuntimeError("OPDS_PAGE_SIZE must be between 1 and 100")

        log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
        if log_level not in logging.getLevelNamesMapping():
            raise RuntimeError("LOG_LEVEL must be a valid Python logging level")

        return cls(
            karakeep_base_url=os.environ["KARAKEEP_BASE_URL"].rstrip("/"),
            karakeep_api_token=os.environ["KARAKEEP_API_TOKEN"],
            opds_username=os.environ["OPDS_USERNAME"],
            opds_password=os.environ["OPDS_PASSWORD"],
            karakeep_api_path=_normalize_path(os.environ.get("KARAKEEP_API_PATH", "/api/v1")),
            opds_page_size=page_size,
            service_base_url=os.environ.get("SERVICE_BASE_URL", "").rstrip("/"),
            log_level=log_level,
        )


def _normalize_path(value: str) -> str:
    stripped = value.strip().strip("/")
    if not stripped:
        return ""
    return f"/{stripped}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
