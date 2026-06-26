"""
Configuration management for the agent proxy.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Proxy settings loaded from environment variables."""

    ollama_base_url: str = Field(default="http://127.0.0.1:11434", description="Base URL for Ollama backend")
    proxy_port: int = Field(default=8080, description="Port to run the proxy on")
    sentry_dsn: str = Field(default="", description="Sentry DSN for error reporting")
    log_level: str = Field(default="INFO", description="Logging level")


# Module-level settings instance
settings = Settings()