"""Stable, reviewable runtime configuration.

Secrets intentionally do not belong here.  A provider credential, if a later
runtime needs one, is read from ``.env`` by the runtime adapter and is never
committed.  The model identifier is kept in source as required by the lab.
"""

from dataclasses import dataclass
from pathlib import Path


POLICY_VERSION = "EC_POLICY_V2"
MODEL_CONFIG = {
    "provider": "openai",
    "name": "gpt-4o-mini",
    # Supplied by the project owner; OpenAI does not publish this figure publicly.
    "parameter_size_billion": 8,
    "parameter_size_source": "project-owner assertion; not publicly disclosed by OpenAI",
    "max_parameter_size_billion": 10,
}


@dataclass(frozen=True)
class ProjectPaths:
    """Absolute project paths derived from the repository root."""

    root: Path

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def input_dir(self) -> Path:
        return self.root / "input"

    @property
    def output_dir(self) -> Path:
        return self.root / "output"

    @property
    def logging_dir(self) -> Path:
        return self.root / "logging"


def project_paths(root: Path | None = None) -> ProjectPaths:
    """Return paths for ``root`` or for the repository containing this file."""

    repository_root = root or Path(__file__).resolve().parents[2]
    return ProjectPaths(repository_root.resolve())
