"""Lineage helpers: checksums, JSON result files and machine facts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import ROOT


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_state(root: Path = ROOT) -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", *args], cwd=root, capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    status = run("status", "--porcelain")
    return {
        "commit": run("rev-parse", "HEAD"),
        "dirty": bool(status) if status is not None else None,
    }


def machine_facts() -> dict[str, Any]:
    memory = int(
        subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout or 0
    )
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "memory_gb": round(memory / 1024**3, 1),
        "python": platform.python_version(),
    }


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):
        return _clean(value.item())
    return value


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(payload), indent=2, sort_keys=True, default=str) + "\n")
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


@dataclass
class Issue:
    issue_id: str
    check_name: str
    severity: str
    affected_variables: str
    affected_count: int
    denominator: int
    description: str
    proposed_fix: str
    status: str = "OPEN"
    evidence_type: str = "real_source"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
