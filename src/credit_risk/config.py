"""Configuration loading. Paths resolve relative to the repository root."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    """A required configuration value is absent."""


@dataclass(frozen=True)
class Settings:
    root: Path
    pipeline: dict[str, Any]
    contract: dict[str, Any]
    thresholds: dict[str, Any]
    features: dict[str, Any] = field(default_factory=dict)
    findings: dict[str, Any] = field(default_factory=dict)

    def path(self, key: str) -> Path:
        path = self.root / self.pipeline["paths"][key]
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def seed(self) -> int:
        return int(self.pipeline["seed"])

    @property
    def version(self) -> str:
        return str(self.pipeline["processing_version"])


def _load(name: str) -> dict[str, Any]:
    path = ROOT / "configs" / name
    if not path.exists():
        raise ConfigError(f"Missing configuration file: {path}")
    return yaml.safe_load(path.read_text())


def load_settings(root: Path | None = None) -> Settings:
    return Settings(
        root=Path(root) if root else ROOT,
        pipeline=_load("pipeline.yaml"),
        contract=_load("data_contract.yaml"),
        thresholds=_load("risk_thresholds.yaml"),
        features=_load("feature_policy.yaml"),
        findings=_load("findings.yaml"),
    )


def database_url(root: Path | None = None) -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env_file = (root or ROOT) / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    raise ConfigError("DATABASE_URL is not set. Copy .env.example to .env first.")
