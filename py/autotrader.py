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
from cmath import inf
import time as TIME_MODULE
from audioop import avg
from http import client
import itertools
from textwrap import fill
from tkinter import E
from turtle import pos, position

import numpy as np

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side


LOT_SIZE = 10                   # size of each order we make.
POSITION_LIMIT = 100            # hard position cap.

ALPHA = 0.8                     # scale error bars
BETA = 0.4                      # hitting the opposite side after getting lifted

OUR_POSITION_LIMIT = 80         # position size we prefer to stay under.
ORDER_TTL = 40                  # number of orderbook snapshots an order lives for.
VOLUME_SIGNAL_THRESHOLD_ONE = 1 # point after which we widen our spread by lowering volume of inner orders to 1.
VOLUME_SIGNAL_THRESHOLD_TWO = 2 # point after which we call the market volatile + scary.
MAX_OPERATIONS_PER_SECOND = 48  # out operations per second limit is a little lower than the rules say.
PERIODICAL_HEDGE_CHECK  = 40    # how often (in terms of orderbook snapshots) we check hedged positions.

LIVE_ORDER_LIMIT = 10           # hard cap on live orders.
TICK_SIZE_IN_CENTS = 100        # tick size of the ETF market.

MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS

class AutoTrader(BaseAutoTrader):
    '''
    -_- LiquidBears Awesome Autotrader -_-
    '''

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)

        self.current_etf_book = dict()         # keep latest ETF order book.
        self.current_future_book = dict()      # keep latest FUTURES order book. This one helps us with the cost function of impulse vs hedge.
        
        self.hedged_current_orders = dict()     # keeps track of orders we just tried to hedge.
        self.current_orders = dict()            # order_id -> info about order. 
        self.executed_orders = dict()           # order_id -> info about order.
        self.cancelled_orders = dict()          # order_id -> info about order.
        self.orderbook_volumes = dict()         # is of the following form:
                                                # {
                                                #   'ask_volumes' : list() 
                                                #   'bid_volumes' : list()
                                                # }
        self.orderbook_volumes['bid_volumes'] = list()
        self.orderbook_volumes['ask_volumes'] = list()
        self.traded_volumes = list()            # an entry is sum(traded asks + traded bids) from ticks
                                                # msg. used for avg. traded volume and volume pressure.

        self.latest_volume_signal = 0           # holds the latest volume signal produced by compute_volume_signal

        self.hedged_position = 0                # keeps track of hedged position.
        self.position = 0                       # keeps track of regular position.
        self.window_size = 30                   # manually set? should this be computed?
        self.last_sequence_processed = -1       # helps detect old and out-of-order orderbook snapshots.
        self.last_sequence_processed_ticks = -1 # same as last_sequence_processed but for ticks.
        self.timer = 0                          # helps track time during execution

        self.times_of_events = list()           # keeps track of the number of requests we have made in the last second.
        self.look_for_liquidity_pockets = False # true means we should be unwinding a positon toward 0 by looking
                                                # for liquidity pockets, false means we should be market making.

    def check_num_operations(self) -> int:
        '''
        Returns num of operations in the last second.
        An operation limit applies to the following
        3 functions that interract with the exchange:
        send_insert_order, send_amend_order, send_cancel_order

        Thankfully, sending a hedge does not count as an operation.
        '''
        if len(self.times_of_events) < MAX_OPERATIONS_PER_SECOND:
            return True

        current_time = TIME_MODULE.time()
        counter = 0
        for time in self.times_of_events:
            if current_time - time < 1:
                counter += 1
            else:
                self.times_of_events = self.times_of_events[:counter+1] # we can delete everything from this point onward.

        return counter

    def compute_volume_signal(self, ask_vol: int, bid_vol: int) -> float:
        '''
        Compute volume pressure magnitude and side based on newest ticks update message.
        If positive, asks are getting knocked out and price should be rising.
        If negative, bids are getting cleared and price should be falling. We could reverse this.

        Returns: the indicator as a float.
        '''
        return (ask_vol - bid_vol) / (sum(self.traded_volumes) / len(self.traded_volumes))
    
    def total_volume_of_current_orders(self) -> int:
        '''
        Used to keep track of total volume of currently placed orders.

        Returns: 
            {
                Side.BID : bid volume of current orders
                Side.ASK : ask volume of current orders
            }
        '''
        if len(self.current_orders) == 0:
            return {
                Side.BID : 0,
                Side.ASK : 0
            }

        total_bids = 0
        total_asks = 0
        for order_id, order in self.current_orders.items():
            if order['type'] == Side.BID:
                total_bids += (order['volume'] - order['filled'])
            elif order['type'] == Side.ASK:
                total_asks += (order['volume'] - order['filled'])
   
        return {
            Side.BID : total_bids,
            Side.ASK : total_asks
        }
    
    def total_volume_of_hedge_orders(self) -> int:
        '''
        Used to keep track of total volume of currently placed hedge orders.

        Returns: 
            {
                'bid' : bid volume of hedge orders
                'ask' : ask volume of hedge orders
            }
        '''
        if len(self.hedged_current_orders) == 0:
            return {
                Side.BID : 0,
                Side.ASK : 0
            }

        total_bids = 0
        total_asks = 0
        for order_id, order in self.hedged_current_orders.items():
            if order['type'] == Side.BID:
                total_bids += order['volume']
            elif order['type'] == Side.ASK:
                total_asks += order['volume']
   
        return {
            Side.BID : total_bids,
            Side.ASK : total_asks
        }

    def average_volume(self, order_type) -> float:
        '''
        Returns: average volume in the orderbook over the past window_size snapshots.
        '''
        if order_type == Side.BID or order_type == Side.ASK:
            return sum(self.orderbook_volumes[order_type]) / len(self.orderbook_volumes[order_type])
        else:
            self.logger.critical(f'THIS BRANCH SHOULD NEVER BE EXECUTED!')

    def record_order(self, order_id, order_type, price, volume, lifespan, corresponding_order_id) -> None:
        '''
        Records order into current_orders.
        '''
        self.current_orders[order_id] = {
            'id' : order_id,                                    # order id.
            'type' : order_type,                                # Side.BID or Side.ASK.
            'price' : price,                                    # price of order.
            'filled' : 0,                                       # amount of shares in order that were filled.
            'volume' : volume,                                  # total size of the order.
            'lifespan' : lifespan,                              # good for day vs fill and kill
            'placed_at' : self.timer,                           # to keep track of how long the order has been active for.
            'corresponding_order_id' : corresponding_order_id   # if GOOD FOR DAY: it's the corresponding bid or ask of the spread we posted.
                                                                # if FILL AND KILL: either says "IMPULSE_HEDGE" or "LIQUIDITY_POCKET" or "BAD_ORDER"
                                                                # specifying the reason we placed the fill and kill order.
        }

    def hedge_record_order(self, order_id, order_type, volume) -> None:
        '''
        Records order into hedged_current_orders.
        '''
        self.hedged_current_orders[order_id] = {
            'type' : order_type,    # Side.BID or Side.ASK.
            'volume' : volume       # amount want to fill.
        }

    def check_wash_order(self, order_type, order_price) -> bool:
        '''
        Checks whether the order we are about to place is a wash order.
        It is illegal to lift your own ask or hit your own bid.

        Returns:
            true if the order is possibly a wash order.
            false otherwise.
        '''
        for order_id, order in self.current_orders.items():
            if order['type'] != order_type and order['price'] == order_price:
                return True
        return False

    def place_impulse_order(self, type, price, volume) -> None:
        '''
        Function for the purpose of unwinding a position we have just entered;
        Because it unwinds, we do not need to check whether we exceed max order limit (we just got filled for an order).
        
        Acts as a position unwinder!!!
        '''
        # if it isn't a wash order, we record this single-sided order, and send it out.
        if not self.check_wash_order(type, price):
            if self.check_num_operations() < MAX_OPERATIONS_PER_SECOND:
                next_id = next(self.order_ids)
                self.record_order(next_id, type, price, volume, Lifespan.FILL_AND_KILL, 'IMPULSE_HEDGE')
                self.send_insert_order(next_id, type, price, volume, Lifespan.FILL_AND_KILL)
                self.times_of_events.insert(0, TIME_MODULE.time())
            else:
                self.logger.warning(f'OPERATION RATE RESTRICTION HIT:\n\t\t\t\tCANNOT SEND IMPULSE ORDER!')

    def place_two_orders_or_none(self, bid, bid_volume, ask, ask_volume) -> None:
        '''
        Places two orders at the given bid and ask with given volumes,
        inserts both orders into current_orders data structure.
        Either places both orders, or doesn't place either of them.
        '''
        
        for id, order in self.current_orders.items():
            if order['price'] == bid or order['price'] == ask:
                # we don't want to place spreads in spots we already have orders in.
                return 

        bid_wash_flag = self.check_wash_order(Side.BID, bid)
        ask_wash_flag = self.check_wash_order(Side.ASK, ask)
            
        if bid_volume > 0 and ask_volume > 0 \
            and len(self.current_orders) + 2 <= LIVE_ORDER_LIMIT \
            and self.total_volume_of_current_orders()[Side.BID] + self.position + bid_volume < POSITION_LIMIT \
            and -self.total_volume_of_current_orders()[Side.ASK] + self.position - ask_volume > -POSITION_LIMIT \
            and not bid_wash_flag \
            and not ask_wash_flag \
            and self.check_num_operations() < MAX_OPERATIONS_PER_SECOND:
            
            bid_id = next(self.order_ids)
            ask_id = next(self.order_ids)
            
            self.record_order(bid_id, Side.BID, bid, bid_volume, Lifespan.GOOD_FOR_DAY, ask_id)
            self.record_order(ask_id, Side.ASK, ask, ask_volume, Lifespan.GOOD_FOR_DAY, bid_id)

            self.send_insert_order(bid_id, Side.BID, bid, bid_volume, Lifespan.GOOD_FOR_DAY)
            self.times_of_events.insert(0, TIME_MODULE.time()) # record time of message to exchange.
            self.send_insert_order(ask_id, Side.ASK, ask, ask_volume, Lifespan.GOOD_FOR_DAY)
            self.times_of_events.insert(0, TIME_MODULE.time()) # record time of message to exchange.
            
            self.logger.info(f'PLACED TWO ORDERS AT BID {bid} VOLUME {bid_volume} ASK {ask} VOLUME {ask_volume}!') 
        else:
            self.logger.info(f'CANNOT PLACE PAIR OF ORDERS AT THIS MOMENT, RISK PARAMETERS DO NOT ALLOW FOR THIS.')

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """
        Called when the exchange detects an error.

        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        """
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0:
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """
        Called when one of your hedge orders is filled.

        The price is the average price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        if volume == 0 and price == 0:
            self.logger.info(f'NO SUCCESS! CANCELLED HEDGE {client_order_id} PRICE {price} VOLUME {volume}')
        else:
            self.logger.info(f'SUCCESS! FILLED HEDGE {client_order_id} PRICE {price} VOLUME {volume}')

        # adjust hedged position value.
        if self.hedged_current_orders[client_order_id]['type'] == Side.BID:
            self.hedged_position += volume
        elif self.hedged_current_orders[client_order_id]['type'] == Side.ASK:
            self.hedged_position -= volume
        else:
            self.logger.critical(f'THIS BRANCH SHOULD NEVER BE EXECUTED')

        # remove record of order if it has been fully filled.
        del self.hedged_current_orders[client_order_id]

    def hedge(self, type, volume, price=None) -> None:
        '''        
        Function to hedge given a volume we want to hedge.
        This differs from the function below, as this one is usually used
        to hedge when an impulse did not work, the function below is used
        periodically to check hedged position status for deviations.

        In this function, we informingly place a hedge with a certain volume.
        Thus, this function does not check whether position = hedged positon.
        Rather, we make sure we aren't going about 100 lots or below -100 lots,
        and send the hedge order.

        This function uses MAX_ASK_NEAREST_TICK and MIN_BID_NEAREST_TICK
        and we will call this function when we know its cheaper than an impulse order.
        '''
        self.logger.info(f'ENTER MANUAL HEDGE WITH VOLUME {volume}.')

        if price is None:
            price = MAX_ASK_NEAREST_TICK if type == Side.BID else MIN_BID_NEAREST_TICK

        # check what the volume of our hedge position + potentially executed hedges.
        current_hedged_volume = self.total_volume_of_hedge_orders()[type]

        if abs(self.hedged_position) + current_hedged_volume + volume < POSITION_LIMIT:
            next_id = next(self.order_ids)
            self.hedge_record_order(next_id, type, volume)
            self.send_hedge_order(next_id, type, price, volume)
        else:
            # we wanted to hedge, but we have too many hedged orders currently placed.
            self.logger.warning(f'COULD NOT HEDGE BECAUSE THERE IS TOO MUCH HEDGED VOLUME CURRENTLY PLACED!')


    def periodical_hedge(self, price=None) -> None:
        '''
        Function to hedge as a last resort, to fix position back to full hedged.
        This function decides how to hedge ON ITS OWN.
        If price is passed in, we try to hedge at that price.
        If price is not passed in, we use MIN_BID or MAX_ASK.
        The latter scenario is bad, and we should try to always put a price of the hedge in.

        Function should be called periodically, not sure how often...

        @TODO There is two cases in this function where, instead of driving the hedge further from 0,
              it is probably best to use an impulse order to bring position closer to 0. however,
              to do this we need a price to send the impulse order at. what price should that be???
        '''
        self.logger.info(f'ENTER PERIODICAL HEDGE.')

        # if no price was passed in, we will be using max_ask_nearest_tick or min_bid_nearest_tick.
        price_to_bid = price if price is not None else MAX_ASK_NEAREST_TICK
        price_to_ask = price if price is not None else MIN_BID_NEAREST_TICK

        # check what the volume of our hedge position + potentially executed hedges.
        volume = self.total_volume_of_hedge_orders()
        bid_hedge_volume_in_book = volume[Side.BID]
        ask_hedge_volume_in_book = volume[Side.ASK]
        
        # current positon plus hedged position,
        diff = abs(self.position + self.hedged_position)
        self.logger.info(f'FOUND DIFF {diff}, CURRENT BID HEDGE VOLUME {bid_hedge_volume_in_book} AND CURRENT ASK VOLUME {ask_hedge_volume_in_book}')

        if self.position != -self.hedged_position:
            next_id = next(self.order_ids)
            if self.position > 0:
                if self.position < -self.hedged_position:
                    # position is positive, hedge too negative.
                    # want to bring hedge back towards 0.
                    diff -= bid_hedge_volume_in_book
                    if diff > 0:
                        # need to hedge.
                        self.hedge_record_order(next_id, Side.BID, diff)
                        self.send_hedge_order(next_id, Side.BID, price_to_bid, diff)
                    else:
                        pass # current hedged orders are in queue, we should see if they get filled first.
                else:
                    # position is positive, hedge is too little.
                    diff -= ask_hedge_volume_in_book
                    self.cost_function(Side.ASK, list(self.current_etf_book[Side.BID])[0], diff)
            elif self.position < 0:
                if self.hedged_position > -self.position:
                    # position is negative, amd hedge is too large.
                    # want to bring hedge back toward 0.
                    diff -= ask_hedge_volume_in_book
                    if diff > 0:
                        self.hedge_record_order(next_id, Side.ASK, diff)
                        self.send_hedge_order(next_id, Side.ASK, price_to_ask, diff)
                    else:
                        pass # current hedged orders are in queue, we should see if they get filled first.
                else:
                    # position is negative, hedge is too little.
                    # either    1) drive hedge more positive, (THIS IS THE CURRENT CHOICE AND IT IS BAD)
                    #           2) or bring position back toward 0.
                    # FINISH THIS CASE WHEN WE DECIDE WHEN TO DO EACH ONE!
                    diff -= bid_hedge_volume_in_book
                    self.cost_function(Side.BID, list(self.current_etf_book[Side.ASK])[0], diff)
            else:
                #position is 0 but we have a hedge open.
                if self.position < self.hedged_position:
                    # drive hedge toward 0.
                    diff -= ask_hedge_volume_in_book
                    if diff > 0:
                        self.hedge_record_order(next_id, Side.ASK, diff)
                        self.send_hedge_order(next_id, Side.ASK, price_to_ask, diff)
                    else:
                        pass # current hedged orders are in queue, we should see if they get filled first.
                else:
                    # drive hedge toward 0.
                    diff -= bid_hedge_volume_in_book
                    if diff > 0:
                        self.hedge_record_order(next_id, Side.BID, diff)
                        self.send_hedge_order(next_id, Side.BID, price_to_bid, diff)
                    else:
                        pass # current hedged orders are in queue, we should see if they get filled first.

    def check_current_orders_ttl(self) -> None:
        '''
        Checks TTL of all current orders. Cancel any order that's passed its time limit.
        '''
        self.logger.info(f'CHECKING CURRENT ORDERS TIME TO LIVE!')

        cancelled_ids = list()
        for order_id, order in self.current_orders.items():
            if self.timer - order['placed_at'] >= ORDER_TTL:
                cancelled_ids.append(order_id)

        temp = self.check_num_operations()
        for id in cancelled_ids[:min(temp, len(cancelled_ids))]:
            self.logger.info(f'ORDER {order_id} TIMED OUT.')
            self.send_cancel_order(id)
            self.times_of_events.insert(0, TIME_MODULE.time())

    def decrease_trading_activity(self) -> None:
        '''
        Function to decrease the volume of all orders close to the current theoretical price,
        but leave the volume of the furthest ask and furthest bid we have up in the order-book.
        '''
        
        # if the order book isn't of length 2, we don't have the orderbook.
        max_ask = max(list(self.current_etf_book[Side.ASK].keys())) if len(self.current_etf_book) > 1 else inf
        min_bid = min(list(self.current_etf_book[Side.BID].keys())) if len(self.current_etf_book) > 1 else 0
        self.logger.info(f'DECREASING TRADING ACTIVITY, KEEPING BID {min_bid} AND ASK {max_ask} ORDERS')
        
        temp = MAX_OPERATIONS_PER_SECOND - self.check_num_operations()
        for order_id, order in self.current_orders.items():
            if order['price'] != max_ask and order['price'] != min_bid:
                if temp == 0: break
                self.send_amend_order(order_id, 1)
                self.times_of_events.insert(0, TIME_MODULE.time())
                temp -= 1

    def cost_function(self, type, price_of_impulse_cause, volume) -> None:
        '''
        Function to decide if its cheaper to send an impulse order
        at the given price with the given volume,
        or if its better to hedge at MIN_ASK or MAX_BID at this point in time.

        Function will be used whenever we are unhedged, to decide how we are
        to try to get back to a fully hedged state with minimal cost.
        '''
        if len(self.current_etf_book) <= 1 or len(self.current_future_book) <= 1:
            return # base case where we dont have an orderbook to go off yet.
        
        # get the top etf and future orderbook order of the given type, calculate cost function.
        ask_etf, bid_etf = list(self.current_etf_book[Side.ASK])[0], list(self.current_etf_book[Side.BID])[0]
        ask_future, bid_future = list(self.current_future_book[Side.ASK])[0], list(self.current_future_book[Side.BID])[0]
        impulse_loss = volume * ((ask_etf - bid_etf) + price_of_impulse_cause * 0.002) # we pay the spread times volume, as well as the taker fee.

        if type == Side.BID:
            # this means we want to place a bid cuz our ask just got filled.
            future_loss = volume * (abs(ask_future - price_of_impulse_cause)) # we pay the difference of what we sold the ETF for and what we buy the FUTURE for. no fee.
            cost = impulse_loss - future_loss
            self.logger.info(f'\t\t\t\tCOST FUNCTIION FOR BUYING ETF OR BUYING A FUTURE = {cost}')
            if cost > 0: # hedge is cheaper.
                self.hedge(type=Side.BID, volume=volume, price=ask_future)
            else: # impulse order is cheaper.
                self.place_impulse_order(type, ask_etf, volume)
        elif type == Side.ASK:
            # this means we want to place a ask cuz our bid just got filled.
            future_loss = volume * (abs(bid_future - price_of_impulse_cause)) # we pay the difference of what we sold the ETF for and what we buy the FUTURE for. no fee.
            cost = impulse_loss - future_loss
            self.logger.info(f'\t\t\t\tCOST FUNCTIION FOR SELLING ETF OR SELLING A FUTURE = {cost}')
            if cost > 0: # hedge is cheaper.
                self.hedge(type=Side.ASK, volume=volume, price=bid_future)
            else: # impulse order is cheaper.
                self.place_impulse_order(type, bid_etf, volume)
        else:
            self.logger.critical(f'THIS BRANCH SHOULD NEVER BE EXECUTED')

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """
        Called periodically to report the status of an order book.

        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """
        self.timer += 1 # increment time counter.

        self.logger.info(f'SNAPSHOT POSTION {self.position} HEDGE {self.hedged_position}')

        # check all orders' time to live, cancel expired ones.
        # check hedged positions once in a while.
        if self.timer % PERIODICAL_HEDGE_CHECK == 0:
            self.check_current_orders_ttl()
            self.periodical_hedge()

        if bid_prices[0] == 0 or ask_prices[0] == 0:
            self.logger.info(">>>FIRST ITERATION, DO NOTHING!")
        elif instrument == Instrument.ETF:
            # if len(self.traded_volumes) < self.window_size:
            #     # do not have enough info about volume indicator, do not trade.
            #     # since if the market starts off very volatile
            #     # we will place incorrect bets the first 2-3 seconds.
            #     return  

            # check if we received an out-of-order sequence!
            if sequence_number < 0 or sequence_number <= self.last_sequence_processed:
                self.logger.info("OLD ORDERBOOK INFORMATION RECEIVED, SKIPPING!")
                return
            self.last_sequence_processed = sequence_number # set the sequence number since we are now processing it.

            # check if we should be looking for a liquidity pool to unwind into, or if we want to market make.
            # if self.look_for_liquidity_pockets:
            #     self.logger.critical(f'LOOKING FOR LIQUIDITY POCKETS!!!')
            #     # means abs(positon) > OUR_POSITION_LIMIT, let's find out if its + or 0
            #     # @TODO: we should also check if price of this big volume is not too far from our theoretical price!
            #     if self.position > 0:
            #         # we want to sell out our position, and buy out our hedge.
            #         # => we would like to look for a big buy order in the orderbook.
            #         if bid_volumes[0] > LOT_SIZE*3:
            #             self.logger.critical(f'FOUND LIQUIDITY POCKET!!!')
            #             # we want to hit this order and unwind hedge too.
            #             if self.check_num_operations() < MAX_OPERATIONS_PER_SECOND:
            #                 next_id = next(self.order_ids)
            #                 self.record_order(next_id, Side.ASK, bid_prices[0], LOT_SIZE, Lifespan.FILL_AND_KILL, 'LIQUIDITY_POCKET')
            #                 self.send_insert_order(next_id, Side.ASK, bid_prices[0], LOT_SIZE, Lifespan.FILL_AND_KILL)
            #                 self.times_of_events.insert(0, TIME_MODULE.time())
            #     elif self.position < 0:
            #         # we want to buy out our position, and sell out our hedge.
            #         # => we would like to look for a big sell order in the orderbook
            #         if ask_volumes[0] > LOT_SIZE*3:
            #             self.logger.critical(f'FOUND LIQUIDITY POCKET!!!')
            #             # we want to hit this order and unwind hedge too.
            #             if self.check_num_operations() < MAX_OPERATIONS_PER_SECOND:
            #                 next_id = next(self.order_ids)
            #                 self.record_order(next_id, Side.BID, ask_prices[0], LOT_SIZE, Lifespan.FILL_AND_KILL, 'LIQUIDITY_POCKET')
            #                 self.send_insert_order(next_id, Side.BID, ask_prices[0], LOT_SIZE, Lifespan.FILL_AND_KILL)
            #                 self.times_of_events.insert(0, TIME_MODULE.time())
            #     return # do we only want to market make if we aren't looking for liquidity pockets? or should we always make a market?
            
            # OTHERWISE WE HIT EM WIT THE GUD OL O CAPTAIN MY CAPTAIN O MAKE ME A MARKET.

            # record ETF order book.
            self.current_etf_book[Side.ASK] = dict(zip(ask_prices, ask_volumes))
            self.current_etf_book[Side.BID] = dict(zip(bid_prices, bid_volumes))

            # next, we need to aggregate the volumes and append it to the orderbook_volumes list,
            # only keeping a window size amount of those records.
            if len(self.orderbook_volumes['bid_volumes']) >= self.window_size:
                self.orderbook_volumes['bid_volumes'].pop(0)
            self.orderbook_volumes['bid_volumes'].append(sum(bid_volumes))
            if len(self.orderbook_volumes['ask_volumes']) >= self.window_size:
                self.orderbook_volumes['ask_volumes'].pop(0)
            self.orderbook_volumes['ask_volumes'].append(sum(ask_volumes))

            # simple average to compute true-ish price
            regular = (bid_prices[0] + ask_prices[0]) / 2
            err = abs(bid_prices[0] - ask_prices[0]) // 2

            # weighted average to compute theoretical_price.
            theo = (bid_volumes[0]*ask_prices[0] + ask_volumes[0]*bid_prices[0]) / (bid_volumes[0]+ask_volumes[0])
            
            # computing variance relative to theoretical price.
            var_theo = sum([ask_volumes[i] * (ask_prices[i] - theo)**2 for i in range(len(ask_prices))])
            var_theo += sum([bid_volumes[i] * (bid_prices[i] - theo)**2 for i in range(len(bid_prices))])
            var_theo = var_theo / (sum(ask_volumes) + sum(bid_volumes))

            # if ask_prices[0] - bid_prices[0] > 4 * TICK_SIZE_IN_CENTS:
            #     if ask_prices[0] < theo:
            #         self.logger.critical(f'FOUND BAD ORDER, TRYING TO SNAG IT!!!')
            #         if self.check_num_operations() < MAX_OPERATIONS_PER_SECOND:
            #             next_id = next(self.order_ids)
            #             size = min(ask_volumes[0], LOT_SIZE)
            #             self.record_order(next_id, Side.BID, ask_prices[0], size, Lifespan.FILL_AND_KILL, 'BAD_ORDER')
            #             self.send_insert_order(next_id, Side.BID, ask_prices[0], size, Lifespan.FILL_AND_KILL)
            #             self.times_of_events.insert(0, TIME_MODULE.time())
            #     elif bid_prices[0] > theo:
            #         self.logger.critical(f'FOUND BAD ORDER, TRYING TO SNAG IT!!!')
            #         if self.check_num_operations() < MAX_OPERATIONS_PER_SECOND:
            #             next_id = next(self.order_ids)
            #             size = min(bid_volumes[0], LOT_SIZE)
            #             self.record_order(next_id, Side.ASK, bid_prices[0], size, Lifespan.FILL_AND_KILL, 'BAD_ORDER')
            #             self.send_insert_order(next_id, Side.ASK, bid_prices[0], size, Lifespan.FILL_AND_KILL)
            #             self.times_of_events.insert(0, TIME_MODULE.time())

            # get new bid and ask using our theo and variance.
            new_bid = theo - ALPHA * np.sqrt(var_theo)
            new_ask = theo + ALPHA * np.sqrt(var_theo)

            self.logger.info(f'REAL INTERVAL [{bid_prices[0]}, {ask_prices[0]}] OUR INTERVAL [{new_bid}, {new_ask}]')

            # round the bid and ask towards the tick size.
            new_bid_by_tick = int(new_bid - new_bid % TICK_SIZE_IN_CENTS) # more conservative to round bid down.
            new_ask_by_tick = int(new_ask + TICK_SIZE_IN_CENTS - new_ask % TICK_SIZE_IN_CENTS) # more conservative to round ask up.
            # 2 cases where we trade, for now:
            if (new_bid > regular-err) and (new_ask < regular+err):
                # our interval is WITHIN the actual market interval.
                self.place_two_orders_or_none(new_bid_by_tick, LOT_SIZE, new_ask_by_tick, LOT_SIZE)
                # pass
            elif (new_bid < regular-err) and (new_ask > regular+err):
                # our interval CONTAINS the actual market interval.
                # self.place_two_orders_or_none(new_bid_by_tick, LOT_SIZE, new_ask_by_tick, LOT_SIZE)
                pass
            else:
                # all other cases, we do not trade.
                pass

        elif instrument == Instrument.FUTURE:
            # record future book.
            self.current_future_book[Side.ASK] = dict(zip(ask_prices, ask_volumes))
            self.current_future_book[Side.BID] = dict(zip(bid_prices, bid_volumes))
        else:
            pass # received random instrument, don't interrupt flow of program.

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info(f'ENTER ORDER FILLED FUNCTION FOR ORDER {client_order_id}')
        # when an order is filled, if it was a good for day order, it may have a corresponding order with it.
        if self.current_orders[client_order_id]['lifespan'] == Lifespan.GOOD_FOR_DAY:
            # check whether the filled order was a bid or an ask.
            if self.current_orders[client_order_id]['type'] == Side.ASK:
                order_side, opposite_side = Side.ASK, Side.BID
            elif self.current_orders[client_order_id]['type'] == Side.BID:
                order_side, opposite_side = Side.BID, Side.ASK

            corresponding_order_id = self.current_orders[client_order_id]['corresponding_order_id']
            if corresponding_order_id in self.current_orders:
                # the corresponding order id still exists.
                # find volume that was filled, hedge it.
                self.logger.info(f'IN ORDER FILLED FUNCTION:\n\t\t\t\tNEED TO HEDGE AN ORDER THAT WAS JUST FILLED')
                remaining_volume = self.current_orders[corresponding_order_id]['volume'] \
                                    - self.current_orders[corresponding_order_id]['filled']
                self.cost_function(type=opposite_side, price_of_impulse_cause=price, volume=remaining_volume)
                
                # after all this, we can cancel the order we moved up.
                if self.check_num_operations() < MAX_OPERATIONS_PER_SECOND:
                    self.send_cancel_order(corresponding_order_id)
                    self.times_of_events.insert(0, TIME_MODULE.time())
                else:
                    self.logger.warning(f'OPERATION RATE RESTRICTION HIT:\n\t\t\t\tCANNOT SEND IMPULSE ORDER!')

            elif corresponding_order_id in self.executed_orders:
                pass # the corresponding order was executed before this one, this is what we want.
            elif corresponding_order_id in self.cancelled_orders:
                pass # the corresponding order was cancelled properly
            else:
                # the corresponding order id never existed.
                self.logger.critical(f'THIS BRANCH SHOULD NEVER GET EXECUTED')
        elif self.current_orders[client_order_id]['lifespan'] == Lifespan.FILL_AND_KILL:
            pass # below function takes care of fill and kill order updates, including executions.
        else:
            self.logger.critical(f'THIS BRANCH SHOULD NEVER BE EXECUTED!') # all orders are GOOD FOR DAY or FILL AND KILL.

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        """Called when the status of one of your orders changes.

        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.

        If an order is cancelled its remaining volume will be zero.
        """
        if client_order_id not in self.current_orders:
            self.logger.warning(f'RECEIVED ORDER ID IN UPDATE ORDER STATUS THAT IS NOT IN CURRENT ORDERS: {client_order_id}')
            return # we should not do anything if we receive a random client order id...

        # fill_volume is total filled, but order could've been partially filled multiple times.
        # we adjust fill volume to mean how many shares filled since last update to this order.
        fill_volume -= self.current_orders[client_order_id]['filled'] 

        # update our position count.
        if self.current_orders[client_order_id]['type'] == Side.BID:
            self.position += fill_volume
        elif self.current_orders[client_order_id]['type'] == Side.ASK:
            self.position -= fill_volume

        # check if our position is "too far from 0" here. if so, start looking for liquidity pockets to unload position.
        # the variable will be checked in the orderbook function to decide if we want to make a market or unload.
        # definitely a better way to decide when to look for liquidity pockets, but for now, we switch modes past 50 ETF SHARES.
        # self.look_for_liquidity_pockets = True if abs(self.position) > OUR_POSITION_LIMIT else False

        if remaining_volume > 0 and fill_volume == 0:
            # order updated or is brand new... does not really matter.
            # this means the order is brand new.
            if self.current_orders[client_order_id]['lifespan'] == Lifespan.FILL_AND_KILL:
                reason = self.current_orders[client_order_id]['corresponding_order_id']
                self.logger.info(f'FROM ORDER STATUS UPDATE:\n\t\t\t\tCREATED FILL AND KILL ORDER {client_order_id} WITH VOLUME {remaining_volume}\n\t\t\t\tREASON => {reason}!')
            elif self.current_orders[client_order_id]['lifespan'] == Lifespan.GOOD_FOR_DAY:
                corresponding_order_id = self.current_orders[client_order_id]['corresponding_order_id']
                self.logger.info(f'FROM ORDER STATUS UPDATE:\n\t\t\t\tCREATED GOOD FOR DAY ORDER {client_order_id} WITH VOLUME {remaining_volume}\n\t\t\t\tCORRESPONDING ORDER ID => {corresponding_order_id}!')
        elif remaining_volume == 0 and fill_volume > 0:
            # order has been filled and executed!!!
            if self.current_orders[client_order_id]['lifespan'] == Lifespan.FILL_AND_KILL:
                reason = self.current_orders[client_order_id]['corresponding_order_id']
                if reason == 'IMPULSE_HEDGE':
                    # this was an impulse hedge, we should send a hedge if this impulse did not work for us, in the same direction as the impulse was in.
                    if remaining_volume > 0:
                        self.logger.info(f'IMPULSE ORDER {client_order_id} PARTIALLY FILLED, SENDING FOLLOW-UP ORDER.')
                        self.hedge(self.current_orders[client_order_id]['type'], remaining_volume)
                    else:
                        self.logger.info(f'IMPULSE ORDER FULLY FILLED, NO NEED TO FOLLOW UP.')
                elif reason == 'LIQUIDITY_POCKET':
                    # we are trying to bring our position close to 0. hedge for the same amount that the order was filled for, in the opposite direction.
                    type_of_hedge = Side.ASK if self.current_orders[client_order_id]['type'] == Side.BID else Side.BID
                    self.hedge(type=type_of_hedge, volume=remaining_volume, price=self.current_orders[client_order_id]['price'])
                elif reason == 'BAD_TRADE':
                    # we see someone posted a bad trade on the market. if we snagged that trade, we should hedge the amount we filled, in the opposite direction.
                    # type_of_hedge = Side.ASK if self.current_orders[client_order_id]['type'] == Side.BID else Side.BID
                    # self.hedge(type=type_of_hedge, volume=remaining_volume)
                    pass
                else:
                    self.logger.critical(f'THIS BRANCH SHOULD NEVER BE EXECUTED!') # reasons to trade fill and kills are only the 3 outlined above.
            elif self.current_orders[client_order_id]['lifespan'] == Lifespan.GOOD_FOR_DAY:
                self.logger.info(f'FROM ORDER STATUS UPDATE:\n\t\t\t\tGOOD FOR DAY ORDER {client_order_id} FULLY FILLED FOR {fill_volume} SHARES!')
            
            # move order from current orders to executed orders.
            self.executed_orders[client_order_id] = self.current_orders[client_order_id]
            del self.current_orders[client_order_id]
        elif remaining_volume == 0 and fill_volume == 0:
            # order has been cancelled.
            if self.current_orders[client_order_id]['lifespan'] == Lifespan.FILL_AND_KILL:
                # fill and kill order did not fill a single share. check reason we tried to do the fill and kill.
                reason = self.current_orders[client_order_id]['corresponding_order_id']
                if reason == 'IMPULSE_HEDGE':
                    # this was an impulse hedge, we should send a hedge since this order did not work for us.
                    # self.logger.info(f'IMPULSE ORDER {client_order_id} DID NOT FILL AT ALL. FOLLOWING UP WITH A HEDGE.')
                    self.hedge(self.current_orders[client_order_id]['type'], remaining_volume)
                elif reason == 'LIQUIDITY_POCKET':
                    # we missed the liquidity pocket we were pursuing. not a problem.
                    # self.logger.info('FROM ORDER STATUS UPDATE:\n\t\t\t\tMISSED LIQUIDITY POCKET OPPORTUNITY.')
                    pass
                elif reason == 'BAD_TRADE':
                    # we missed the bad trade we were trying to catch. too slow.
                    # self.logger.info('FROM ORDER STATUS UPDATE:\n\t\t\t\tMISSED SNAGGING A BAD TRADE OPPORTUNITY.')
                    pass
                else:
                    self.logger.critical(f'THIS BRANCH SHOULD NEVER BE EXECUTED!') # reasons to trade fill and kills are only the 3 outlined above.
            elif self.current_orders[client_order_id]['lifespan'] == Lifespan.GOOD_FOR_DAY:
                # corresponding_order_id = self.current_orders[client_order_id]['corresponding_order_id']
                self.logger.info(f'FROM ORDER STATUS UPDATE:\n\t\t\t\tGOOD FOR DAY ORDER {client_order_id} HAS BEEN CANCELLED!')
                # if corresponding_order_id in self.current_orders:
                #     if self.check_num_operations():
                #         self.logger.info(f'\SENDING CANCEL REQUEST FOR CORRESPONDING ORDER {corresponding_order_id} AS WELL!')
                #         self.send_cancel_order(corresponding_order_id)
                #         self.times_of_events.insert(0, TIME_MODULE.time())
            
            # move order from current orders to cancelled orders.
            self.cancelled_orders[client_order_id] = self.current_orders[client_order_id]
            del self.current_orders[client_order_id]
        else:
            # # regular order has been partially filled.
            # self.logger.info(f'FROM ORDER STATUS UPDATE:\n\t\t\t\tTHE ORDER {client_order_id} WAS PARTIALLY FILLED FOR {fill_volume} WITH {remaining_volume} remaining!')
            # # we would like to hedge or offset, but an impulse order may result in a wash order. so we hedge.
            # opposite_side = Side.BUY if self.current_orders[client_order_id]['type'] == Side.ASK else Side.ASK
            # self.cost_function(opposite_side, self.current_orders[client_order_id]['price'], fill_volume)
            
            # # should amend the corresponding GOOD FOR DAY's order's volume down to match this one.
            # # corresponding_order_id = self.current_orders[client_order_id]['corresponding_order_id']
            # # if corresponding_order_id in self.current_orders:
            # #     if self.check_num_operations():
            # #         self.send_amend_order(corresponding_order_id, remaining_volume)
            # #         self.times_of_events.insert(0, TIME_MODULE.time())
            # #     else:
            # #         self.logger.warning(f'OPERATION RATE RESTRICTION HIT:\n\t\t\t\tCOULD NOT AMEND CORRESPONDING ORDER!')
            # # else:
            # #     self.logger.critical(f'\t\t\t\tTHIS GOOD FOR DAY ORDER DOES NOT HAVE A CORRESPONDING GOOD FOR DAY ORDER... NOT GOOD.')
            pass
        # finally, update the filled amount of the order we just worked with if its still a current order.
        if client_order_id in self.current_orders:
            self.current_orders[client_order_id]['filled'] += fill_volume

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """
        Called periodically when there is trading activity on the market.

        The five best ask (i.e. sell) and bid (i.e. buy) prices at which there
        has been trading activity are reported along with the aggregated volume
        traded at each of those price levels.

        If there are less than five prices on a side, then zeros will appear at
        the end of both the prices and volumes arrays.
        """

        # sum up traded ask volumes and traded bid volumes
        sum_ask, sum_bid = sum(ask_volumes), sum(bid_volumes)
        if sum_ask == 0 and sum_bid == 0:
            pass # nothing got traded
        elif instrument == Instrument.ETF:
            # check if we received an out-of-order sequence!
            if sequence_number < 0 or sequence_number <= self.last_sequence_processed_ticks:
                self.logger.info("OLD TICKS INFORMATION RECEIVED, SKIPPING!")
                return
            self.last_sequence_processed_ticks = sequence_number # set the sequence number since we are now processing it.

            # add traded volume to container list for average traded volume computation.
            if len(self.traded_volumes) == self.window_size:
                self.traded_volumes.pop(0)
            self.traded_volumes.append(sum_ask + sum_bid)

            # compute signal
            self.latest_volume_signal = self.compute_volume_signal(ask_vol=sum_ask, bid_vol=sum_bid)
            self.logger.info(f'VOLUME PRESSURE SIGNAL IS: {self.latest_volume_signal}')

            # we do this here because we should react to the signal as fast as we can.
            if self.latest_volume_signal > VOLUME_SIGNAL_THRESHOLD_ONE:
                self.decrease_trading_activity()
        else:
            pass