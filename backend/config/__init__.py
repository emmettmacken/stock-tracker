"""Versioned strategy configuration package.

Public surface:
  * StrategyConfig / sub-config dataclasses  — the schema (see base.py)
  * PRODUCTION_CONFIG                          — live baseline (live.py)
  * BACKTEST_CONFIG                            — experiment config (backtest.py)
  * resolve(key, value, baseline=...)          — DB-overridable key resolution
"""

from __future__ import annotations

import logging

import database as db

from .base import (
    DEFAULT_FACTOR_WEIGHTS,
    StrategyConfig,
    Thresholds,
    Sizing,
    Gates,
    HMM,
    Factors,
    Adaptive,
    Backtest,
)
from .live import PRODUCTION_CONFIG
from .backtest import BACKTEST_CONFIG

logger = logging.getLogger("uvicorn.error")

__all__ = [
    "DEFAULT_FACTOR_WEIGHTS",
    "StrategyConfig",
    "Thresholds",
    "Sizing",
    "Gates",
    "HMM",
    "Factors",
    "Adaptive",
    "Backtest",
    "PRODUCTION_CONFIG",
    "BACKTEST_CONFIG",
    "resolve",
]


def resolve(key: str, value, baseline=None) -> str:
    """Resolve a DB-overridable strategy key, returning a string (callers cast).

    The ``system_config`` DB value wins when present, so runtime tuning (and the
    adaptive-threshold job's writes) still take effect; ``value`` is the config-supplied
    default used only when the key isn't stored.

    ``baseline`` is the PRODUCTION_CONFIG value for the same key. When a backtest resolves
    a key whose config value differs from ``baseline`` (i.e. an experiment override is in
    play) but the DB pins the key, the DB value shadows the override — we return it and log
    a warning so the ignored override is never silent. On the live path ``baseline`` is
    left None (production can't shadow itself), so no warning is emitted.
    """
    stored = db.get_config_opt(key)
    if stored is not None:
        if baseline is not None and value != baseline:
            logger.warning(
                "[config] backtest override %s=%r ignored — system_config pins %s=%r",
                key, value, key, stored,
            )
        return stored
    return str(value)
