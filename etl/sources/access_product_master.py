"""S8 + S9 — product & supplier master from the Access operational database.

Contract (§5.1.1): explicit filename allowlist (never glob — would pull
back_up_*.accdb), copy to local temp before opening (live .laccdb lock +
~65 MB over the share), read-only, delete the temp copy afterwards.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import unicodedata
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from etl.config import settings
from etl.sources.base import Source

ALLOWLIST = {"SHM_TMS_001_AllUser_New.accdb"}
TABLE_PRODUCT = "tbl_Master_003_Product_List"
TABLE_SUPPLIER = "tbl_Master_004_Supplier_List"

# Source column -> stg snake_case (§4.2). The mapping is the contract: a new
# source column with no entry here FAILS the load, never silently ignored.
PRODUCT_RENAME = {
    "Devision": "division",
    "Buyer_Team": "buyer_team",
    "OEM_Brand": "oem_brand",
    "Non_Vat": "non_vat",
    "Supplier_Code": "supplier_code",
    "SHM_Item": "shm_item",
    "CJ_Item": "cj_item",
    "TD_Item": "td_item",
    "DCM_Item": "dcm_item",
    "CJ_Description": "cj_description",
    "Description": "description",
    "Cat": "cat",
    "Sub_Cat": "sub_cat",
    "Brand": "brand",
    "Group": "item_group",
    "First_SO_Date": "first_so_date",
    "CJ_Status": "cj_status",
    "CJ_Status_as_of": "cj_status_as_of",
    "TD_Status": "td_status",
    "TD_Status_as_of": "td_status_as_of",
    "DCM_Status": "dcm_status",
    "product_shelf_life": "product_shelf_life",
    "PC_Cartons": "pc_cartons",
    "PC_Pack": "pc_pack",
    "Pack_Cartons": "pack_cartons",
    "Buy_Price": "buy_price",
    "Sale Price_DCM": "sale_price_dcm",
    "Sale Price_CJ": "sale_price_cj",
    "TI": "ti",
    "HI": "hi",
    "Cartons_Pallet": "cartons_pallet",
    "ShipFromCode": "ship_from_code",
    "Barcode_Cartons": "barcode_cartons",
    "ctn_Wide_cc": "ctn_wide_cc",
    "ctn_Long_cc": "ctn_long_cc",
    "ctn_High_cc": "ctn_high_cc",
    "GrossWeight_kg": "gross_weight_kg",
    "Barcode_PC": "barcode_pc",
    "Barcode_Pack": "barcode_pack",
    "Country": "country",
    "Transport_OwnerByItem": "transport_owner_by_item",
    "LoadingGroup": "loading_group",
    "LoadingNote": "loading_note",
    "MachineGroup": "machine_group",
    "GroupOfSup": "group_of_sup",
    "Commodities_Flag": "commodities_flag",
    "FC_Item": "fc_item",
    "Base_LT": "base_lt",
    "MOQ_Number": "moq_number",
    "MOQ_Text": "moq_text",
    "MOQ_Group": "moq_group",
    "DC1_LT": "dc1_lt",
    "DC2_LT": "dc2_lt",
    "DC3_LT": "dc3_lt",
    "DC4_LT": "dc4_lt",
    "DC5_LT": "dc5_lt",
    "Business_Group": "business_group",
    "RecNo": "rec_no",
    "ShipFromCondition": "ship_from_condition",
}

SUPPLIER_RENAME = {
    "Supplier_Code": "supplier_code",
    "Supplier Name": "supplier_name",
    "Supplier_Type": "supplier_type",
    "Product": "product",
    "Owner": "owner",
    "Status": "status",
    "Credit Term": "credit_term",
    "Country": "country",
    "Division": "division",
}


def normalise_enum(v) -> str | None:
    """NFKC → trim → upper. Folds case and invisible characters ONLY (§4.3).

    Preserves hyphens, digits, wildcards, and non-Latin script.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = unicodedata.normalize("NFKC", str(v)).strip().upper()
    return s or None


def normalise_status(raw) -> str | None:
    """Same fold as normalise_enum; cancellation test is exact membership (§5.1.1)."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = unicodedata.normalize("NFKC", str(raw))  # NFKC folds NBSP -> space
    s = re.sub(r"\s+", "", s).strip().casefold()
    return s or None


def _resolve_source(filename: str) -> Path:
    if filename not in ALLOWLIST:
        raise ValueError(
            f"{filename!r} is not in the source allowlist {sorted(ALLOWLIST)}; "
            "refusing to open (a glob would pull back_up_*.accdb)"
        )
    return Path(settings.share_root) / "iTMS" / filename


def extract_accdb_table(src: Path, table: str) -> pd.DataFrame:
    """Copy to local temp first (§5.1.1), open read-only, always clean up."""
    import pyodbc

    if table not in (TABLE_PRODUCT, TABLE_SUPPLIER):
        raise ValueError(f"{table!r} is not an in-scope master table (S8/S9 only)")
    tmp = Path(tempfile.gettempdir()) / f"{src.stem}__extract.accdb"
    shutil.copy2(src, tmp)
    try:
        conn = pyodbc.connect(
            f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={tmp};"
        )
        try:
            return pd.read_sql(f"SELECT * FROM [{table}]", conn)
        finally:
            conn.close()
    finally:
        tmp.unlink(missing_ok=True)


def frame_product(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the rename contract + enum normalisation. Unmapped columns fail loudly."""
    unmapped = [c for c in df.columns if c not in PRODUCT_RENAME]
    if unmapped:
        raise ValueError(
            f"S8 schema drift — {len(unmapped)} source column(s) with no rename mapping: "
            f"{unmapped}. Add them to PRODUCT_RENAME, never silently ignore."
        )
    out = df.rename(columns=PRODUCT_RENAME)
    for col in ("division", "item_group", "cj_status", "td_status", "dcm_status"):
        out[col] = out[col].map(normalise_enum)
    for col in ("cj_item", "shm_item", "supplier_code"):
        out[col] = out[col].astype("string").str.strip()
        out[col] = out[col].replace({"": None, "None": None, "<NA>": None})
    return out


def frame_supplier(df: pd.DataFrame) -> pd.DataFrame:
    """Explicit column selection per Rule M7 (drops Supplier_Name dup + Thai col)."""
    missing = [c for c in SUPPLIER_RENAME if c not in df.columns]
    if missing:
        raise ValueError(f"S9 schema drift — expected column(s) missing: {missing}")
    out = df[list(SUPPLIER_RENAME)].rename(columns=SUPPLIER_RENAME)
    for col in ("supplier_type", "status", "division"):
        out[col] = out[col].map(normalise_enum)
    out["supplier_code"] = out["supplier_code"].astype("string").str.strip()
    out = out[out["supplier_code"].notna() & (out["supplier_code"] != "")]
    return out


class AccessProductMaster(Source):
    name = "access_product_master"

    def extract(self, run_date: date):
        src = self._source_path()
        yield ("product", frame_product(extract_accdb_table(src, TABLE_PRODUCT)))
        yield ("supplier", frame_supplier(extract_accdb_table(src, TABLE_SUPPLIER)))

    def _source_path(self) -> Path:
        return _resolve_source("SHM_TMS_001_AllUser_New.accdb")

    def dataset_meta(self) -> dict:
        """uploaded_at is NULL for Access (mtime is meaningless, §10.1)."""
        src = self._source_path()
        return {"file_name": src.name, "file_path": str(src), "uploaded_at": datetime.now()}
