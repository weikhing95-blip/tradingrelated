"""The Source contract.

A source knows how to fetch *new* items for a watchlist. "New" is decided by an
``is_seen`` callback the caller supplies (backed by the `seen` table), so the
source itself stays stateless and easy to test against fixtures.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, List

from ..schemas import RawItem, Tier

# Given (source_name, source_item_id) -> already processed?
SeenFn = Callable[[str, str], bool]


class Source(ABC):
    """Base class for all source adapters."""

    name: str
    tier: Tier

    @abstractmethod
    async def fetch_new(self, tickers: List[str], is_seen: SeenFn) -> List[RawItem]:
        """Return items not previously seen, for the given tickers.

        Implementations must filter out anything ``is_seen(self.name, id)``
        already returns True for, so the caller only handles fresh items.
        """
        raise NotImplementedError
