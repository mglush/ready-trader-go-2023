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
from http import client
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
        for id in self.last_orders:
            # order is either current or executed or cancelled orders, filled ratio still applies!
            if id in self.executed_orders:
                # calc the actual ratio = filled/volume.
                ratio = self.executed_orders[id]['filled'] / self.executed_orders[id]['volume']
            elif id in self.cancelled_orders:
                # calc the actual ratio = filled/volume.
                ratio = self.cancelled_orders[id]['filled'] / self.cancelled_orders[id]['volume'] 
            elif id in self.current_orders:
                # calc the actual ratio = filled/volume.
                ratio = self.current_orders[id]['filled'] / self.current_orders[id]['volume']               
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
        if order_id in self.executed_orders:
            return self.executed_orders[order_id]['volume'] / self.average_volume()
        elif order_id in self.cancelled_orders:
            return self.cancelled_orders[order_id]['volume'] / self.average_volume()
        elif order_id in self.current_orders:
            return self.current_orders[order_id]['volume'] / self.average_volume()

    def record_order(self, order_id, order_type, price, volume, lifespan) -> None:
        '''
        records order into current_orders.
        returns nothing.
        '''
        self.current_orders[order_id] = {
            'id' : order_id,            # order id.
            'type' : order_type,        # Side.BID or Side.ASK.
            'price' : price,            # price of order.
            'filled' : 0,               # amount of shares in order that were filled.
            'volume' : volume,          # total size of the order.
            'lifespan' : lifespan,      # good for day vs fill and kill
        }

    def place_orders_at_two_levels(self, bid, bid_volume, ask, ask_volume) -> None:
        '''
        places two orders at the given bid and ask with given volumes,
        inserts order into current_orders data structure.
        returns: nothing.
        '''
        
        if bid_volume > 0 and len(self.current_orders) < LIVE_ORDER_LIMIT and self.position < POSITION_LIMIT - bid_volume:
            self.logger.info(f'PLACING ORDERS AT BID {bid} VOLUME {bid_volume} AND ASK {ask} VOLUME {ask_volume}!')
            # placing the bid order here.
            # size could be based on other factors than just lot LOT_SIZE variable!!!
            self.bid_id = next(self.order_ids)
            # record the order as as currently-placed order.
            self.record_order(self.bid_id, Side.BUY, bid, bid_volume, Lifespan.GOOD_FOR_DAY)
            self.last_orders.append(self.bid_id)
            self.bid_price = bid
            # send the order out.
            self.send_insert_order(self.bid_id, Side.BUY, bid, bid_volume, Lifespan.GOOD_FOR_DAY) # LIMIT ORDER = GOOD FOR DAY ORDER
            self.bids.add(self.bid_id)
    
        if ask_volume > 0 and len(self.current_orders) < LIVE_ORDER_LIMIT and self.position > -POSITION_LIMIT + ask_volume:
            # placing the ask order here.
            # size could be based on other factors than just lot LOT_SIZE variable!!!
            self.ask_id = next(self.order_ids)
            # record the order as as currently-placed order.
            self.record_order(self.ask_id, Side.SELL, ask, ask_volume, Lifespan.GOOD_FOR_DAY)
            self.last_orders.append(self.ask_id)
            self.ask_price = ask
            # send the order out.
            self.send_insert_order(self.ask_id, Side.SELL, ask, ask_volume, Lifespan.GOOD_FOR_DAY) # LIMIT ORDER = GOOD FOR DAY ORDER
            self.asks.add(self.ask_id)

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

    def check_current_orders_optimality(self, bid, ask) -> None:
        '''
        checks every current order to make sure its within the current optimal interval.
        returns: nothing.
        '''
        cancelled_ids = list()
        for order_id, order in self.current_orders.items():
            self.logger.info(f'checking order id {order_id}')
            if order['price'] < bid or order['price'] > ask:
                self.logger.info(f'CANCELLING ORDER {order_id}')
                self.cancelled_orders[order_id] = self.current_orders[order_id]
                self.send_cancel_order(order_id)
                cancelled_ids.append(order_id)

        # modify dictionary.
        for id in cancelled_ids:
            # order cancelled, remove it from current orders, insert into cancelled orders.
            del self.current_orders[id]


    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically to report the status of an order book.

        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """
        # TO DO CREATE CSV WITH ORDER BOOOK SNAPSHOTS FROM THE SIMULATION.
        self.logger.info("received order book for instrument %d with sequence number %d", instrument,
                         sequence_number)
        
        if bid_prices[0] == 0 or ask_prices[0] == 0:
            self.logger.info("LOOKS LIKE DA FIRST ITERATION! DOING NOTHING!")
        elif instrument == Instrument.ETF:
            self.logger.info("RECEIVED ETF ORDER BOOK!")
            # next, we need to aggregate the volumes and append it to the orderbook_volumes list.
            self.orderbook_volumes.append(sum(ask_volumes) + sum(bid_volumes))
            # check if we now have too many volumes stored.
            if len(self.orderbook_volumes) > self.window_size:
                self.orderbook_volumes.pop(0) # remove least reacent volume.

            # weighted average to compute theoretical_price, to be modified later.
            total_volume = sum(ask_volumes) + sum(bid_volumes)
            ask_volume_ratios = np.array(np.array(ask_volumes)/total_volume)
            bid_volume_ratios = np.array(np.array(bid_volumes)/total_volume)
            theoretical_price = np.dot(np.array(ask_prices), ask_volume_ratios) \
                       + np.dot(np.array(bid_prices), bid_volume_ratios)
            # theoretical_price = (bid_prices[0] + ask_prices[0]) / 2
            self.logger.info(f'THEORETICAL PRICE CALCULATED TO BE {theoretical_price}.') 

            # standard deviation to use for spread.
            spread = np.sqrt(np.std(np.array(ask_prices + bid_prices)))

            # check if we are just starting up and have no current information.
            # below should be if len(self.last_orders) < self.window_size but for now im running this strat
            # without using any statistical variables. this is the baseline P/L strategy IMO.
            if True:
                # need to find fair price using JUST weighted average,
                # calculate the spread with variance,
                # start placing orders and collecting info,
                # calculate newBid and newAsk based on our fair price and spread.
                new_bid = theoretical_price - spread / 2
                new_ask = theoretical_price + spread / 2

                # new_ask and new_bid are probably not to the tick_size_in_cents correct.
                # two appraoches here.
                # can either round up new_ask and new_bid to their nearest tick mark
                # or we can keep them there and place orders around
                # the new_ask at nearest ticks on both sides.
                # need to think about how to round here.
                new_bid_by_tick = int(new_bid - new_bid % TICK_SIZE_IN_CENTS) # more conservative to round bid down.
                new_ask_by_tick = int(new_ask + TICK_SIZE_IN_CENTS - new_ask % TICK_SIZE_IN_CENTS) # more conservative to round ask up.

                # we want to asymetrically "change" the spread we have based on the current order book spread.
                # within the cases, we change the spread by inserting two orders at our spread (no information on what better to do yet).
                # the order size should be determined by the depth of the book.
                # the order timing should be full lot in one order (for each side), because we don't
                # yet have information on how to break up the order properly.
                # in every single case, we MUST check if we have existing orders outside our calculated interval, and CANCEL them.
                # perhaps not the best solution once we do time-weighted execution, but if the interval we have does not contain
                # the price of a current order, it should not be a current order in my opinion.
                # 7 cases:
                if new_bid_by_tick > ask_prices[0]:
                    # our interval is completely ABOVE current market interval.
                    # => we want to move the market UP.
                    # => we want to break to ASK and go PAST it.
                    # => we need to RAISE our calculated BID to or past the current ASK and keep the ASK we calculated the SAME.
                    self.logger.info("our interval is completely ABOVE current market interval")
                    # if self.position < 0: # only push the current spread up if our position allows for it.
                    #     new_bid_by_tick = ask_prices[0] # raising the bid, using volume = min(volume at that bid, LOT_SIZE) at that bid.
                    #     self.place_orders_at_two_levels(new_bid_by_tick, min(ask_volumes[0], LOT_SIZE), new_ask_by_tick, LOT_SIZE)
                elif new_bid_by_tick > bid_prices[0] and new_ask_by_tick > ask_prices[0]:
                    # our interval OVERLAPS actual market interval on the right side.
                    # => we want to move the market UP, not as aggressively as the previous case.
                    # => we want to break the ASK and STAY at it, or just rattle the ASK a little.
                    # => we need to RAISE our calculated BID towards current ASK and keep the ASK we calculated the SAME.
                    self.logger.info("our interval OVERLAPS actual market interval on the right side")
                    # new_bid_by_tick = ask_prices[0] # raising the bid, using volume = min(volume at that bid, LOT_SIZE) at that bid.
                    # self.place_orders_at_two_levels(new_bid_by_tick, min(bid_volumes[0], LOT_SIZE), new_ask_by_tick, LOT_SIZE)
                elif new_ask_by_tick < bid_prices[0]:
                    # our interval is completely BELOW current market interval.
                    # => we want to move the market DOWN.
                    # => we want to break to BID and go PAST it.
                    # => we need to LOWER our calculated ASK to or past current BID and keep the BID we calculated the SAME.
                    self.logger.info("our interval is completely BELOW current market interval")
                    # if self.position > 0: # only push the current spread down if our position allows for it.
                    #     new_ask_by_tick = bid_prices[0] # lowering the ask, using volume = min(volume at that bid, LOT_SIZE) at that bid.
                    #     self.place_orders_at_two_levels(new_bid_by_tick, min(bid_volumes[0], LOT_SIZE), new_ask_by_tick, LOT_SIZE)
                elif new_bid_by_tick < bid_prices[0] and new_ask_by_tick < ask_prices[0]:
                    # our interval OVERLAPS actual market interval on the left side.
                    # => we want to move the market DOWN, not as aggressively as the previous case.
                    # => we want to break the BIO and STAY at it, or just rattle the BID a little.
                    # => we need to LOWER our calculated ASK towards current BID and keep the BID we calculated the SAME.
                    self.logger.info("our interval OVERLAPS actual market interval on the left side")
                    # new_ask_by_tick = bid_prices[0] # lowering the ask, using volume = min(volume at that bid, LOT_SIZE) at that bid.
                    # self.place_orders_at_two_levels(new_bid_by_tick, min(bid_volumes[0], LOT_SIZE), new_ask_by_tick, LOT_SIZE)
                elif new_bid_by_tick > bid_prices[0] and new_ask_by_tick < ask_prices[0]:
                    # our interval is WITHIN the actual market interval, GREAT!
                    # => just place orders at the bid and ask we calculated, ba-da-bing ba-da-bang.
                    # must be a better way than just using LOT_SIZE here!!!
                    self.logger.info("our interval is WITHIN the actual market interval")
                    # if (new_ask_by_tick - new_bid_by_tick != TICK_SIZE_IN_CENTS and new_ask_by_tick - new_bid_by_tick != 0):
                    #     self.place_orders_at_two_levels(bid_prices[0] + TICK_SIZE_IN_CENTS, LOT_SIZE, new_ask_by_tick - TICK_SIZE_IN_CENTS, LOT_SIZE)
                elif new_ask_by_tick > ask_prices[0] and new_bid_by_tick < bid_prices[0]:
                    # our interval CONTAINS the actual market interval, this is a little interesting, needs some thought.
                    self.logger.info("our interval CONTAINS the actual market interval")
                    # self.place_orders_at_two_levels(new_bid_by_tick, LOT_SIZE, new_ask_by_tick, LOT_SIZE)
                elif new_ask_by_tick == ask_prices[0] and new_bid_by_tick == bid_prices[0]:
                    # our interval perfectly MATCHES the actual market interval, also a little interesting, needs some thought.
                    self.logger.info("our interval perfectly MATCHES the actual market interval")
                    # self.place_orders_at_two_levels(new_bid_by_tick, LOT_SIZE, new_ask_by_tick, LOT_SIZE)
            
            else:
                # we have information, so modify the theoretical_price and spread and execution duration of the orders.
                # we want to asymetrically change the spread we have based on the current order book spread.
                # after that, we want to determine our order size via a ratio of the average volume, since we now have data.
                pass

            # if we now have too many current orders, we should cancel some old ones.
            # this logic should be rewritten to cancel orders when they're no longer optimal!
            self.check_current_orders_optimality(new_bid_by_tick, new_ask_by_tick)

    def order_status_update_helper(self, id, volume) -> None:
        '''
        finds amount of shares filled,
        HEDGES the filled position,
        updates filled attribute current_orders[id].
        '''
        # first, must hedge whatever partial amount was just filled,
        # as well as update our mPosition variable properly.
        if id in self.executed_orders:
            value_filled = volume - self.executed_orders[id]['filled']
            temp = self.executed_orders
        elif id in self.cancelled_orders:
            value_filled = volume - self.cancelled_orders[id]['filled']
            temp = self.cancelled_orders
        elif id in self.current_orders:
            value_filled = volume - self.current_orders[id]['filled']
            temp = self.current_orders
        
        if value_filled > 0:
            if temp[id]['type'] == Side.BID:
                self.position += value_filled
                self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, value_filled)
            elif temp[id]['type'] == Side.ASK:
                self.position -= value_filled
                self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, value_filled)
            else:
                self.logger.error('ORDER TYPE IS MESSED UP')
            # next, we must update the order's filled amount.
            temp[id]['filled'] = volume

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info(f'ORDER {client_order_id} HAS BEEN FILLED AT {price} WITH VOLUME {volume}')
        
        # some shares were bought, this is our reaction to that.
        self.order_status_update_helper(client_order_id, volume)

        # order executed, remove it from current orders, insert into executed orders.
        
        if client_order_id in self.current_orders:
            self.executed_orders[client_order_id] = self.current_orders[client_order_id]
            del self.current_orders[client_order_id]
        elif client_order_id in self.cancelled_orders:
            self.executed_orders[client_order_id] = self.cancelled_orders[client_order_id]
            del self.cancelled_orders[client_order_id]


    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        """Called when the status of one of your orders changes.

        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.

        If an order is cancelled its remaining volume will be zero.
        """
        self.logger.info(f'STATUS UPDATE ORDER {client_order_id} HAS BEEN FILLED FOR {fill_volume} WITH REMAINING VOLUME {remaining_volume}')
        # this code block happens if the order was CANCELLED, per function description.
        if remaining_volume == 0 or fill_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0

            # It could be either a bid or an ask
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)
            return
        
        # some shares were bought, this is our reaction to that.
        self.logger.info(f'HEDGING {client_order_id}, IT WAS PARTIALLY FILLED WITH {fill_volume} SHARES!')
        self.order_status_update_helper(client_order_id, fill_volume)
        

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
