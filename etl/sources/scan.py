"""Share-scan rule, one place (§14): Excel owner lock files are transient.

A file whose name starts with ``~$`` is an owner lock left while somebody
has the workbook open — never data, never a drift signal. Every scan in
this project goes through :func:`iter_data_files`, which excludes them.
Anything else unexpected is still a loud failure at the call site.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

OWNER_LOCK_PREFIX = "~$"


def is_owner_lock(path: Path) -> bool:
    return path.name.startswith(OWNER_LOCK_PREFIX)


def iter_data_files(directory: Path, pattern: str) -> Iterator[Path]:
    """Sorted data files under directory (non-recursive), minus owner locks."""
    for p in sorted(directory.glob(pattern)):
        if p.is_file() and not is_owner_lock(p):
            yield p
