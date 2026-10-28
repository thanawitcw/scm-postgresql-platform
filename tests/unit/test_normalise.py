"""Unit tests for §4.3 enum folding + the §5.1.1 Status trap.

These encode the exact failure modes measured in the source files:
- `Mail`/`mail` variants must fold (143 rows lost their tie-break otherwise)
- `7-DAYS_*` must stay distinct from `7-DAYS` (wildcard, §15)
- Thai-script values must pass through untouched
- `'\\xa0cancel'` (NBSP) must classify as cancelled; everything else active
"""

import pandas as pd

from etl.sources.access_product_master import normalise_enum, normalise_status


def test_enum_folds_case_only():
    assert normalise_enum("Mail") == "MAIL"
    assert normalise_enum("7-Days") == "7-DAYS"
    assert normalise_enum("other") == "OTHER"
    assert normalise_enum("pack") == "PACK"


def test_enum_preserves_business_spelling():
    assert normalise_enum("7-Days_*") == "7-DAYS_*"
    assert normalise_enum("7-Days_*") != "7-DAYS"
    assert normalise_enum("กระชาก") == "กระชาก"


def test_enum_nulls():
    assert normalise_enum(None) is None
    assert normalise_enum(float("nan")) is None
    assert normalise_enum("   ") is None


def test_status_nbsp_trap():
    assert normalise_status("Cancel") == "cancel"
    assert normalise_enum("cancel") == "CANCEL"
    assert normalise_status(" cancel") == "cancel"
    assert normalise_status("Revised1") == "revised1"  # active, not cancelled
    assert normalise_status(0) == "0"
    assert normalise_status(None) is None


def test_cancellation_is_exact_membership():
    cancelled = {normalise_status(v) for v in ["Cancel", "cancel", " cancel"]}
    assert cancelled == {"cancel"}
    for active in ["Revised1", "New", "adhoc", 0, None, " "]:
        assert normalise_status(active) != "cancel"


def test_frame_rejects_unmapped_columns():
    from etl.sources.access_product_master import frame_supplier

    df = pd.DataFrame({"Supplier_Code": ["S1"], "Brand_New_Col": [1]})
    try:
        frame_supplier(df)
    except ValueError as e:
        assert "schema drift" in str(e)
    else:
        raise AssertionError("unmapped column did not fail the load")
