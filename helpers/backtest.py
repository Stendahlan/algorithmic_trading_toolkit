from decimal import Decimal
"""
=========================
Section: strat info
=========================
"""
def get_free_cash(
    strategy
):
    venue = strategy.config.bar_types[0].instrument_id.venue
    return strategy.portfolio.account(venue).balance_free().as_decimal()

def get_positions_equity(
    strategy
):
    total = Decimal(0)

    for position in strategy.cache.positions_open(strategy_id=strategy.id):
        total += position.quantity.as_decimal() * strategy.cache.bar(strategy._bar_type_for[position.instrument_id]).close.as_decimal()

    return total
