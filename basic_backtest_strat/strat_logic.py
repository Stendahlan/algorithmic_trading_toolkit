from decimal import Decimal
import numpy as np
import pandas as pd

from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.datetime import unix_nanos_to_dt
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

from helpers.backtest import get_free_cash, get_positions_equity


class TestStratConfig(StrategyConfig, frozen=True):
    """
    Configuration for :class:`TestStrat`.

    Parameters
    ----------
    instrument_ids : list[InstrumentId]
        Universe of instruments the strategy may trade.
    bar_types : list[BarType]
        One bar type per instrument; used for subscriptions and pricing.
    trade_start : str, optional
        ISO date string when live trading begins. Bars before this are
        treated as warmup. Defaults to ``"2010-01-01"``.
    rng_seed : int, optional
        Seed for the random number generator used by placeholder buy/sell
        signals. Defaults to ``42``.
    """

    instrument_ids: list[InstrumentId]
    bar_types: list[BarType]
    trade_start: str = "2010-01-01"
    rng_seed: int = 42


class TestStrat(Strategy):
    """
    Simple Nautilus backtest strategy skeleton.

    Each day the strategy runs a sell cycle on the first bar, then a buy
    cycle (via signals) once selling is finished. Buy and sell decisions
    currently use random placeholder signals so the order flow is easy to
    verify end-to-end.
    """

    def __init__(self, config: TestStratConfig):
        """
        Initialize the strategy and its random number generator.

        Parameters
        ----------
        config : TestStratConfig
            Frozen strategy configuration.
        """
        super().__init__(config)
        self._rng = np.random.default_rng(config.rng_seed)

    def on_start(self):
        """
        Set up subscriptions and runtime state when the strategy starts.

        Builds an instrument→bar-type map, parses ``trade_start``, and
        subscribes to bars plus the internal lifecycle signals used to
        chain sell and buy cycles.
        """
        self._bar_type_for = {
            bar_type.instrument_id: bar_type for bar_type in self.config.bar_types
        }

        self._trade_start = pd.Timestamp(self.config.trade_start, tz="UTC")

        for bar_type in self.config.bar_types:
            self.subscribe_bars(bar_type)

        self.subscribe_signal(name="SELL_CYCLE_FINISHED_WITH_NO_CHANGES")
        self.subscribe_signal(name="BUY_CYCLE_FINISHED_WITH_NO_CHANGES")

        self.subscribe_signal(name="POSITION_CLOSED")
        self.subscribe_signal(name="POSITION_OPENED")

    def on_bar(
        self,
        bar: Bar
    ):
        """
        Handle each incoming bar and drive the daily sell cycle.

        Trading logic only runs once per day, gated on the last configured
        bar type. Bars before ``trade_start`` are ignored. On the first bar
        of the day (minute == 0) account equity is logged and
        :meth:`sell_cycle` is called.

        Parameters
        ----------
        bar : Bar
            The bar event received from the data engine.
        """
        # strat only runs once per day
        if bar.bar_type != self.config.bar_types[-1]:
            return

        curr_date = unix_nanos_to_dt(bar.ts_event)

        if curr_date < self._trade_start:
            return

        self.log.info(f"Date: {curr_date}")
        # self.log.info(f"{bar.open}, {bar.high}, {bar.low}, {bar.close}")
        if curr_date.minute == 0: # first bar
            cash = get_free_cash(self)
            equity = get_positions_equity(self)

            self.log.info(f"Acc equity: {cash + equity} -> {cash} cash + {equity} worth of positions")

            self.sell_cycle()
        else: # second bar
            self.log.info(f"\n")

    def on_signal(
        self,
        signal,
    ):
        """
        React to internal signals that chain sell and buy cycles.

        After a sell cycle finishes with no changes, starts
        :meth:`buy_cycle`. Other lifecycle signals are acknowledged but
        currently take no further action.

        Parameters
        ----------
        signal
            Published signal object whose type name identifies the event.
        """
        signal_name = type(signal).__name__

        self.log.info(f"ON_SIGNAL: {signal_name}")

        if signal_name in (
            f"Signal{'SELL_CYCLE_FINISHED_WITH_NO_CHANGES'.title()}",
        ):
            self.buy_cycle()

        elif signal_name in (
            f"Signal{'BUY_CYCLE_FINISHED_WITH_NO_CHANGES'.title()}",
            f"Signal{'POSITION_OPENED'.title()}",
            f"Signal{'POSITION_CLOSED'.title()}",
        ):
            pass

    def on_position_opened(self, event):
        """
        Publish a ``POSITION_OPENED`` signal when a position is opened.

        Parameters
        ----------
        event
            Position-opened event from the execution engine.
        """
        self.publish_signal("POSITION_OPENED", value=1)

    def on_position_closed(
        self,
        event
    ):
        """
        Publish a ``POSITION_CLOSED`` signal when a position is closed.

        Parameters
        ----------
        event
            Position-closed event from the execution engine.
        """
        self.publish_signal("POSITION_CLOSED", value=1)

    def on_stop(self):
        """
        Flatten any open positions when the strategy stops.

        Called at the end of a backtest (or when the strategy is stopped
        in live trading).
        """
        positions = self.cache.positions_open(strategy_id=self.id)

        for position in positions:
            self.close_position(position)

        self.log.info("Backtest finished")

    """
    =========================
    Section: Cycles
    =========================
    """
    def sell_cycle(self):
        """
        Evaluate and optionally close the strategy's open position.

        This strategy holds at most one position. If none is open, or if
        :meth:`sell_signal` does not trigger, publishes
        ``SELL_CYCLE_FINISHED_WITH_NO_CHANGES`` so the buy cycle can run.
        """
        positions = self.cache.positions_open(strategy_id=self.id)

        assert len(positions) <= 1, f"{self.id} strat should only have one position"

        if len(positions) == 0:
            self.log.info("SELL CYCLE: no positions open - skipping")
            self.publish_signal("SELL_CYCLE_FINISHED_WITH_NO_CHANGES", value=1)
            return

        position = positions[0]

        sell_signal = self.sell_signal(position)

        if sell_signal:
            price = self.cache.bar(self._bar_type_for[position.instrument_id]).open.as_decimal()
            proceeds = round(position.quantity.as_decimal() * price, 2)
            self.log.info(f"Closing position {position.instrument_id.symbol} for {proceeds}")
            self.close_position(position)
        else:
            self.log.info(f"SELL SIGNAL: not triggered for {position.instrument_id.symbol} - skipping")
            self.publish_signal("SELL_CYCLE_FINISHED_WITH_NO_CHANGES", value=1)

    def buy_cycle(self):
        """
        Open a new long position when a buy signal is available.

        Skips if a position is already open. Otherwise uses
        :meth:`find_buy_signals` and, if any signals exist, submits a
        market buy for the first candidate sized to available free cash.
        Publishes ``BUY_CYCLE_FINISHED_WITH_NO_CHANGES`` when nothing is
        bought.
        """
        positions = self.cache.positions_open(strategy_id=self.id)

        if len(positions) == 1:
            sym = str(positions[0].instrument_id.symbol)
            self.publish_signal("BUY_CYCLE_FINISHED_WITH_NO_CHANGES", value=1)
            return

        buy_signals = self.find_buy_signals()

        if len(buy_signals) > 0:
            instrument_id_to_buy = buy_signals[0]["instrument_id"]

            self.log.info(f"BUY CYCLE: best buy signal is {instrument_id_to_buy.symbol} ")

            free_cash = get_free_cash(self)

            assert free_cash is not None and free_cash.as_decimal() > 0, (f"BUY CYCLE: free cash available = {free_cash}")

            instrument = self.cache.instrument(instrument_id_to_buy)
            price = self.cache.bar(self._bar_type_for[instrument_id_to_buy]).open.as_decimal()
            quantity = instrument.make_qty(free_cash.as_decimal() / price, round_down=True)

            order = self.order_factory.market(
                instrument_id=instrument_id_to_buy,
                order_side=OrderSide.BUY,
                quantity=quantity,
            )

            self.log.info(f"Buying {instrument_id_to_buy.symbol} with {round(quantity.as_decimal() * price, 2)}")

            self.submit_order(order)

        else:
            self.log.info("BUY CYCLE: no buy signals")
            self.publish_signal("BUY_CYCLE_FINISHED_WITH_NO_CHANGES", value=1)

    """
    =========================
    Section: Signals
    =========================
    """

    def sell_signal(
        self,
        position
    ):
        """
        Decide whether to close an open position.

        Placeholder logic: returns ``True`` with a 5% probability so the
        sell path can be exercised in backtests.

        Parameters
        ----------
        position
            The open position being evaluated for exit.

        Returns
        -------
        bool
            ``True`` if the position should be closed, else ``False``.
        """
        position_sym = str(position.instrument_id.symbol)

        sell_signal = self._rng.random() < 0.05

        if sell_signal:
            self.log.info(f"SELL SIGNAL: triggered for {position_sym}")

        return sell_signal

    def find_buy_signals(
        self
    ):
        """
        Scan the universe for instruments that look buyable.

        Placeholder logic: each instrument is selected independently with
        a 5% probability.

        Returns
        -------
        list[dict]
            Candidate buy signals as dicts with an ``instrument_id`` key.
            Empty if no instruments triggered.
        """
        buy_signals = []

        for instrument in self.config.instrument_ids:
            sym = str(instrument.symbol)

            buy_signal = self._rng.random() < 0.05

            if buy_signal:
                buy_signals.append({
                    "instrument_id": instrument
                })

        return buy_signals
