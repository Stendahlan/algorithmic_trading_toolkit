# Basic Backtest Strat

A simple Nautilus Trader backtest strategy skeleton for verifying end-to-end order flow.

## Overview

Strategy logic is designed to run on **OHLCV** bar data (open, high, low, close, volume). Market data is loaded from JSON files and converted into Nautilus bars before the backtest engine runs.

The strategy may hold a **maximum of one position** at a time.

## Daily cycle

Each trading day the strategy runs through:

1. **One sell cycle** — evaluates whether to exit the current position
2. **One buy cycle** — evaluates whether to open a new position

Sell finishes first; buy runs once selling is done (or if there was nothing to sell).

## Placeholder signals

Buy and sell signals are **placeholders**. They randomly decide when to buy and when to sell so the full order lifecycle can be tested without real alpha logic.

## Files

- `data_loader.py` — loads OHLCV JSON into Nautilus instruments and bars
- `strat_logic.py` — strategy config and daily buy/sell cycle logic
- `run_backtest.py` — wires the backtest engine and runs the strategy
