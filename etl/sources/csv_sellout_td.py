"""S4 — TD sellout. Same shape as S3 except Product_Name is replaced by
a constant Sales Channel column (byte-compatible otherwise, §5.1.1)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from etl.config import settings
from etl.sources.base import Source
from etl.sources.sellout_common import read_sellout

EXPECTED = ["Date", "CJ_Item", "Sales Channel", "Promotion_Type", "Promotion_Theme",
            "ProPrice In.Vat", "Total_SO_Qty", "Total_SO_Amt (In.Vat)",
            "Main_Promotion"]


def source_dir() -> Path:
    return (Path(settings.share_root) / "Back_Office_SCM" / "Data for PowerBI"
            / "TD_SellOut")


class CsvSelloutTd(Source):
    name = "csv_sellout_td"

    def extract(self, run_date: date):
        raise NotImplementedError("sellout loads by month file; use load_month_file()")

    def load_month_file(self, path: Path):
        df, uploaded_at = read_sellout(path, EXPECTED, "TD",
                                       channel_col="Sales Channel")
        df["product_name"] = None
        return df, uploaded_at
