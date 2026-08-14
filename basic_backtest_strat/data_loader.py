import json
from pathlib import Path
import pandas as pd
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AssetClass
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import TokenizedAsset
from nautilus_trader.model.objects import Currency, Price, Quantity
from nautilus_trader.persistence.wranglers import BarDataWrangler

def json_file_to_df(
    path: str
) -> pd.DataFrame:
    """
    Load OHLCV bar data from a JSON file into a pandas DataFrame.

    Expects each record to include a ``date`` field plus open/high/low/close
    prices. The date column is converted to UTC timestamps and used as a
    sorted DatetimeIndex.

    Parameters
    ----------
    path : str
        Path to the JSON file containing OHLCV records.

    Returns
    -------
    pd.DataFrame
        DataFrame indexed by UTC datetime with columns
        ``open``, ``high``, ``low``, and ``close``.
    """
    ohlcv_cols = ["open", "high", "low", "close"]
    
    with open(path) as f:
        records = json.load(f)
    
    df = pd.DataFrame(records)
    # formatting date
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df.set_index("date").sort_index()
    
    return df[ohlcv_cols]

def json_file_to_bars(
    json_path: Path,
    symbol: str,
    venue: str = "XNAS",
) -> tuple:
    """
    Convert a JSON OHLCV file into Nautilus Trader bars.

    Builds a ``TokenizedAsset`` instrument and 1-minute ``BarType`` for the
    given symbol/venue, then wrangles the JSON data into Nautilus ``Bar``
    objects. Because I want to simulate buying at the open of a trading day 
    but I'm using ohlcv data (by default nautilus executes orders on the 
    'close' of a bar), I create two bars for each day: 
        - A bar which represents the open for a day which I can execute trades on
        - The regular ohlc bar

    Parameters
    ----------
    json_path : Path
        Path to the JSON file containing OHLCV records for one symbol.
    symbol : str
        Instrument symbol (e.g. ``"AAPL"`` or ``"BTC"``).
    venue : str, optional
        Venue identifier. Defaults to ``"XNAS"``.

    Returns
    -------
    tuple
        ``(instrument, bar_type, bars)`` where ``bars`` is a list of
        Nautilus ``Bar`` objects sorted by ``ts_event``.
    """
    instrument = TokenizedAsset(
        instrument_id=InstrumentId(symbol=Symbol(symbol), venue=Venue(venue)),
        raw_symbol=Symbol(symbol),
        asset_class=AssetClass.CRYPTOCURRENCY,
        base_currency=Currency.from_str(symbol),
        quote_currency=USD,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        ts_event=0,
        ts_init=0,
    )
    
    bar_type = BarType.from_str(f"{symbol}.{venue}-1-MINUTE-LAST-EXTERNAL")

    open_df = json_file_to_df(json_path)
    ohlc_df = json_file_to_df(json_path).copy()

    open_df.loc[:, ["high", "low", "close"]] = open_df["open"].to_numpy()[:, None]
    ohlc_df.index = ohlc_df.index + pd.Timedelta(minutes=1)

    open_bars = BarDataWrangler(bar_type, instrument).process(open_df)
    ohlc_bars = BarDataWrangler(bar_type, instrument).process(ohlc_df)

    bars = sorted(open_bars + ohlc_bars, key=lambda bar: bar.ts_event)

    return instrument, bar_type, bars

def load_universe_from_directory(
    data_dir: str,
    venue: str = "SIM"
):
    """
    Load a multi-symbol universe from a directory of JSON OHLCV files.

    Each ``*.json`` file is treated as one instrument, with the filename
    stem used as the symbol (e.g. ``AAPL.json`` → ``AAPL``). Files are
    processed in sorted order via :func:`json_file_to_bars`.

    Built to work with the Nautilus Trader framework.

    Parameters
    ----------
    data_dir : str
        Directory containing one JSON OHLCV file per symbol.
    venue : str, optional
        Venue identifier applied to every instrument. Defaults to ``"SIM"``.

    Returns
    -------
    tuple
        ``(instruments, bar_types, all_bars)`` — parallel lists of
        instruments, bar types, and per-symbol bar lists.
    """
    data_dir = Path(data_dir)

    instruments, bar_types, all_bars = [], [], []
    
    for json_path in sorted(data_dir.glob("*.json")):
        symbol = json_path.stem.upper()  # AAPL.json → AAPL
        instrument, bar_type, bars = json_file_to_bars(json_path, symbol, venue)
        instruments.append(instrument)
        bar_types.append(bar_type)
        all_bars.append(bars)
    
    return instruments, bar_types, all_bars

