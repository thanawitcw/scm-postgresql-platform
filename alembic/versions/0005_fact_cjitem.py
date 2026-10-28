"""No-op marker (superseded).

The cj_item grain key this revision introduced is folded directly into the
0004 fact DDL (reshaped with dim_item in 0009). Kept as an empty revision so
the chain (0004 → 0006) stays continuous and the history reads truthfully.
"""

revision = "0005_fact_cjitem"
down_revision = "0004_phase2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
