"""Configuration for the ARB Bot agent stack."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(os.getenv("DOTENV_PATH", ".env.local"), override=False)
load_dotenv(".env", override=False)

# Also load the repo-root .env (one level above back-end/) so commands run
# from back-end/ pick up provisioning outputs without needing a CWD-local copy.
_REPO_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
if _REPO_ROOT_ENV.is_file():
    load_dotenv(_REPO_ROOT_ENV, override=False)


class ConfigError(RuntimeError):
    """Raised when required env vars are missing."""


def _require(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise ConfigError(
            f"Missing required environment variable: {name}. "
            "Run back-end/infra/provision.py and copy .env.example to .env.local."
        )
    return val


@dataclass
class Config:
    """Runtime config loaded from environment variables."""

    foundry_project_endpoint: str = field(
        default_factory=lambda: os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
    )
    foundry_model_deployment: str = field(
        default_factory=lambda: os.getenv("FOUNDRY_MODEL_DEPLOYMENT", "gpt-5.4-pro")
    )
    validate_agent_name: str = field(
        default_factory=lambda: os.getenv("VALIDATE_AGENT_NAME", "ValidateArbAgent")
    )
    iac_agent_name: str = field(
        default_factory=lambda: os.getenv("IAC_AGENT_NAME", "IacGeneratorAgent")
    )
    azure_search_endpoint: str = field(
        default_factory=lambda: os.getenv("AZURE_SEARCH_ENDPOINT", "")
    )
    azure_search_index: str = field(
        default_factory=lambda: os.getenv("AZURE_SEARCH_INDEX", "arb-policies")
    )
    foundry_search_connection_id: str = field(
        default_factory=lambda: os.getenv("FOUNDRY_SEARCH_CONNECTION_ID", "")
    )
    timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("WORKFLOW_TIMEOUT_SECONDS", "60"))
    )
    retry_count: int = field(
        default_factory=lambda: int(os.getenv("RETRY_COUNT", "3"))
    )
    retry_base_delay: float = field(
        default_factory=lambda: float(os.getenv("RETRY_BASE_DELAY", "1.0"))
    )
    circuit_breaker_threshold: int = field(
        default_factory=lambda: int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "3"))
    )
    circuit_breaker_recovery_seconds: float = field(
        default_factory=lambda: float(os.getenv("CIRCUIT_BREAKER_RECOVERY_SECONDS", "30.0"))
    )
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )

    def require_runtime(self) -> None:
        """Validate the fields needed to talk to Foundry."""
        if not self.foundry_project_endpoint:
            _require("FOUNDRY_PROJECT_ENDPOINT")
        if not self.foundry_model_deployment:
            _require("FOUNDRY_MODEL_DEPLOYMENT")
