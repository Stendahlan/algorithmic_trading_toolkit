from __future__ import annotations

import sys
from pathlib import Path

STRATEGY_DIR = Path(__file__).resolve().parent
if str(STRATEGY_DIR) not in sys.path:
    sys.path.insert(0, str(STRATEGY_DIR))

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import CacheConfig, LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money

from data_loader import load_universe_from_directory
from strat_logic import TestStrat, TestStratConfig

VENUE = "SIM"


def main() -> None:
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id="BACKTESTER-001",
            logging=LoggingConfig(log_level="INFO"),
            cache=CacheConfig(bar_capacity=1_000_000),
        )
    )

    engine.add_venue(
        venue=Venue(VENUE),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=USD,
        starting_balances=[Money(100_000, USD)],
    )

    instruments, bar_types, all_bars = load_universe_from_directory(
        str(STRATEGY_DIR / "json_data"), venue=VENUE
    )

    for instrument, bars in zip(instruments, all_bars):
        engine.add_instrument(instrument)
        engine.add_data(bars)

    config = TestStratConfig(
        instrument_ids=[instrument.id for instrument in instruments],
        bar_types=bar_types,
        trade_start="2022-01-01",
    )

    engine.add_strategy(TestStrat(config=config))

    engine.run(
        end="2026-01-01",
    )

    print(engine.trader.generate_account_report(Venue(VENUE)))
    print(engine.trader.generate_order_fills_report())
    print(engine.trader.generate_positions_report())

    engine.reset()
    engine.dispose()


if __name__ == "__main__":
    main()
