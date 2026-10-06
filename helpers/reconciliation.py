import math
import warnings

def calc_info_for_all_positions_in_strategy(
    formatted_orders,
    config
):
    """
    Replay fills into current qty and cash, and split out dust. Assumes that orders have been formatted
    after being pulled from specific venue.
    """
    ret = {}
    # starting cash
    ret["cash"] = {"qty": config.starting_free_cash}

    for order in sorted(formatted_orders, key=lambda o: o["fill_date"]):
        if order["side"] == "buy":
            ret["cash"]["qty"] -= order["total_cash_spent"]

            if order["symbol"] in ret:
                ret[order["symbol"]]["qty"] += order["total_quantity_bought"]
            else:
                ret[order["symbol"]] = {"qty": order["total_quantity_bought"]}
            
            ret[order["symbol"]]["date_of_most_recent_buy"] = order["fill_date"]

            ret[order["symbol"]]["most_recent_buy_price"] = order["avg_fill_price"]
        else:
            ret["cash"]["qty"] += order["total_cash_received"]

            ret[order["symbol"]]["qty"] -= order["total_quantity_sold"]
    #adding dust
    dust_symbols = get_dust_positions(formatted_orders)
    dust = {}
    
    for sym in dust_symbols:
        dust[sym] = ret[sym]

        del ret[sym]
    # formatting and checking cash
    ret["cash"]["qty"] = math.floor(ret["cash"]["qty"] * 100) / 100

    if ret["cash"]["qty"] < 0:
        warnings.warn(
            f"Cash quantity is negative: {ret['cash']['qty']}",
            UserWarning,
            stacklevel=2,
        )

    return ret, dust

def get_dust_positions(
    formatted_orders
):
    """
    Symbols whose latest fill is a sell.
    """
    ret = set()

    for order in sorted(formatted_orders, key=lambda o: o["fill_date"]):
        if order["side"] == "buy":
            ret.discard(order["symbol"])
        else:
            ret.add(order["symbol"])
    
    return ret
    


