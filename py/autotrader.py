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
from audioop import avg
from http import client
import itertools
from turtle import pos, position
import numpy as np

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side


LOT_SIZE = 10
POSITION_LIMIT = 100

OUR_POSITION_LIMIT = 75
DESIRED_AVG_TIME_TO_FILL = 20
ORDER_TTL = 40

LIVE_ORDER_LIMIT = 10
TICK_SIZE_IN_CENTS = 100
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
        
        self.hedged_current_orders = dict() # keeps track of orders we just tried to hedge.
        self.current_orders = dict()        # order_id -> info about order. 
        self.executed_orders = dict()       # order_id -> info about order.
        self.cancelled_orders = dict()      # order_id -> info about order.
        self.orderbook_volumes = dict()     # is of the following form:
                                            # {
                                            #   'ask_volumes' : list() 
                                            #   'bid_volumes' : list()
                                            # }
        self.orderbook_volumes['bid_volumes'] = list()
        self.orderbook_volumes['ask_volumes'] = list()

        self.last_orders = list()           # last order ids chronologically ordered.
        self.fill_times = list()            # records the time it took for an order to fully fill or get cancelled.

        self.hedged_position = 0            # keeps track of hedged position.
        self.position = 0                   # keeps track of regular position.
        self.window_size = 10               # manually set? should this be computed?
        self.last_sequence_processed = -1   # helps detect old and out-of-order orderbook snapshots.
        self.timer = 0                      # helps track time during execution
    
    def total_volume_of_current_orders(self) -> int:
        '''
        need to keep track of total volume within our current orders,
        so it never exceeds some value.

        returns: dict with total bid and total ask volume of current orders.
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
    
    def average_time_to_fill(self) -> float:
        '''
        returns: average time it takes for order to fully fill or get cancelled if we have enough data.
                 returns DESIRED_AVG_TIME_TO_FILL flag otherwise.
        '''
        if len(self.fill_times) < self.window_size:
            return DESIRED_AVG_TIME_TO_FILL
        return sum(self.fill_times[-self.window_size:]) / len(self.fill_times[-self.window_size:])

    def average_fill_ratio(self) -> float:
        '''
        returns: portion of our orders that gets filled, on average.
        '''
        fill_ratios = list()
        # for each id in the last however many orders orders
        for id in self.last_orders[-self.window_size:]:
            # order is either current or executed or cancelled orders, filled ratio still applies!
            if id in self.executed_orders:
                # calc the actual ratio = filled/volume.
                ratio = self.executed_orders[id]['filled'] / self.executed_orders[id]['volume']
            elif id in self.cancelled_orders:
                # calc the actual ratio = filled/volume.
                if self.cancelled_orders[id]['filled'] != 0:
                    ratio = self.cancelled_orders[id]['filled'] / self.cancelled_orders[id]['volume'] 
                else:
                    continue
            elif id in self.current_orders:
                # calc the actual ratio = filled/volume.
                ratio = self.current_orders[id]['filled'] / self.current_orders[id]['volume']               
            # append that thang to the end of ratios.
            fill_ratios.append(ratio)

        return sum(fill_ratios) / len(fill_ratios)


    def average_volume(self, order_type) -> float:
        '''
        returns: average volume in the orderbook over the past window_size snapshots.
        '''
        if order_type == Side.BID or order_type == Side.ASK:
            return sum(self.orderbook_volumes[order_type][-self.window_size:]) / len(self.orderbook_volumes[order_type][-self.window_size:])
        else:
            self.logger.warning(f'THIS BRANCH SHOULD NEVER BE EXECUTED!')

    def order_volume_to_avg_volume_ratio(self, order_id, order_type) -> float:
        '''
        returns: ratio of given order volume to average_volume().
        '''
        if order_id in self.executed_orders:
            return self.executed_orders[order_id]['volume'] / self.average_volume(order_type)
        elif order_id in self.cancelled_orders:
            return self.cancelled_orders[order_id]['volume'] / self.average_volume(order_type)
        elif order_id in self.current_orders:
            return self.current_orders[order_id]['volume'] / self.average_volume(order_type)

    def record_order(self, order_id, order_type, price, volume, lifespan, corresponding_trade_id) -> None:
        '''
        records order into current_orders.

        returns: nothing.
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
            'corresponding_trade_id' : corresponding_trade_id   # each bid has corresponding ask and vice versa.
        }

    def hedge_record_order(self, order_id, order_type, price, volume, lifespan) -> None:
        '''
        records order into hedged_current_orders.

        returns: nothing.
        '''
        self.logger.info(f'LOGGING HEDGE ORDER {order_id}')
        self.hedged_current_orders[order_id] = {
            'id' : order_id,            # order id.
            'type' : order_type,        # Side.BID or Side.ASK.
            'price' : price,            # price of order.
            'filled' : 0,               # amount of shares in order that were filled.
            'volume' : volume,          # total size of the order.
            'lifespan' : lifespan,      # good for day vs fill and kill
        }

    def check_wash_order(self, order_type, order_price) -> bool:
        '''
        checks whether the order we are about to place is a wash order.
        it is illegal to lift your own ask or hit your own bid.

        returns: true if the order is possibly a wash order.
        '''
        for order_id, order in self.current_orders.items():
            if order['type'] != order_type and order['price'] == order_price:
                self.logger.info(f'PREVENTING A WASH ORDER, CANNOT PLACE ONE HERE')
                return True
        return False

    def place_immediate_single_order(self, type, price, volume) -> None:
        '''
        function for the purpose of unwinding a position we have just entered;
        because it unwinds, we do not need to check whether we exceed max order limit (we just got filled for an order).

        returns: nothing.
        '''
        self.logger.info(f'PLACING IMMEDIATE IMPULSE ORDER, RESISTING DIRECTIONAL PRESSURE.')
        # if it isn't a wash order, we record this single-sided order, and send it out.
        if not self.check_wash_order(type, price):
            next_id = next(self.order_ids)
            self.record_order(next_id, type, price, volume, Lifespan.FILL_AND_KILL, None)
            self.send_insert_order(next_id, type, price, volume, Lifespan.FILL_AND_KILL)

    def place_two_orders(self, bid, bid_volume, ask, ask_volume) -> None:
        '''
        places two orders at the given bid and ask with given volumes,
        inserts order into current_orders data structure.
        in the function, bid total temp and ask total temp are the
        total volumes of current bids and asks we have placed.

        returns: nothing.
        '''
        # make sure we are not placing orders that can potentially exceed our position limit.
        bid_total_temp = self.total_volume_of_current_orders()['bid'] + self.position + bid_volume
        ask_total_temp = -self.total_volume_of_current_orders()['ask'] + self.position - ask_volume
        
        # make sure we are not placing orders that are potential wash trades.
        # this function could be made more specific, but for now it's kept general as it works.
        bid_wash_flag = self.check_wash_order(Side.BID, bid)
        asK_wash_flag = self.check_wash_order(Side.ASK, ask)
        
        # previously, I had two separate if statements for the two orders.
        # however, we MUST place a bid and an ask whenver placing orders to
        # so that we can avoid accumulating a directional position.
        if bid_volume > 0 and ask_volume > 0 \
            and len(self.current_orders) + 2 <= LIVE_ORDER_LIMIT \
            and bid_total_temp < POSITION_LIMIT \
            and ask_total_temp > -POSITION_LIMIT \
            and not bid_wash_flag \
            and not asK_wash_flag:

            # then we go ahead and place the two orders!!!
            self.logger.info(f'PLACING ORDER AT BID {bid} VOLUME {bid_volume} ASK {ask} VOLUME {ask_volume}!')
            
            # get the ids of the two orders.
            bid_id = next(self.order_ids)
            ask_id = next(self.order_ids)
            
            # record orders into self.current_orders.
            self.record_order(bid_id, Side.BID, bid, bid_volume, Lifespan.GOOD_FOR_DAY, ask_id)
            self.record_order(ask_id, Side.ASK, ask, ask_volume, Lifespan.GOOD_FOR_DAY, bid_id)
            
            # keep chronological track of order ids for sake of avg fill rate and avg fill time caclulations
            self.last_orders.append(bid_id)
            self.last_orders.append(ask_id)

            # send the orders out!
            self.send_insert_order(bid_id, Side.BID, bid, bid_volume, Lifespan.GOOD_FOR_DAY) # LIMIT ORDER = GOOD FOR DAY ORDER
            self.send_insert_order(ask_id, Side.ASK, ask, ask_volume, Lifespan.GOOD_FOR_DAY) # LIMIT ORDER = GOOD FOR DAY ORDER
        else:
            self.logger.info(f'CANNOT PLACE PAIR OF ORDERS AT THIS MOMENT, RISK PARAMETERS DO NOT ALLOW FOR THIS.')

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error.

        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        """
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0 \
            and (client_order_id in self.current_orders \
                or client_order_id in self.executed_orders \
                or client_order_id in self.cancelled_orders):

            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your hedge orders is filled.

        The price is the average price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info(f'RECEIVED HEDGE FILLED MESSAGE PRICE {price} VOLUME {volume}')
        
        self.hedged_current_orders[client_order_id]['filled'] += volume
        
        if self.hedged_current_orders[client_order_id]['type'] == Side.BID:
            self.hedged_position += volume
        elif self.hedged_current_orders[client_order_id]['type'] == Side.ASK:
            self.hedged_position -= volume
        else:
            self.logger.warning(f'THIS BRANCH SHOULD NEVER BE EXECUTED')

        if self.hedged_current_orders[client_order_id]['filled'] == self.hedged_current_orders[client_order_id]['volume']:
            del self.hedged_current_orders[client_order_id]

        # self.logger.info(f'EXITING CURRENT POSITION IS {self.position} AND HEDGED POSITION IS {self.hedged_position}')

    def check_hedged_orders_status(self) -> None:
        '''
        checks if we have hedged orders that have not been filled fully.
        '''
        self.logger.info(f'CHECKING HEDGED ORDERS FUNCTION.')

        for order_id, order in self.hedged_current_orders.items():
            if order['filled'] != 0 and order['filled'] != order['volume']:
                # the hedged order was only partially filled! must place new hedge order.
                next_id = next(self.order_ids)
                # copy mapping to new id
                self.hedged_current_orders[next_id] = self.hedged_current_orders[order_id]
                # set the new id in the order description.
                self.hedged_current_orders[next_id]['id'] = next_id
                # set the volume of the new order.
                self.hedged_current_orders[next_id]['volume'] = self.hedged_current_orders[order_id]['volume'] - self.hedged_current_orders[order_id]['filled']
                # delete previous order id mapping.
                del self.hedged_current_orders[order_id]
                # send out the new hedged order.
                self.logger.info('HEDGE ORDER WAS ONLY PARTIALLY FILLED, RESENDING HEDGED ORDER NOW.')
                trade_type = self.hedged_current_orders[next_id]['type']
                price = MAX_ASK_NEAREST_TICK if trade_type == Side.BID else MIN_BID_NEAREST_TICK
                volume = self.hedged_current_orders[next_id]['volume']
                self.send_hedge_order(next_id, trade_type, price, volume)

    # THIS FUNCTION IS NOT IN USE, BUT DON't GET RID OF IT JUST YET.
    def check_current_orders_out_of_bounds(self, bid, ask) -> None:
        '''
        checks if a current order is priced outside the interval we like.
        if so, we modify this order's volume to decrease its significance but keep its priority.

        returns: nothing.
        '''
        self.logger.info(f'CHECKING CURRENT ORDERS OUT OF BOUNDS!')
        for order_id, order in self.current_orders.items():
            spread = ask - bid
            if order['price'] < bid:
                # order out of bounds, decrease its significance.
                self.logger.info(f'UPDATING ORDER {order_id} TO HALF VOLUME')
                self.current_orders[order_id]['volume'] = int(order['volume'] / 3)
                self.send_amend_order(order_id, int(order['volume'] / 3))
            elif order['price'] > ask:
                # order out of bounds, decrease its significance.
                self.logger.info(f'UPDATING ORDER {order_id} TO HALF VOLUME')
                self.current_orders[order_id]['volume'] = int(order['volume'] / 3)
                self.send_amend_order(order_id, int(order['volume'] / 3))
            else:
                # order within bounds, check if we can increase its significance.
                self.logger.info(f'UPDATING ORDER {order_id} TO FULL VOLUME ASAP ROCKY')
                if order['volume'] < int(LOT_SIZE / 2):
                    self.current_orders[order_id]['volume'] = LOT_SIZE
                    self.send_amend_order(order_id, LOT_SIZE)

    def check_current_orders_ttl(self) -> None:
        '''
        checks every current order to make sure its ttl has not yet burnt out.
        bids and corresponding asks have the same ttl, no need to worry about tracking that here.
        '''
        self.logger.info(f'CHECKING CURRENT ORDERS TIME TO LIVE!')

        cancelled_ids = list()
        for order_id, order in self.current_orders.items():
            if self.timer - order['placed_at'] >= ORDER_TTL:
                self.logger.info(f'ORDER {order_id} TIMED OUT, CANCELLING')
                cancelled_ids.append(order_id)
            else:
                self.logger.info(f'ORDER {order_id} IS STILL GUCCI')

        for id in cancelled_ids:
            self.send_cancel_order(id)
    
    def is_orderbook_symmetric(self, bid_volumes, ask_volumes) -> bool:
        '''
        Returns: true if orderbook bid and ask volumes are similar.
                 false otherwise.
        '''
        # here is a rough implementation that looks at the average volume of each side and compares
        # that to the current volume.
        # If both sides are larger, that's okay.
        # If one side is below average and one side is above, orderbook is asymmetric.
        # If both sides are below, that's also okay.
        
        avg_bid_volume = self.average_volume(Side.BID) # average volume in a rolling window of size self.window_size.
        avg_ask_volume = self.average_volume(Side.ASK) # average volume in a rolling window of size self.window_size.
        if sum(bid_volumes) / len(bid_volumes) > avg_bid_volume \
            and sum(ask_volumes) / len(ask_volumes) > avg_ask_volume:
            return True # BALANCED.
        elif sum(bid_volumes) / len(bid_volumes) < avg_bid_volume \
            and sum(ask_volumes) / len(ask_volumes) < avg_ask_volume:
            return True # BALANCED.
        else:
            return False # UNBALANCED

    def decrease_trading_activity(self, active_order_ceiling) -> None:
        '''
        Function to cancel a certain amount of orders to get below active_order_ceiling.
        Function will be called whenever we believe a directional move is about to happen,
        i.e. when the orderbook of volumes is heavily skewed.
        '''
        self.logger.info(f'DECREASING TRADING ACTIVITY DOWN TO {active_order_ceiling} ORDERS')
        # base cases: we already are below the ceiling.
        #             or active order ceiling doesn't make sense.
        if len(self.current_orders) - active_order_ceiling <= 0 \
            or active_order_ceiling < 0:
            return
        
        # otherwise, we will go ahead and remove the oldest orders we have on record.
        # this is probably not a good idea, we lose our time priority when cancelling like this.
        
        # process: get the current order's placed at times.
        times_by_id = list()
        for order_id, order in self.current_orders.items():
            if order['type'] == Side.BID:
                times_by_id.append((order_id, order['placed_at']))

        # sort order ids by placed_at time. default sort mode is ascending,
        # meaning oldest orders (smallest placed_at time) in the beginning.
        times_by_id = sorted(times_by_id, key=lambda tup: tup[1])

        # we now want to delete these oldest orders.
        items_to_delete = int((len(self.current_orders) - active_order_ceiling) / 2)
        for i in range(items_to_delete):
            if len(times_by_id) > 0:
                orig_id = times_by_id.pop(0)[0]
                corr_id = self.current_orders[orig_id]['corresponding_trade_id']
                if corr_id != None:
                    self.send_cancel_order(corr_id)             # delete the corresponding ask first.
                self.send_cancel_order(orig_id)   # delete the bid.

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically to report the status of an order book.

        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """
        self.timer += 1 # increment time counter.
        self.logger.info("received order book for instrument %d with sequence number %d", instrument,
                         sequence_number)
        
        # first, we MUST make sure we are always as hedged as can be at the best price.
        # this should not come after placing orders, this should happen here because
        # if we are unhedged we should not be placing more orders... hope this makes sense.
        self.check_hedged_orders_status()
        
        # next, we MUST make sure any order that is timed out gets canceled and
        # is not interracting with the orderbook we will see in the next snapshot.
        self.check_current_orders_ttl()

        # we CAN check if orders placed are currently outside the optimal interval,
        # but with this strategy there's a better approach: each ask has a corresponding bid,
        # and the actions are made on one based on what happens to the other.
        # self.check_current_orders_out_of_bounds(theoretical_bid, theoretical_ask)

        if bid_prices[0] == 0 or ask_prices[0] == 0 or self.theoretical_price == 0:
            pass # first couple iterations, do nothing.
        elif instrument == Instrument.ETF:
            
            # check if we received an out-of-order sequence!
            if sequence_number < 0 or sequence_number <= self.last_sequence_processed:
                self.logger.info(">>>OLD INFORMATION RECEIVED, SKIPPING!")
                return
            self.last_sequence_processed = sequence_number # set the sequence number since we are now processing it.

            # next, we need to aggregate the volumes and append it to the orderbook_volumes list.
            self.orderbook_volumes['bid_volumes'].append(sum(bid_volumes))
            self.orderbook_volumes['ask_volumes'].append(sum(ask_volumes))

            # weighted average to compute theoretical_price.
            total_volume = ask_volumes[0] + bid_volumes[0]
            self.theoretical_price = bid_prices[0]*(ask_volumes[0] / total_volume) \
                                    + ask_prices[0]*(bid_volumes[0] / total_volume)

            # standard deviation to use for spread.
            # i don't really know what the best spread is man.
            # gonna try to use without scaling first.
            self.spread = 2 * np.sqrt(np.std(np.array(ask_prices + bid_prices))) 
            # scale = 1 / (self.average_time_to_fill() / (ORDER_TTL / 2))
            # spread = scale * spread if scale > 0 else spread

            # if we are just starting without information, we have a very simplistic trading approach.
            if True: # this will be changed to "if we don't have enough information yet"
                # need to find fair price using JUST weighted average,
                new_bid = self.theoretical_price - self.spread / 2
                new_ask = self.theoretical_price + self.spread / 2
                # new_ask and new_bid are probably not to the
                # tick_size_in_cents correct, need to round them up.
                new_bid_by_tick = int(new_bid - new_bid % TICK_SIZE_IN_CENTS) # more conservative to round bid down.
                new_ask_by_tick = int(new_ask + TICK_SIZE_IN_CENTS - new_ask % TICK_SIZE_IN_CENTS) # more conservative to round ask up.
                
                self.logger.warning(f'REAL INTERVAL [{bid_prices[0]}, {ask_prices[0]}] OUR INTERVAL [{new_bid_by_tick}, {new_ask_by_tick}]')
                # 7 cases, 2 types of aciton:
                if new_bid_by_tick >= bid_prices[0] and new_ask_by_tick <= ask_prices[0]:
                    # our interval is WITHIN the actual market interval, GREAT!
                    self.logger.info("our interval is WITHIN the actual market interval")
                    self.place_two_orders(new_bid_by_tick, LOT_SIZE, new_ask_by_tick, LOT_SIZE)
                elif new_ask_by_tick >= ask_prices[0] and new_bid_by_tick <= bid_prices[0]:
                    # our interval CONTAINS the actual market interval, this is a little interesting, needs some thought.
                    self.logger.info("our interval CONTAINS the actual market interval")
                    self.place_two_orders(new_bid_by_tick, LOT_SIZE, new_ask_by_tick, LOT_SIZE)
                elif new_ask_by_tick == ask_prices[0] and new_bid_by_tick == bid_prices[0]:
                    # our interval perfectly MATCHES the actual market interval, also a little interesting, needs some thought.
                    self.logger.info("our interval perfectly MATCHES the actual market interval")
                    # self.place_two_orders(new_bid_by_tick, LOT_SIZE, new_ask_by_tick, LOT_SIZE)
                else:
                    # otherwise we need to decrease trading activity because the interval we have
                    # is on the right or left of the actual interval. I propose we calculate the order
                    # ceiling we want based on each case and decrease trading activity.
                    # basically, we DO NOT want to get caught lacking.
                    order_ceiling = 0 # for now, i am literally deleting all of them if we are in these cases.
                    if new_bid_by_tick >= ask_prices[0]:
                        # our interval is completely ABOVE current market interval.
                        self.decrease_trading_activity(order_ceiling)
                        self.logger.info("our interval is completely ABOVE current market interval")
                    elif new_bid_by_tick > bid_prices[0] and new_ask_by_tick > ask_prices[0]:
                        # our interval OVERLAPS actual market interval on the right side.
                        self.decrease_trading_activity(order_ceiling)
                        self.logger.info("our interval OVERLAPS actual market interval on the right side")
                    elif new_ask_by_tick <= bid_prices[0]:
                        # our interval is completely BELOW current market interval.
                        self.decrease_trading_activity(order_ceiling)
                        self.logger.info("our interval is completely BELOW current market interval")
                    elif new_bid_by_tick < bid_prices[0] and new_ask_by_tick < ask_prices[0]:
                        # our interval OVERLAPS actual market interval on the left side.
                        self.decrease_trading_activity(order_ceiling)
                        self.logger.info("our interval OVERLAPS actual market interval on the left side")
                    else:
                        # i don't think there are more cases, but should handle all branches of execution just in case.
                        self.logger.warning(f'REAL INTERVAL [{bid_prices[0]}, {ask_prices[0]}] OUR INTERVAL [{new_bid_by_tick}, {new_ask_by_tick}]')
                        pass

            else:
                # we have information, so modify the theoretical_price and spread and execution duration of the orders.
                # we want to asymetrically change the spread we have based on the current order book spread.
                # after that, we want to determine our order size via a ratio of the average volume, since we now have data.
                self.logger.warning(f'BRANCH THAT TRADES USING INFORMATION NOT IMPLEMENTED!!!')
                pass
        else:
            self.logger.info(f'NOT DOING ANYTHING HERE BECAUSE INSTRUMENT IS NOT ETF!')

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        # any time an order gets filled,
        # check what the latest orderbook volume is,
        # compare it to average orderbook volume,
        # and if the volume is below half the average,
        # we cancel the corresponding bid or ask order of the executed clinet_order_id,
        # and we place a fill and kill at the price we just got filled at, in the opposite direction.
        if self.current_orders[client_order_id]['type'] == Side.ASK:
            # bid just got executed, must look at bid volume
            if self.position > OUR_POSITION_LIMIT and self.orderbook_volumes['ask_volumes'] < self.average_volume(Side.ASK) / 2:
                self.logger.info(f'IMPULSE ORDER SMACKING THAT THANG HERE')
                # cancel the order, place a new one with a fill or kill method.
                corresponding_order_id = self.current_orders[client_order_id]['corresponding_trade_id']
                volume_to_trade = self.current_orders[corresponding_order_id]['volume']
                self.send_cancel_order(corresponding_order_id)
                # place immediate fill or kill for the volume of the order we just cancelled.
                self.place_immediate_single_order(Side.BID, price, volume_to_trade)
            else:
                pass # safe to keep the other order, it is likely gonna get hit.
        elif self.current_orders[client_order_id]['type'] == Side.BID:
            # bid just got executed, must look at bid volume
            if self.position > OUR_POSITION_LIMIT and self.orderbook_volumes['bid_volumes'] < self.average_volume(Side.BID) / 2:
                self.logger.info(f'IMPULSE ORDER SMACKING THAT THANG HERE')
                # cancel the order, place a new one with a fill or kill method.
                corresponding_order_id = self.current_orders[client_order_id]['corresponding_trade_id']
                volume_to_trade = self.current_orders[corresponding_order_id]['volume']
                self.send_cancel_order(corresponding_order_id)
                # place immediate fill or kill for the volume of the order we just cancelled.
                self.place_immediate_single_order(Side.ASK, price, volume_to_trade)
            else:
                pass # safe to keep the other order, it is likely gonna get hit.
        else:
            self.logger.warning(f'THIS BRANCH SHOULD NEVER BE EXECUTED!!!')

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
        fill_volume -= self.current_orders[client_order_id]['filled'] # fill_volume is total filled, but order could've been partially filled multiple times.
                                                                      # we thus need to difference in status, not the fill_volume on its own.
        if remaining_volume > 0 and fill_volume == 0:
            # order has just been created!!!
            self.logger.info(f'THE ORDER {client_order_id} WAS JUST CREATED!')
            return
        elif remaining_volume == 0 and fill_volume > 0:
            # order has been filled and executed!!!
            self.logger.info(f'THE ORDER {client_order_id} HAS BEEN FILLED FULLY AND EXECUTED!')
            time = self.timer - self.current_orders[client_order_id]['placed_at']
            self.fill_times.append(time) # record how long it took for this to happen.
            self.logger.info(f'THE ORDER TOOK {time} SECONDS TO GET FULLY FILLED!')
            order = self.executed_orders[client_order_id] = self.current_orders[client_order_id]
            del self.current_orders[client_order_id]
        elif remaining_volume == 0 and fill_volume == 0:
            # order has been cancelled!!!
            self.logger.info(f'THE ORDER {client_order_id} HAS BEEN CANCELLED!')
            time = self.timer - self.current_orders[client_order_id]['placed_at']
            self.fill_times.append(time) # record how long it took for this to happen.
            self.logger.info(f'THE ORDER TOOK {time} SECONDS TO GET CANCELLED!')
            order = self.cancelled_orders[client_order_id] = self.current_orders[client_order_id]
            del self.current_orders[client_order_id]
        else:
            # order has been partially filled, not cancelled, not executed, not just created.
            self.logger.info(f'THE ORDER {client_order_id} WAS PARTIALLY FILLED!')
            order = self.current_orders[client_order_id]

        # some shares were bought, this is our reaction to that.        
        if fill_volume > 0:
            next_id = next(self.order_ids)
            if order['type'] == Side.BID:
                self.position += fill_volume # adjust our position here based on what type of order was exceuted.
                self.hedge_record_order(next_id, Side.ASK, MIN_BID_NEAREST_TICK, fill_volume, Lifespan.FILL_AND_KILL)
                self.send_hedge_order(next_id, Side.ASK, MIN_BID_NEAREST_TICK, fill_volume)
            elif order['type'] == Side.ASK:
                self.position -= fill_volume # adjust our position here based on what type of order was exceuted.
                self.hedge_record_order(next_id, Side.BID, MAX_ASK_NEAREST_TICK, fill_volume, Lifespan.FILL_AND_KILL)
                self.send_hedge_order(next_id, Side.BID, MAX_ASK_NEAREST_TICK, fill_volume)
            else:
                self.logger.error('ORDER TYPE IS MESSED UP')
            
            order['filled'] += fill_volume # finally, update the filled amount of the order we just worked with.

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically when there is trading activity on the market.

        The five best ask (i.e. sell) and bid (i.e. buy) prices at which there
        has been trading activity are reported along with the aggregated volume
        traded at each of those price levels.

        If there are less than five prices on a side, then zeros will appear at
        the end of both the prices and volumes arrays.
        """