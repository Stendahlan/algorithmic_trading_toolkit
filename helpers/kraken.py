import base64
import hashlib
import hmac
import os
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import requests
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

from helpers.reconciliation import calc_info_for_all_positions_in_strategy

"""
=========================
Section: General
=========================
"""
def _kraken_private_request(
    path: str,
    api_key: str,
    api_secret: str,
    data: dict | None = None,
    _retries: int = 3,
) -> dict:
    """Signed POST to a Kraken private REST endpoint, returns the 'result' dict."""
    KRAKEN_API_URL = "https://api.kraken.com"

    data = dict(data or {})
    # Nanosecond nonce: the Nautilus nodes use ns nonces on these same keys, so
    # the keys' nonce high-water marks are far above millisecond timestamps.
    data["nonce"] = str(time.time_ns())

    post_data = urllib.parse.urlencode(data)
    encoded = (data["nonce"] + post_data).encode()
    message = path.encode() + hashlib.sha256(encoded).digest()
    signature = hmac.new(base64.b64decode(api_secret), message, hashlib.sha512)

    response = requests.post(
        f"{KRAKEN_API_URL}{path}",
        headers={
            "API-Key": api_key,
            "API-Sign": base64.b64encode(signature.digest()).decode(),
        },
        data=data,
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()
    if payload.get("error"):
        # A live trading node sharing this API key can win a nonce race;
        # retry with a fresh (higher) nonce.
        if "EAPI:Invalid nonce" in payload["error"] and _retries > 0:
            time.sleep(1.0)
            return _kraken_private_request(path, api_key, api_secret, data, _retries - 1)
        raise RuntimeError(f"Kraken API error for {path}: {payload['error']}")

    return payload["result"]

def get_open_orders_for_kraken_strategy(
    strategy: Strategy,
) -> list[dict]:
    """
    Currently-open Kraken orders that belong to this strategy.

    Queries /0/private/OpenOrders rather than the Nautilus cache. Strategy
    ownership uses the same cl_ord_id tag as filter_kraken_orders_for_specific_strat
    (O-{HHMMSS}-{trader}-{order_id_tag}-{count}).
    """
    open_result = _kraken_private_request(
        "/0/private/OpenOrders",
        os.getenv(strategy.config.api_key_env_name),
        os.getenv(strategy.config.api_secret_env_name),
    )

    ret = []

    for txid, order in open_result.get("open", {}).items():
        cl_ord_id = order.get("cl_ord_id")
        if not cl_ord_id:
            if not getattr(strategy.config, "claim_untagged_venue_order_ids", False):
                continue
        else:
            parts = cl_ord_id.split("-")
            if not (len(parts) >= 2 and parts[-2] == strategy.config.order_id_tag):
                continue

        tagged = dict(order)
        tagged["txid"] = txid
        descr = dict(order.get("descr", {}))
        descr["pair"] = _format_kraken_pair(descr.get("pair"))
        tagged["descr"] = descr

        ret.append(tagged)

    return ret

"""
=========================
Section: Order execution
=========================
"""
def kraken_crypto_limit_buy(
    strategy: Strategy,
    instrument_id: InstrumentId,
    notional: float,
    notional_buffer: float,
):
    """
    Submit a limit BUY sized from ``notional`` USD, converted to base quantity.

    Parameters
    ----------
    notional_buffer : float
        Fraction of ``notional`` to actually spend. Leaves headroom for the
        maker/taker fee (charged on top of notional by Kraken) and balance drift.
    """
    limit_buffer = Decimal("0.004")

    tick = strategy.cache.quote_tick(instrument_id)

    if tick is not None:
        # plan A for getting curr price
        curr_price = tick.extract_price(PriceType.MID).as_decimal()
    else:
        # plan B for getting curr price
        curr_price = Decimal(str(strategy.latest_minute_bar_data[str(instrument_id.symbol)]["close"]))

    buffered_notional = round(notional * notional_buffer, 2)
    
    limit_price = curr_price * (Decimal("1") + limit_buffer)
    quantity = Decimal(str(buffered_notional)) / limit_price

    instrument = strategy.cache.instrument(instrument_id)
    
    client_order_id = f"{uuid.uuid4().hex[:12]}-{strategy.config.order_id_tag}-0"

    data = {
        "ordertype": "limit",
        "type": "buy",
        "pair": str(instrument_id.symbol).replace("/", "").replace("BTC", "XBT", 1),
        "volume": str(quantity),
        "price": str(instrument.make_price(limit_price)),
        "timeinforce": "GTC",
        "cl_ord_id": client_order_id,
    }

    return _kraken_private_request(
        "/0/private/AddOrder",
        os.getenv(strategy.config.api_key_env_name),
        os.getenv(strategy.config.api_secret_env_name),
        data=data,
    )

def kraken_crypto_stop_limit_order(
    strategy: Strategy,
    instrument_id: InstrumentId,
    quantity,
    trigger_price,
    limit_price,
):
    """
    Submit a stop-loss-limit SELL: when price falls to ``trigger_price``,
    a limit sell at ``limit_price`` is placed for ``quantity``.

    On Kraken's AddOrder, ``price`` is the trigger and ``price2`` the limit -
    the same fields read back as descr.price / descr.price2 by
    get_open_orders_for_kraken_strategy and amended by
    amend_kraken_stop_limit_order.
    """
    instrument = strategy.cache.instrument(instrument_id)

    client_order_id = f"{uuid.uuid4().hex[:12]}-{strategy.config.order_id_tag}-0"

    data = {
        "ordertype": "stop-loss-limit",
        "type": "sell",
        "pair": str(instrument_id.symbol).replace("/", "").replace("BTC", "XBT", 1),
        "volume": str(quantity),
        "price": str(instrument.make_price(trigger_price)),
        "price2": str(instrument.make_price(limit_price)),
        "timeinforce": "GTC",
        "cl_ord_id": client_order_id,
    }

    return _kraken_private_request(
        "/0/private/AddOrder",
        os.getenv(strategy.config.api_key_env_name),
        os.getenv(strategy.config.api_secret_env_name),
        data=data,
    )

def amend_kraken_stop_limit_order(
    strategy: Strategy,
    order: dict,
    trigger_price,
    limit_price,
) -> dict:
    pair = order["descr"]["pair"]
    instrument = strategy.cache.instrument(InstrumentId.from_str(f"{pair}.KRAKEN"))

    data = {
        "txid": order["txid"],
        "trigger_price": str(instrument.make_price(trigger_price)),
        "limit_price": str(instrument.make_price(limit_price)),
    }

    return _kraken_private_request(
        "/0/private/AmendOrder",
        os.getenv(strategy.config.api_key_env_name),
        os.getenv(strategy.config.api_secret_env_name),
        data=data,
    )

"""
=========================
Section: Reconciliation
=========================
"""

def get_all_raw_orders_from_specific_kraken_account(
    api_key: str,
    api_secret: str,
) -> dict[str, dict]:
    """
    Fetch every order (open and closed) on a Kraken account.
    """
    _PAGE_SLEEP_SECS = 1.0
    
    orders: dict[str, dict] = {}

    # Open orders (single call, not paginated).
    open_result = _kraken_private_request(
        "/0/private/OpenOrders",
        api_key,
        api_secret,
        data={"trades": True},
    )
    orders.update(open_result.get("open", {}))

    # Closed orders, paginated by offset until Kraken's reported count is reached.
    offset = 0
    while True:
        time.sleep(_PAGE_SLEEP_SECS)
        closed_result = _kraken_private_request(
            "/0/private/ClosedOrders",
            api_key,
            api_secret,
            data={"trades": True, "ofs": offset},
        )
        page = closed_result.get("closed", {})
        orders.update(page)

        offset += len(page)
        if not page or offset >= int(closed_result.get("count", 0)):
            break

    return orders

def get_all_ledger_entries_from_specific_kraken_account(
    api_key: str,
    api_secret: str,
) -> dict[str, list[dict]]:
    """
    Fetch every ledger entry on a Kraken account, grouped by refid.
    """
    _PAGE_SLEEP_SECS = 1.0

    entries_by_refid: dict[str, list[dict]] = {}

    offset = 0
    while True:
        result = _kraken_private_request(
            "/0/private/Ledgers",
            api_key,
            api_secret,
            data={"ofs": offset},
        )
        page = result.get("ledger", {})
        for entry in page.values():
            entries_by_refid.setdefault(entry["refid"], []).append(entry)

        offset += len(page)
        if not page or offset >= int(result.get("count", 0)):
            break
        time.sleep(_PAGE_SLEEP_SECS)

    return entries_by_refid

def filter_kraken_orders_for_specific_strat(
    strategy_tag: str,
    raw_kraken_orders,
    strategy_start_date: datetime,
    claim_untagged_orders: bool = False,
):
    """
    Keep filled orders that belong to one strategy.

    Orders with no cl_ord_id (placed manually or before tagging existed) are
    included only when claim_untagged_orders is True (see Strat 1's
    claim_untagged_venue_order_ids config).

    Orders whose fill date is earlier than strategy_start_date are dropped.
    Naive start dates are treated as UTC midnight.
    """
    ret = []
    if strategy_start_date.tzinfo is None:
        strategy_start_date = strategy_start_date.replace(tzinfo=timezone.utc)
    start_ts = strategy_start_date.timestamp()

    for order in raw_kraken_orders.values():
        if float(order.get("vol_exec") or 0) == 0:
            continue
        fill_date = float(order.get("closetm") or order.get("opentm") or 0)
        if fill_date < start_ts:
            continue

        cl_ord_id = order.get("cl_ord_id")
        if not cl_ord_id:
            if claim_untagged_orders:
                ret.append(order)
            continue

        parts = cl_ord_id.split("-")
        if len(parts) >= 2 and parts[-2] == strategy_tag:
            ret.append(order)

    return ret

def format_kraken_orders(
    raw_kraken_orders,
    ledgers_by_refid: dict[str, list[dict]],
):
    """
    Convert filtered Kraken orders into the dicts that
    calc_info_for_all_positions_in_strategy expects.
    """
    ret = []

    for order in raw_kraken_orders:
        descr = order.get("descr", {})
        symbol = descr.get("pair")
        side = descr.get("type")
        if side not in ("buy", "sell"):
            continue

        formatted = {
            "symbol": _format_kraken_pair(symbol),
            "side": side,
            # closetm is when the order finished filling; opentm covers
            # partially-filled orders that are still open.
            "fill_date": float(order.get("closetm") or order.get("opentm") or 0),
            # top-level price is Kraken's average fill; descr.price is the limit
            "avg_fill_price": float(order.get("price")),
        }

        # Net balance changes from the ledger: fees land on whichever
        # asset Kraken actually took them from.
        cash_delta = 0.0
        base_delta = 0.0
        ledger_entries_found = False
        for trade_txid in order.get("trades") or []:
            for entry in ledgers_by_refid.get(trade_txid, []):
                ledger_entries_found = True
                net = float(entry["amount"]) - float(entry["fee"])
                if entry["asset"] in ("ZUSD", "USD"):
                    cash_delta += net
                else:
                    base_delta += net

        if not ledger_entries_found:
            raise RuntimeError(
                f"No ledger entries found for order {order.get('descr', {}).get('order')} "
                f"(trades={order.get('trades')}); cannot reconcile without them"
            )

        if side == "buy":
            formatted["total_cash_spent"] = -cash_delta
            formatted["total_quantity_bought"] = base_delta
        else:
            formatted["total_cash_received"] = cash_delta
            formatted["total_quantity_sold"] = -base_delta

        ret.append(formatted)

    return ret

def get_all_position_quantities_for_kraken_strategy(
    strategy: Strategy
):
    raw_orders = get_all_raw_orders_from_specific_kraken_account(
        os.getenv(strategy.config.api_key_env_name),
        os.getenv(strategy.config.api_secret_env_name),
    )

    ledgers_by_refid = get_all_ledger_entries_from_specific_kraken_account(
        os.getenv(strategy.config.api_key_env_name),
        os.getenv(strategy.config.api_secret_env_name),
    )

    strat_orders = filter_kraken_orders_for_specific_strat(
        strategy.config.order_id_tag,
        raw_orders,
        strategy_start_date=strategy.config.start_date,
        claim_untagged_orders=getattr(
            strategy.config, "claim_untagged_venue_order_ids", False
        ),
    )

    formatted_orders = format_kraken_orders(strat_orders, ledgers_by_refid)

    ret, _ = calc_info_for_all_positions_in_strategy(formatted_orders, strategy.config)

    return ret

"""
=========================
Section: Small
=========================
"""

def _format_kraken_pair(
    pair: str | None,
) -> str | None:
    """
    Kraken reports ETHUSD / XBTUSD. Strategy logic uses ETH/USD / BTC/USD.
    """
    if pair.startswith("XBT"):
        pair = "BTC" + pair[3:]
    
    return pair[:3] + "/" + pair[3:]