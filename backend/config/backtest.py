"""Backtest strategy configuration — a copy of PRODUCTION_CONFIG that experiments may
override WITHOUT touching production.

To run an experiment, override specific fields with ``dataclasses.replace`` at the
sub-config level, e.g.::

    from dataclasses import replace
    BACKTEST_CONFIG = replace(
        PRODUCTION_CONFIG,
        gates=replace(PRODUCTION_CONFIG.gates, vix_max=25.0),
    )

Currently there are NO overrides: BACKTEST_CONFIG is field-for-field identical to
PRODUCTION_CONFIG, which is what keeps the pinned backtest baseline bit-identical after the
config-consolidation refactor.

Caveat for DB-overridable keys: an override here on a key that is also pinned in the
``system_config`` table is IGNORED in favor of the DB value at resolution time; ``resolve``
logs a warning when that happens so it is never silent.
"""

from __future__ import annotations

from .live import PRODUCTION_CONFIG

# No experiment overrides — identical to production. Replace fields here to experiment.
BACKTEST_CONFIG = PRODUCTION_CONFIG
