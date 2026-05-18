"""Capability memory — persisted versioned definitions per capability slug.

Layout on disk:
    ~/.t2v_promptgen/memory/
    ├── capabilities/
    │   ├── human_hand/
    │   │   ├── v1__2026-05-14__abc123.yaml
    │   │   ├── v2__2026-05-20__def456.yaml
    │   │   └── latest.lnk        (text file: "v2__...yaml")
    │   └── ...
    └── index.json                (slug → meta lookup)

Slug standardization: enforce snake_case ASCII. Fuzzy-match incoming
free-form names against existing slugs and prompt the user when there's a
near-collision (e.g. "人手" vs existing "human_hand").
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..core.schema import CapabilityVersion

DEFAULT_MEMORY_ROOT = Path.home() / ".t2v_promptgen" / "memory"


def slug_for(free_form: str, existing_slugs: list[str] | None = None) -> tuple[str, list[str]]:
    """Normalize a free-form capability name to a canonical slug.

    Returns: (proposed_slug, near_collision_slugs).
    If near_collision_slugs is non-empty, CLI should ask the user to either
    pick an existing slug or confirm creating a new one.

    Examples:
        ('人手生成', []) → ('human_hand', [])
        ('Camera Motion', []) → ('camera_motion', [])
        ('手部表现', ['human_hand']) → ('hand_performance', ['human_hand'])
    """
    raise NotImplementedError


def list_capabilities(root: Path = DEFAULT_MEMORY_ROOT) -> list[str]:
    """Return all capability slugs that have ≥1 stored version."""
    raise NotImplementedError


def list_versions(slug: str, root: Path = DEFAULT_MEMORY_ROOT) -> list[int]:
    """Return all version numbers for a slug, ascending."""
    raise NotImplementedError


def load(slug: str, version: int | None = None,
         root: Path = DEFAULT_MEMORY_ROOT) -> CapabilityVersion:
    """Load a CapabilityVersion. version=None means latest."""
    raise NotImplementedError


def save(cap: CapabilityVersion, root: Path = DEFAULT_MEMORY_ROOT) -> Path:
    """Write a CapabilityVersion as YAML, update latest.lnk and index.json.

    The version field on `cap` must equal max(existing_versions) + 1 or 1.
    Returns the path written.
    """
    raise NotImplementedError


def export_archive(slug: str, to: Path, root: Path = DEFAULT_MEMORY_ROOT) -> Path:
    """Bundle all versions of a slug + its seed pool into a tar.gz for sharing."""
    raise NotImplementedError


def import_archive(archive: Path, root: Path = DEFAULT_MEMORY_ROOT) -> str:
    """Restore a previously exported capability bundle. Returns the slug installed."""
    raise NotImplementedError
