"""Mag 7 News Bot — source-verified Telegram alerts on the Magnificent Seven.

A long-running service that polls SEC EDGAR (Tier 1) and Finnhub (Tier 2/3) for
material news on a watchlist of large-cap names, deduplicates and classifies it,
and publishes bite-sized, source-linked alerts to a private Telegram channel.
Configuration happens privately in a 1:1 owner DM (the control plane); the
channel is broadcast-only (the publish plane).

See the project README and ``mag7newsbotprd.md`` for the full product spec.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
