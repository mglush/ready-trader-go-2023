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


LOT_SIZE = 10
POSITION_LIMIT = 100
LIVE_ORDER_LIMIT = 10
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
TRADES_NEEDED_TO_TRAIN_FEATURES = 25


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

        self.current_orders = dict() # order_id -> info about order. 
        self.executed_orders = dict() # order_id -> info about order.
        self.cancelled_orders = dict() # order_id -> info about order.
        
        self.orderbook_volumes = list() # for average volume.
        self.last_orders = list() # last order ids chronologically ordered.
        
        # self.average_time_to_fill = 0 # TBD.
        self.window_size = 20 # manually set? should this be computed?
        
    def average_fill_ratio(self) -> float:
        '''
        returns: portion of our orders that gets filled, on average.
        '''
        fill_ratios = list()
        # for each id in the last however many orders orders
        for id in self.fill_ratios:
            # order is either current or executed or cancelled orders, filled ratio still applies!
            if id in self.current_orders:
                # calc the actual ratio = filled/volume.
                ratio = self.current_orders[id]['filled'] / self.current_orders[id]['volume']
            elif id in self.executed_orders:
                # calc the actual ratio = filled/volume.
                ratio = self.executed_orders[id]['filled'] / self.executed_orders[id]['volume']
            elif id in self.cancelled_orders:
                # calc the actual ratio = filled/volume.
                ratio = self.cancelled_orders[id]['filled'] / self.cancelled_orders[id]['volume']                
            # append that thang to the end of ratios.
            fill_ratios.append(ratio)

        return sum(fill_ratios) / len(fill_ratios)


    def average_volume(self) -> float:
        '''
        returns: average volume in the orderbook over the past n snapshots.
        '''
        return sum(self.orderbook_volumes) / len(self.orderbook_volumes)

    def curr_order_volume_to_avg_volume_ratio(self, order_id) -> float:
        '''
        returns: ratio of given order volume to average_volume().
        '''
        return self.current_orders[order_id]['volume'] / self.average_volume()

    def record_order(self, order_id, order_type, price, volume, lifespan) -> None:
        '''
        records order into current_orders.
        returns nothing.
        '''
        self.current_orders[order_id] = {
            'id' : order_id,            # order id.
            'type' : order_type,        # sell or buy.
            'price' : price,            # price of order.
            'filled' : 0,               # amount of shares in order that were filled.
            'volume' : volume,          # total size of the order.
            'lifespan' : lifespan,      # good for day vs fill and kill
        }

    def is_volume_trending_up(self):
        '''
        return: true if volume is trending up.
                false otherwise.
        '''
        pass # vasyl's work will go here.

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
        
        if bid_prices[0] == 0 or ask_prices[0] == 0 or ask_volumes[0] == 0 or bid_volumes[0] == 0:
            self.logger.info("LOOKS LIKE DA FIRST ITERATION! DOING NOTHING!")
        elif instrument == Instrument.ETF:
            # first, we need to aggregate the volumes and append it to the orderbook_volumes list.
            self.orderbook_volumes.append(sum(ask_volumes) + sum(bid_volumes))
            # check if we now have too many volumes stored.
            if len(self.orderbook_volumes) > self.window_size:
                self.orderbook_volumes.pop(0) # remove least reacent volume.

            

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received order filled for order %d with price %d and volume %d", client_order_id,
                         price, volume)

        if client_order_id in self.bids:
            self.position += volume
            self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, volume)
        elif client_order_id in self.asks:
            self.position -= volume
            self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, volume)

        # place order into executed_orders.
        self.executed_orders[client_order_id] = self.current_orders[client_order_id]
        # remove order from current orders.
        del self.current_orders[client_order_id]
        # set its fill raito of this order to be 1.
        self.executed_orders[client_order_id]['filled'] = volume
        

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
        
        # first, we must update the order's filled amount.
        self.current_orders[client_order_id]['filled'] = fill_volume

        # this code block happens if the order was cancelled, per function description.
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0

            # It could be either a bid or an ask
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)

            # place order into cancelled.
            self.cancelled_orders[client_order_id] = self.current_orders[client_order_id]
            # remove order from current orders.
            del self.current_orders[client_order_id]
        

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
