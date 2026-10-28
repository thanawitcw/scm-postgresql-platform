"""S3 — CJ sellout. Monthly files, Product_Name instead of a channel column."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from etl.config import settings
from etl.sources.base import Source
from etl.sources.sellout_common import read_sellout

EXPECTED = ["Date", "CJ_Item", "Product_Name", "Promotion_Type", "Promotion_Theme",
            "ProPrice In.Vat", "Total_SO_Qty", "Total_SO_Amt (In.Vat)",
            "Main_Promotion"]


def source_dir() -> Path:
    return (Path(settings.share_root) / "Back_Office_SCM" / "Data for PowerBI"
            / "CJ_Sellout_with_promotion_name")


class CsvSellout(Source):
    name = "csv_sellout"

    def extract(self, run_date: date):
        raise NotImplementedError("sellout loads by month file; use load_month_file()")

    def load_month_file(self, path: Path):
        df, uploaded_at = read_sellout(path, EXPECTED, "CJ")
        df["product_name"] = df["Product_Name"].astype("string")
        return df.drop(columns=["Product_Name"]), uploaded_at
