"""S1+S2 daily-CSV archive, Option C (§5.1.1).

Each promotion grid is now published as one CSV per business date
(`ProGrid_Data_YYYYMMDD.csv`, `ProGrid_PCB_YYYYMMDD.csv`) and the folders
accumulate history. The archive therefore takes the NEWEST file per source
only — business order from the filename, never mtime — and the load reads
that archived copy, never the live file. Order: select → copy → validate →
promote atomically → manifest.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from etl.sources import csv_promotion_grid, csv_promotion_grid_pcb

SOURCES = {"S1": csv_promotion_grid, "S2": csv_promotion_grid_pcb}

# Sanity bounds for the validated copy (a mid-save torn copy is worse than none).
ROW_BOUNDS = {"S1": (5_000, 20_000), "S2": (1_000, 10_000)}


def snapshot_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "ops" / "snapshot"


def select_latest() -> dict[str, Path]:
    """Newest CSV per source, by the YYYYMMDD in the filename."""
    return {key: module.latest_file() for key, module in SOURCES.items()}


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def archive_upload(paths: dict[str, Path] | None = None) -> dict:
    """Select newest → copy → validate → promote. Returns manifest.

    Aborts before promotion on failure. Pass `paths` when the caller already
    selected the files (so a refresh mid-run cannot swap the version).
    """
    selected = paths or select_latest()
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    staging = snapshot_root() / f"{stamp}.partial"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        copied = {}
        for key, src in selected.items():
            if not src.exists():
                raise FileNotFoundError(f"source file missing: {src}")
            dst = staging / src.name
            shutil.copy2(src, dst)
            copied[key] = dst
        manifest = _validate_and_manifest(staging, copied, stamp)
        final = snapshot_root() / stamp
        staging.rename(final)  # atomic promotion (same volume)
        (final / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        _prune_snapshots(keep=30)  # owner ruling 2026-10-06: 30 snapshots max
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _prune_snapshots(keep: int = 30) -> None:
    """Delete oldest snapshot dirs beyond `keep` (stamp names sort chronologically).

    Never touches `<stamp>.partial` staging dirs or the just-promoted snapshot
    except by age order — and the just-promoted one is always newest.
    """
    root = snapshot_root()
    snaps = sorted(p for p in root.iterdir()
                   if p.is_dir() and not p.name.endswith(".partial"))
    for old in snaps[:-keep] if len(snaps) > keep else []:
        shutil.rmtree(old, ignore_errors=True)


def _validate_and_manifest(staging: Path, copied: dict, stamp: str) -> dict:
    files = {}
    for key, path in copied.items():
        module = SOURCES[key]
        df = module.read_file(path)  # strict: headers, dates, prices
        n = len(df)
        lo, hi = ROW_BOUNDS[key]
        if not (lo <= n <= hi):
            raise ValueError(
                f"{path.name}: {n} rows outside historical bounds [{lo},{hi}] — "
                "caught mid-save? Aborting before promotion.")
        files[key] = {
            "file_name": path.name,
            "rows": n,
            "sha256": file_hash(path),
            "mtime": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        }
    return {"uploaded_at": stamp, "snapshot_dir": f"ops/snapshot/{stamp}",
            "source_key": "S1+S2", "files": files}
