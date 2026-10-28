"""Source ABC. One module per source system implements extract() (R8)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import date

import pandas as pd


class Source(ABC):
    name: str

    @abstractmethod
    def extract(self, run_date: date) -> Iterator[pd.DataFrame]:
        """Yield DataFrame chunks. Chunked so staging is 'chunks not rows' (§3)."""
        ...
