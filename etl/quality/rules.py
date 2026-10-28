"""Per-table DQ expectations. Pipelines import from here and pass to *_load()."""

from __future__ import annotations

from functools import partial

from etl.quality import checks as c

# ops tables themselves: the lightest possible gate.
OPS_ETL_RUN = [partial(c.not_empty, table="ops.etl_run")]
