# Copyright 2021 Optiver Asia Pacific Pty. Ltd.
#
# This file is part of Ready Trader Go.
#
#     Ready Trader Go is free software: you can redistribute it and/or
#     modify it under the terms of the GNU Affero General Public License
#     as published by the Free Software Foundation, either version 3 of
#     the License, or (at your option) any later version.
#
#     Ready Trader Go is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU Affero General Public License for more details.
#
#     You should have received a copy of the GNU Affero General Public
#     License along with Ready Trader Go.  If not, see
#     <https://www.gnu.org/licenses/>.
import asyncio
import itertools
import numpy as np

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side


LOT_SIZE = 2
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
TRADES_NEEDED_TO_TRAIN_FEATURES = 10


class AutoTrader(BaseAutoTrader):
    """Example Auto-trader.

    When it starts this auto-trader places ten-lot bid and ask orders at the
    current best-bid and best-ask prices respectively. Thereafter, if it has
    a long position (it has bought more lots than it has sold) it reduces its
    bid and ask prices. Conversely, if it has a short position (it has sold
    more lots than it has bought) then it increases its bid and ask prices.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.ask_id = self.ask_price = self.bid_id = self.bid_price = self.position = 0
        
        # MG ADDITIONS THURSDAY 03/09/2023.
        self.enough_data_acquired = False # must collect data about how our actions 
                                          # influence features/prices.
        self.our_order_impacts = list() # will hold last x impacts of our trades on prices.
        self.current_orders_mapping = dict() # maps orderId -> fairPrice at time of order placement.
                                            # can also have it map to time of order placement.

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error.

        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        """
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0 and (client_order_id in self.bids or client_order_id in self.asks):
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your hedge orders is filled.

        The price is the average price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received hedge filled for order %d with average price %d and volume %d", client_order_id,
                         price, volume)

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically to report the status of an order book.

        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """
        self.logger.info("received order book for instrument %d with sequence number %d", instrument,
                         sequence_number)
        # MG ADDITIONS THURSDAY 03/09/2023.
        if bid_prices[0] == 0 or ask_prices[0] == 0 or ask_volumes[0] == 0 or bid_volumes[0] == 0:
            self.logger.info("LOOKS LIKE DA FIRST ITERATION!")
        elif instrument == Instrument.ETF:
            # compute fair price using weighted average.
            # parameter: how much more important are the asks/bids closer to top?
            # for now, treating each position as equal weight.
            # in the end, log the price we computed.
            
            total_volume = sum(ask_volumes) + sum(bid_volumes)
            ask_volume_ratios = np.array(np.array(ask_volumes)/total_volume)
            bid_volume_ratios = np.array(np.array(bid_volumes)/total_volume)
            new_price = np.dot(np.array(ask_prices), ask_volume_ratios) \
                       + np.dot(np.array(bid_prices), bid_volume_ratios)
            self.logger.info(f'new price calculated to be {new_price}.') 

            # compute standard deviation given the bids and asks.
            # parameter: how many sigmas should the spread be?
            # for now, treating the spread to be ONE sigma.
            # in the end, log the spread we computed.
            spread = np.sqrt(np.sqrt(np.std(np.array(ask_prices + bid_prices))))
            self.logger.info(f'spread calculated to be {spread}.') 

            # calculate newBid and newAsk based on our fair price and spread.
            new_ask = new_price + spread / 2
            new_bid = new_price - spread / 2

            # new_ask and new_bid are probably not to the tick_size_in_cents correct.
            # two appraoches here.
            # can either round up new_ask and new_bid to their nearest tick mark
            # or we can keep them there and place orders around
            # the new_ask at nearest ticks on both sides.
            new_ask_by_tick = int(new_ask + TICK_SIZE_IN_CENTS - new_ask % TICK_SIZE_IN_CENTS) # more conservative to round ask up.
            new_bid_by_tick = int(new_bid - new_bid % TICK_SIZE_IN_CENTS) # more conservative to round bid down.
            
            if not self.enough_data_acquired:
                self.logger.info(f'PLACING TWO NEW ORDERS AT {new_bid_by_tick} and {new_ask_by_tick}.') 
                # submit orders around the new price.
                # once they are executed, the order_filled_function will collect data on impact on price.
                # once we have enough data, this flag will forever be set to true.
                self.bid_id = next(self.order_ids)
                self.bid_price = new_bid_by_tick
                self.send_insert_order(self.bid_id, Side.BUY, new_bid_by_tick, LOT_SIZE, Lifespan.GOOD_FOR_DAY) # LIMIT ORDER = GOOD FOR DAY ORDER
                self.bids.add(self.bid_id)
                self.current_orders_mapping[self.bid_id] = new_price
                
                self.ask_id = next(self.order_ids)
                self.ask_price = new_ask_by_tick
                self.send_insert_order(self.ask_id, Side.SELL, new_ask_by_tick, LOT_SIZE, Lifespan.GOOD_FOR_DAY) # LIMIT ORDER = GOOD FOR DAY ORDER
                self.asks.add(self.ask_id)
                self.current_orders_mapping[self.ask_id] = new_price

                self.logger.info("ORDERS DONE BEEN PLACED.") 
            else:
                # must see whether our perceived price impact lets us insert an order.
                # if it does, we insert order at both new bid and new ask.
                # if it does not, we do nothing, perhaps try to unwind position a little.
                pass
            # trade.

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received order filled for order %d with price %d and volume %d", client_order_id,
                         price, volume)
        # MG ADDITIONS THURSDAY 03/09/2023.
        # calculate impact of order fill on the price.
        # NEED some calculation of impact here.
        # DO NOT KNOW HOW TO COMPUTE PRICE IMPACT HERE!!!

        # record impact and update the list.

        # change flag if we now have enough information.
        if len(self.our_order_impacts) == TRADES_NEEDED_TO_TRAIN_FEATURES:
            self.enough_data_acquired = True

        if client_order_id in self.bids:
            self.position += volume
            self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, volume)
        elif client_order_id in self.asks:
            self.position -= volume
            self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, volume)
        
        # delete orderId from active orders mapping.
        if client_order_id in self.current_orders_mapping:
            del self.current_orders_mapping[client_order_id]

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        """Called when the status of one of your orders changes.

        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.

        If an order is cancelled its remaining volume will be zero.
        """

        # WE CAN DO SHIT HERE TO WORK WITH PARTIALLY-FILELD ORDERS,
        # TRY TO THINK OF WHAT WE CAN RECOMPUTE OR DO WITHIN THIS THANG!!!

        self.logger.info("received order status for order %d with fill volume %d remaining %d and fees %d",
                         client_order_id, fill_volume, remaining_volume, fees)
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0

            # It could be either a bid or an ask
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically when there is trading activity on the market.

        The five best ask (i.e. sell) and bid (i.e. buy) prices at which there
        has been trading activity are reported along with the aggregated volume
        traded at each of those price levels.

        If there are less than five prices on a side, then zeros will appear at
        the end of both the prices and volumes arrays.
        """
        self.logger.info("received trade ticks for instrument %d with sequence number %d", instrument,
                         sequence_number)