from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.trading import Strategy

def kraken_crypto_market_buy(
    strategy: Strategy,
    instrument_id: InstrumentId,
    notional: float,
    buffer: float,
) -> MarketOrder:
    """
    This function creates and submits a BUY market order for a cryptocurrency
    trading pair on the Kraken venue. The order is denominated in quote
    currency (notional/cash amount).

    Built to work with the Nautilus Trader framework.
    """
    buffered_notional = round(notional * buffer, 2)

    order = strategy.order_factory.market(
        instrument_id=instrument_id,
        order_side=OrderSide.BUY,
        quantity=Quantity(buffered_notional, precision=2),
        quote_quantity=True,
    )

    strategy.log.info(
        f"MARKET BUY (notional): submitting {buffered_notional} USD of "
        f"{instrument_id.symbol} (buffer={buffer} applied to {notional})"
    )

    strategy.submit_order(order)

    return order