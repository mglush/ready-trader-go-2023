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

ALPHA = 0.5                     # scale error bars
BETA = 0.5                     # hitting the opposite side after getting lifted

OUR_POSITION_LIMIT = 75         # position size we prefer to stay under.
ORDER_TTL = 40                  # number of orderbook snapshots an order lives for.
VOLUME_SIGNAL_THRESHOLD = 1     # point after which we call the market volatile + scary.
MAX_OPERATIONS_PER_SECOND = 46  # out operations per second limit is a little lower than the rules say.
PERIODICAL_HEDGE_PERIOD = 20    # how often (in terms of orderbook snapshots) we check hedged positions.

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

        self.curr_order_book = dict() # keep latest order book
        
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

    def check_num_operations(self) -> bool:
        '''
        Returns true if we can place an operation safely.
        '''
        current_time = TIME_MODULE.time()
        counter = 0
        for time in self.times_of_events:
            if current_time - time < 1.01: # if they're within the same window.
                counter += 1
            else:
                self.times_of_events = self.times_of_events[:counter+1] # we can delete everything from this point onward.

        self.logger.info(f'NUMBER OPERATIONS IN THE LAST SECOND = {counter}!')
        return (counter < MAX_OPERATIONS_PER_SECOND)

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
                'bid' : bid volume of current orders
                'ask' : ask volume of current orders
            }
        '''
        if len(self.current_orders) == 0:
            return {
                'bid' : 0,
                'ask' : 0
            }

        total_bids = 0
        total_asks = 0
        for order_id, order in self.current_orders.items():
            if order['type'] == Side.BID:
                total_bids += (order['volume'] - order['filled'])
            elif order['type'] == Side.ASK:
                total_asks += (order['volume'] - order['filled'])
   
        return {
            'bid' : total_bids,
            'ask' : total_asks
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
                'bid' : 0,
                'ask' : 0
            }

        total_bids = 0
        total_asks = 0
        for order_id, order in self.hedged_current_orders.items():
            if order['type'] == Side.BID:
                total_bids += order['volume']
            elif order['type'] == Side.ASK:
                total_asks += order['volume']
   
        return {
            'bid' : total_bids,
            'ask' : total_asks
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
        self.logger.info(f'LOGGING ORDER {order_id}')
        self.current_orders[order_id] = {
            'id' : order_id,                                    # order id.
            'type' : order_type,                                # Side.BID or Side.ASK.
            'price' : price,                                    # price of order.
            'filled' : 0,                                       # amount of shares in order that were filled.
            'volume' : volume,                                  # total size of the order.
            'lifespan' : lifespan,                              # good for day vs fill and kill
            'placed_at' : self.timer,                           # to keep track of how long the order has been active for.
            'corresponding_order_id' : corresponding_order_id
        }

    def hedge_record_order(self, order_id, order_type, volume) -> None:
        '''
        Records order into hedged_current_orders.
        '''
        self.logger.info(f'LOGGING HEDGE ORDER {order_id}')
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
                self.logger.info(f'PREVENTING A WASH ORDER, CANNOT PLACE ONE HERE')
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
            if self.check_num_operations():
                self.logger.info(f'PLACING IMMEDIATE IMPULSE ORDER, RESISTING DIRECTIONAL PRESSURE.')
                next_id = next(self.order_ids)
                self.record_order(next_id, type, price, volume, Lifespan.FILL_AND_KILL, None)
                self.send_insert_order(next_id, type, price, volume, Lifespan.FILL_AND_KILL)
                self.times_of_events.insert(0, TIME_MODULE.time())
            else:
                self.logger.warning(f'OPERATION RATE RESTRICTION HIT:\n\tCANNOT SEND IMPULSE ORDER!')

    def place_two_orders_or_none(self, bid, bid_volume, ask, ask_volume) -> None:
        '''
        Places two orders at the given bid and ask with given volumes,
        inserts both orders into current_orders data structure.
        Either places both orders, or doesn't place either of them.
        '''
        bid_wash_flag = self.check_wash_order(Side.BID, bid)
        ask_wash_flag = self.check_wash_order(Side.ASK, ask)
            
        if bid_volume > 0 and ask_volume > 0 \
            and len(self.current_orders) + 2 <= LIVE_ORDER_LIMIT \
            and self.total_volume_of_current_orders()['bid'] + self.position + bid_volume < POSITION_LIMIT \
            and -self.total_volume_of_current_orders()['ask'] + self.position - ask_volume > -POSITION_LIMIT \
            and not bid_wash_flag \
            and not ask_wash_flag \
            and self.check_num_operations():

            self.logger.info(f'PLACING TWO ORDERS AT BID {bid} VOLUME {bid_volume} ASK {ask} VOLUME {ask_volume}!') 
            
            bid_id = next(self.order_ids)
            ask_id = next(self.order_ids)
            
            self.record_order(bid_id, Side.BID, bid, bid_volume, Lifespan.GOOD_FOR_DAY, ask_id)
            self.record_order(ask_id, Side.ASK, ask, ask_volume, Lifespan.GOOD_FOR_DAY, bid_id)

            self.send_insert_order(bid_id, Side.BID, bid, bid_volume, Lifespan.GOOD_FOR_DAY)
            self.times_of_events.insert(0, TIME_MODULE.time()) # record time of message to exchange.
            self.send_insert_order(ask_id, Side.ASK, ask, ask_volume, Lifespan.GOOD_FOR_DAY)
            self.times_of_events.insert(0, TIME_MODULE.time()) # record time of message to exchange.
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
        self.logger.info(f'RECEIVED HEDGE FILLED MESSAGE ID {client_order_id} PRICE {price} VOLUME {volume}')

        # adjust hedged position value.
        if self.hedged_current_orders[client_order_id]['type'] == Side.BID:
            self.hedged_position += volume
        elif self.hedged_current_orders[client_order_id]['type'] == Side.ASK:
            self.hedged_position -= volume
        else:
            self.logger.critical(f'THIS BRANCH SHOULD NEVER BE EXECUTED')

        # remove record of order if it has been fully filled.
        del self.hedged_current_orders[client_order_id]

    def hedge(self, type, volume) -> None:
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
        self.logger.info(f'ENTER MANUAL HEDGE FUNCTION WITH TYPE VOLUME {volume}.')
        current_hedge_volume = self.total_volume_of_hedge_orders()[type]
        price = MAX_ASK_NEAREST_TICK if type == Side.ASK else MIN_BID_NEAREST_TICK

        if abs(self.hedged_position) + current_hedge_volume < POSITION_LIMIT:
            if self.check_num_operations():
                next_id = next(self.order_ids)
                self.hedge_record_order(next_id, type, volume)
                self.send_hedge_order(next_id, type, price, volume)
                self.times_of_events.insert(0, TIME_MODULE.time())
            else:
                self.logger.warning(f'OPERATION RATE RESTRICTION HIT:\n\tWE WANTED TO HEDGE AN UNSUCCESSFUL IMPULSE ORDER, BUT CANNOT!')
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

        @TODO Decide how often we should call this function. it is not good to call it frequently
              assuming our strategy of measuring the spread is good. but still must call it sometimes
              in order to make sure we aren't breaking the rules.

        @TODO There is two cases in this function where, instead of driving the hedge further from 0,
              it is probably best to use an impulse order to bring position closer to 0. however,
              to do this we need a price to send the impulse order at. what price should that be???

        Returns: nothing.
        '''
        self.logger.info(f'ENTER PERIODICAL HEDGE FUNCTION.')

        # if no price was passed in, we will be using max_ask_nearest_tick or min_bid_nearest_tick.
        price_to_bid = price if price is not None else MAX_ASK_NEAREST_TICK
        price_to_ask = price if price is not None else MIN_BID_NEAREST_TICK

        # check what the volume of our hedge position + potentially executed hedges.
        volume = self.total_volume_of_hedge_orders()
        bid_hedge_volume_in_book = volume['bid']
        ask_hedge_volume_in_book = volume['ask']
        
        # current positon plus hedged position,
        diff = abs(self.position + self.hedged_position)
        self.logger.info(f'FOUND DIFF {diff}, CURRENT BID HEDGE VOLUME {bid_hedge_volume_in_book} AND CURRENT ASK VOLUME {ask_hedge_volume_in_book}')

        if self.position != -self.hedged_position:
            if self.check_num_operations():
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
                            self.times_of_events.insert(0, TIME_MODULE.time()) # record the time when we hedged!!!
                        else:
                            pass # current hedged orders are in queue, we should see if they get filled first.
                    else:
                        # position is positive, hedge is too little.
                        # either    1) drive hedge more negative, (THIS IS THE CURRENT CHOICE AND IT IS BAD)
                        #           2) or bring position back toward 0.
                        # FINISH THIS CASE WHEN WE DECIDE WHEN TO DO EACH ONE!
                        diff -= ask_hedge_volume_in_book
                        self.hedge_record_order(next_id, Side.ASK, diff)
                        self.send_hedge_order(next_id, Side.ASK, price_to_ask, diff)
                        self.times_of_events.insert(0, TIME_MODULE.time()) # record the time when we hedged!!!
                elif self.position < 0:
                    if self.hedged_position > -self.position:
                        # position is negative, amd hedge is too large.
                        # want to bring hedge back toward 0.
                        diff -= ask_hedge_volume_in_book
                        if diff > 0:
                            self.hedge_record_order(next_id, Side.ASK, diff)
                            self.send_hedge_order(next_id, Side.ASK, price_to_ask, diff)
                            self.times_of_events.insert(0, TIME_MODULE.time()) # record the time when we hedged!!!
                        else:
                            pass # current hedged orders are in queue, we should see if they get filled first.
                    else:
                        # position is negative, hedge is too little.
                        # either    1) drive hedge more positive, (THIS IS THE CURRENT CHOICE AND IT IS BAD)
                        #           2) or bring position back toward 0.
                        # FINISH THIS CASE WHEN WE DECIDE WHEN TO DO EACH ONE!
                        diff -= bid_hedge_volume_in_book
                        self.hedge_record_order(next_id, Side.BID, diff)
                        self.send_hedge_order(next_id, Side.BID, price_to_bid, diff)
                        self.times_of_events.insert(0, TIME_MODULE.time()) # record the time when we hedged!!!
                else:
                    #position is 0 but we have a hedge open.
                    if self.position < self.hedged_position:
                        # drive hedge toward 0.
                        diff -= ask_hedge_volume_in_book
                        if diff > 0:
                            self.hedge_record_order(next_id, Side.ASK, diff)
                            self.send_hedge_order(next_id, Side.ASK, price_to_ask, diff)
                            self.times_of_events.insert(0, TIME_MODULE.time()) # record the time when we hedged!!!
                        else:
                            pass # current hedged orders are in queue, we should see if they get filled first.
                    else:
                        # drive hedge toward 0.
                        diff -= bid_hedge_volume_in_book
                        if diff > 0:
                            self.hedge_record_order(next_id, Side.BID, diff)
                            self.send_hedge_order(next_id, Side.BID, price_to_bid, diff)
                            self.times_of_events.insert(0, TIME_MODULE.time()) # record the time when we hedged!!!
                        else:
                            pass # current hedged orders are in queue, we should see if they get filled first.
            else:
                self.logger.warning(f'OPERATION RATE RESTRICTION HIT:\n\tWE ARE UNHEDGED, BUT CANNOT PLACE HEDGE!')

    def check_current_orders_ttl(self) -> None:
        '''
        Checks TTL of all current orders. Cancel any order that's passed its time limit.
        '''
        self.logger.info(f'CHECKING CURRENT ORDERS TIME TO LIVE!')

        cancelled_ids = list()
        for order_id, order in self.current_orders.items():
            if self.timer - order['placed_at'] >= ORDER_TTL:
                cancelled_ids.append(order_id)

        for id in cancelled_ids:
            if self.check_num_operations():
                self.logger.info(f'ORDER {order_id} TIMED OUT, CANCELLING.')
                self.send_cancel_order(id)
                self.times_of_events.insert(0, TIME_MODULE.time())
            else:
                self.logger.warning(f'OPERATION RATE RESTRICTION HIT:\n\tCANNOT CANCEL TIMED OUT ORDER {order_id}!')

    def decrease_trading_activity(self) -> None:
        '''
        Function to decrease the volume of all orders close to the current theoretical price,
        but leave the volume of the furthest ask and furthest bid we have up in the order-book.
        '''
        max_ask = max(list(self.curr_order_book['A'].keys()))
        min_bid = min(list(self.curr_order_book['B'].keys()))
        self.logger.info(f'DECREASING TRADING ACTIVITY, KEEPING BID {min_bid} AND ASK {max_ask} ORDERS')
        
        for order_id, order in self.current_orders.items():
            if order['price'] != max_ask and order['price'] != min_bid:
                # if self.check_num_operations():
                self.send_amend_order(order_id, 1)
                self.times_of_events.insert(0, TIME_MODULE.time())
                # else:
                #     self.logger.warning(f'OPERATION RATE RESTRICTION HIT:\n\tCANNOT AMEND ORDER {order_id} VOLUME TO 1!')

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
        self.logger.info("received order book for instrument %d with sequence number %d", instrument,
                         sequence_number)
        self.logger.info(f'SNAPSHOT POSTION {self.position} HEDGE {self.hedged_position}')

        # check all orders' time to live, cancel expired ones.
        self.check_current_orders_ttl()

        # check if we should be looking for a liquidity pool to unwind into, or if we want to market make.
        if self.look_for_liquidity_pockets:
            # means abs(positon) > OUR_POSITION_LIMIT, let's find out if its + or 0
            if self.position > 0:
                # we want to sell out our position, and buy out our hedge.
                # => we would like to look for a big buy order in the orderbook
                #    and hit it (use it to our advantage baby).
                # @TODO NEED TO IMLEMENT ONE-DIRECTIONAL ORDERS HERE TO BE ABLE TO UNWIND POSITION
                pass
            elif self.position < 0:
                # we want to buy out our position, and sell out our hedge.
                # => we would like to look for a big sell order in the orderbook
                #    and hit it (use it to our advantage baby).
                # @TODO NEED TO IMLEMENT ONE-DIRECTIONAL ORDERS HERE TO BE ABLE TO UNWIND POSITION
                pass
            
            return # we only want to market make if we aren't looking for liquidity pockets.

        # check hedged positions once in a while.
        if self.timer % PERIODICAL_HEDGE_PERIOD == 0:
            self.periodical_hedge()

        if bid_prices[0] == 0 or ask_prices[0] == 0:
            self.logger.info(">>>FIRST ITERATION, DO NOTHING!")
        elif instrument == Instrument.ETF:
            # check if we received an out-of-order sequence!
            if sequence_number < 0 or sequence_number <= self.last_sequence_processed:
                self.logger.info("OLD ORDERBOOK INFORMATION RECEIVED, SKIPPING!")
                return
            self.last_sequence_processed = sequence_number # set the sequence number since we are now processing it.

            # aggregate order book
            self.curr_order_book['A'] = dict(zip(ask_prices, ask_volumes))
            self.curr_order_book['B'] = dict(zip(bid_prices, bid_volumes))

            # next, we need to aggregate the volumes and append it to the orderbook_volumes list.
            self.orderbook_volumes['bid_volumes'].append(sum(bid_volumes))
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

            # if we are just starting without information, we have a very simplistic trading approach.
            have_information = False
            if not have_information:
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
                    self.logger.info("our interval is WITHIN (eaten) the actual market interval, TRYING TO TRADE")
                    self.place_two_orders_or_none(new_bid_by_tick, LOT_SIZE, new_ask_by_tick, LOT_SIZE)
                
                elif (new_bid < regular-err) and (new_ask > regular+err):
                    # our interval CONTAINS the actual market interval.
                    self.logger.info("our interval CONTAINS (eats) the actual market interval, TRYING TO TRADE")
                    self.place_two_orders_or_none(new_bid_by_tick, LOT_SIZE, new_ask_by_tick, LOT_SIZE)
                else:
                    # all other cases, we do not trade.
                    self.logger.info("not trading this interval -- it does not eat nor is it eaten.")

            else:
                # we have information. we should use this information we accumulated to better our
                # theoretical value and variance estimates... not sure how ofcourse.
                self.logger.info(f'BRANCH THAT TRADES USING INFORMATION NOT IMPLEMENTED!!!')
                pass
        else:
            pass

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        # when an order is filled, if it was a good for day order, it may have a corresponding order with it.
        if self.current_orders[client_order_id]['lifespan'] == Lifespan.GOOD_FOR_DAY:
            # check whether the filled order was a bid or an ask.
            if self.current_orders[client_order_id]['type'] == Side.ASK:
                order_side, opposite_side = 'A', Side.BID
            elif self.current_orders[client_order_id]['type'] == Side.BID:
                order_side, opposite_side = 'B', Side.ASK

            if price in self.curr_order_book[order_side].keys():
                total_vol_at_price = self.curr_order_book[order_side][price]
                vol_ratio_stat = volume / total_vol_at_price
                if vol_ratio_stat >= BETA:
                    corresponding_order_id = self.current_orders[client_order_id]['corresponding_order_id']
                    if corresponding_order_id in self.current_orders:
                        # the corresponding order id still exists, and beta is high.
                        # => place an impulse order, 
                        # => adjust actual corresponding order's volume to 1 
                        # (or cancel the corresponding order, I am not sure which one).
                        remaining_volume = self.current_orders[corresponding_order_id]['volume'] \
                                            - self.current_orders[corresponding_order_id]['filled']
                        self.place_impulse_order(type=opposite_side, price=price, volume=remaining_volume)
                        if self.check_num_operations():
                            self.send_cancel_order(corresponding_order_id)
                            self.times_of_events.insert(0, TIME_MODULE.time())
                        else:
                            self.logger.warning(f'OPERATION RATE RESTRICTION HIT:\n\tCANNOT SEND IMPULSE ORDER!')

                    elif corresponding_order_id in self.executed_orders:
                        pass # the corresponding order was executed before this one, this is what we want.
                    elif corresponding_order_id in self.cancelled_orders:
                        # the corresponding order was, for some reason, cancelled.
                        # => send an impulse order for the volume amount that
                        # this order was filled at.
                        self.place_impulse_order(type=opposite_side, price=price, volume=remaining_volume)
                    else:
                        # the corresponding order id never existed, we should try to send an impulse order.
                        self.place_impulse_order(type=opposite_side, price=price, volume=remaining_volume)
            else:
                # unable to calculate beta
                # => unable place an impulse order -- there's no remaining volume at the price.
                # we should:
                #   1) check whether we need an impulse order.
                #   2) if we do need an impulse, send it. otherwise, chillen.
                #   2.1) How to decide if we need an impulse? if we have more bids than asks.
                self.logger.critical(f'CASE NOT IMPLEMENTED:\n\t1)UNABLE TO CALCULATE BETA')
        elif self.current_orders[client_order_id]['lifespan'] == Lifespan.FILL_AND_KILL:
            # the filled order was an impulse order. great.
            # order_status function below checks whether the fill and kill was partially or fully filled,
            # and hedges if it was only partially filled.
            pass
        else:
            self.logger.critical(f'THIS BRANCH SHOULD NEVER BE EXECUTED.')

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
            return # we should not do anything if we receive a random client order id...

        fill_volume -= self.current_orders[client_order_id]['filled'] # fill_volume is total filled, but order could've been partially filled multiple times.
                                                                        # we thus need to difference in status, not the fill_volume on its own.
        # update our position
        if self.current_orders[client_order_id]['type'] == Side.BID:
            self.position += fill_volume
        elif self.current_orders[client_order_id]['type'] == Side.ASK:
            self.position -= fill_volume

        # check if our position is "too far from 0" here. if so, start looking for liquidity pockets to unload position.
        self.look_for_liquidity_pockets = True if abs(self.position) > OUR_POSITION_LIMIT else False

        self.logger.info(f'STATUS UPDATE ORDER {client_order_id} HAS BEEN FILLED FOR {fill_volume} MORE SHARES, REMAINING VOLUME {remaining_volume}')

        if remaining_volume > 0 and fill_volume == 0:
            # either order was updated or order is brand new.
            if client_order_id in self.current_orders:
                # this means order was updated.
                self.logger.info(f'UPDATED ORDER {client_order_id} TO VOLUME {remaining_volume}!')
                self.current_orders[client_order_id]['filled'] = 0
                self.current_orders[client_order_id]['volume'] = remaining_volume
            else:
                # this means the order is brand new.
                if self.current_orders[client_order_id]['lifespan'] == Lifespan.FILL_AND_KILL:
                    self.logger.info(f'CREATED FILL AND KILL ORDER {client_order_id} WITH VOLUME {remaining_volume}!')
                elif self.current_orders[client_order_id]['lifespan'] == Lifespan.GOOD_FOR_DAY:
                    self.logger.info(f'CREATED GOOD FOR DAY ORDER {client_order_id} WITH VOLUME {remaining_volume}!')
        elif remaining_volume == 0 and fill_volume > 0:
            # order has been filled and executed!!!
            if self.current_orders[client_order_id]['lifespan'] == Lifespan.FILL_AND_KILL:
                # place hedge if the impulse order we sent did not get filled fully.
                if remaining_volume > 0:
                    self.logger.info(f'IMPULSE ORDER {client_order_id} PARTIALLY FILLED, NEED TO HEDGE.')
                    type_of_hedge = Side.BID if self.current_orders[client_order_id]['type'] == Side.ASK else Side.ASK
                    self.hedge(type_of_hedge, remaining_volume)
                else:
                    self.logger.info(f'IMPULSE ORDER FULLY FILLED, NOT HEDGING.')
            elif self.current_orders[client_order_id]['lifespan'] == Lifespan.GOOD_FOR_DAY:
                self.logger.info(f'GOOD FOR DAY ORDER {client_order_id} FULLY FILLED FOR {fill_volume} SHARES!')
            
            # move order from current orders to executed orders.
            self.executed_orders[client_order_id] = self.current_orders[client_order_id]
            order = self.executed_orders[client_order_id]
            del self.current_orders[client_order_id]
        elif remaining_volume == 0 and fill_volume == 0:
            # order has been cancelled or it was a fill and kill order that did not get filled at all.
            if self.current_orders[client_order_id]['lifespan'] == Lifespan.FILL_AND_KILL:
                # place hedge if the impulse order we sent did not get filled fully.
                self.logger.info(f'IMPULSE ORDER {client_order_id} NOT FILLED AT ALL, NEED TO HEDGE.')
                type_of_hedge = Side.BID if self.current_orders[client_order_id]['type'] == Side.ASK else Side.ASK
                self.hedge(type_of_hedge, remaining_volume)
            elif self.current_orders[client_order_id]['lifespan'] == Lifespan.GOOD_FOR_DAY:
                self.logger.info(f'GOOD FOR DAY ORDER {client_order_id} HAS BEEN CANCELLED!')
            
            # move order from current orders to cancelled orders.
            self.cancelled_orders[client_order_id] = self.current_orders[client_order_id]
            order = self.cancelled_orders[client_order_id]
            del self.current_orders[client_order_id]
        else:
            # order has been partially filled, not cancelled, not executed, not just created.
            self.logger.info(f'THE ORDER {client_order_id} WAS PARTIALLY FILLED!')
            order = self.current_orders[client_order_id]

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
            if self.latest_volume_signal > VOLUME_SIGNAL_THRESHOLD:
                self.decrease_trading_activity()
        else:
            pass