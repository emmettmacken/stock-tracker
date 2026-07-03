"""Production strategy configuration — the single source the live trader imports.

PRODUCTION_CONFIG is the canonical baseline. The live signal job, stop-loss job,
adaptive-threshold job, position sizing, and the factor engine all read from it (directly
for pure-Python constants, or as the default behind a DB-overridable key via ``resolve``).
"""

from __future__ import annotations

from .base import StrategyConfig

PRODUCTION_CONFIG = StrategyConfig()
