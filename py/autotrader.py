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
from turtle import pos, position
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
    '''
    -_- LiquidBears Awesome Autotrader -_-
    '''

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.ask_id = self.ask_price = self.bid_id = self.bid_price = self.position = 0

        self.hedged_position = 0 # keeps track of hedged position.
        self.hedged_current_orders = dict() # keeps track of orders we just tried to hedge.

        self.current_orders = dict() # order_id -> info about order. 
        self.executed_orders = dict() # order_id -> info about order.
        self.cancelled_orders = dict() # order_id -> info about order.
        
        self.orderbook_volumes = list() # for average volume.
        self.last_orders = list() # last order ids chronologically ordered.
        
        # self.average_time_to_fill = 0 # TBD.
        self.window_size = 20 # manually set? should this be computed?

        # we want to discard old orderbook snapshots when we get them.
        # for this, we keep a last_sequence_processed variable.
        self.last_sequence_processed = 0
    
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
                self.logger.info(f'ITERATION: FOUND BID, TOTAL BIDS {total_bids}')
            elif order['type'] == Side.ASK:
                total_asks += (order['volume'] - order['filled'])
                self.logger.info(f'ITERATION: FOUND ASK, TOTAL ASKS {total_asks}')
   
        return {
            'bid' : total_bids,
            'ask' : total_asks
        }

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
        self.logger.info(f'LOGGING ORDER {order_id}')
        self.current_orders[order_id] = {
            'id' : order_id,            # order id.
            'type' : order_type,        # Side.BID or Side.ASK.
            'price' : price,            # price of order.
            'filled' : 0,               # amount of shares in order that were filled.
            'volume' : volume,          # total size of the order.
            'lifespan' : lifespan,      # good for day vs fill and kill
        }

    def hedge_record_order(self, order_id, order_type, price, volume, lifespan) -> None:
        '''
        records order into hedged_current_orders.
        returns nothing.
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

    def place_orders_at_two_levels(self, bid, bid_volume, ask, ask_volume) -> None:
        '''
        places two orders at the given bid and ask with given volumes,
        inserts order into current_orders data structure.
        returns: nothing.
        '''
        bid_total_temp = self.total_volume_of_current_orders()['bid'] + self.position + bid_volume
        if bid_volume > 0 and len(self.current_orders) < LIVE_ORDER_LIMIT and bid_total_temp < POSITION_LIMIT:
            self.logger.info(f'PLACING ORDER AT BID {bid} VOLUME {bid_volume}!')
            # placing the bid order here.
            # size could be based on other factors than just lot LOT_SIZE variable!!!
            self.bid_id = next(self.order_ids)
            # record the order as as currently-placed order.
            self.record_order(self.bid_id, Side.BID, bid, bid_volume, Lifespan.GOOD_FOR_DAY)
            self.last_orders.append(self.bid_id)
            self.bid_price = bid
            # send the order out.
            self.send_insert_order(self.bid_id, Side.BID, bid, bid_volume, Lifespan.GOOD_FOR_DAY) # LIMIT ORDER = GOOD FOR DAY ORDER
            self.bids.add(self.bid_id)
    
        ask_total_temp = -self.total_volume_of_current_orders()['ask'] + self.position - ask_volume
        if ask_volume > 0 and len(self.current_orders) < LIVE_ORDER_LIMIT and ask_total_temp > -POSITION_LIMIT:
            self.logger.info(f'PLACING ORDER AT ASK {ask} VOLUME {ask_volume}!')
            # placing the ask order here.
            # size could be based on other factors than just lot LOT_SIZE variable!!!
            self.ask_id = next(self.order_ids)
            # record the order as as currently-placed order.
            self.record_order(self.ask_id, Side.ASK, ask, ask_volume, Lifespan.GOOD_FOR_DAY)
            self.last_orders.append(self.ask_id)
            self.ask_price = ask
            # send the order out.
            self.send_insert_order(self.ask_id, Side.ASK, ask, ask_volume, Lifespan.GOOD_FOR_DAY) # LIMIT ORDER = GOOD FOR DAY ORDER
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
        # self.logger.info("received hedge filled for order %d with average price %d and volume %d", client_order_id,
                        #  price, volume)
        self.logger.info(f'HEDGE POSITION FILLED, ID = {client_order_id}, price = {price}, volume = {volume}')
        self.logger.info(f'ENTERING CURRENT POSITION IS {self.position} AND HEDGED POSITION IS {self.hedged_position}')

        self.hedged_current_orders[client_order_id]['filled'] += volume
        
        if self.hedged_current_orders[client_order_id]['type'] == Side.BID:
            self.hedged_position += volume
        elif self.hedged_current_orders[client_order_id]['type'] == Side.ASK:
            self.hedged_position -= volume
        else:
            self.logger.info(f'FOR SOME REASON IT IS NEITHER A BID NOR AN ASK???')

        if self.hedged_current_orders[client_order_id]['filled'] == self.hedged_current_orders[client_order_id]['volume']:
            del self.hedged_current_orders[client_order_id]

        self.logger.info(f'EXITING CURRENT POSITION IS {self.position} AND HEDGED POSITION IS {self.hedged_position}')

    def check_hedged_orders_status(self) -> None:
        '''
        checks if we have hedged orders that have not been filled fully.
        '''
        self.logger.info(f'ENTER CHECKING HEDGED ORDERS AVAILABILITY, THERE ARE {len(self.hedged_current_orders)} HEDGED ORDERS CURRENTLY.')
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


    def check_current_orders_optimality(self, bid, ask) -> None:
        '''
        checks every current order to make sure its within the current optimal interval.
        returns: nothing.
        '''
        self.logger.info(f'CURRENT ORDERS BEING CHECKED HERE!')
        temp_bid_total = self.total_volume_of_current_orders()['bid']
        temp_ask_total = self.total_volume_of_current_orders()['ask']
        self.logger.info(f'TOTAL OF CURRENT BUY ORDERS IS {temp_bid_total}, TOTAL OF CURRENT SELL ORDERS IS {temp_ask_total}')
        cancelled_ids = list()
        for order_id, order in self.current_orders.items():
            if order['price'] < bid or order['price'] > ask:
                self.logger.info(f'CANCELLING ORDER {order_id}')
                cancelled_ids.append(order_id)
            else:
                self.logger.info(f'ORDER {order_id} IS STILL GUCCI')

        # block other processes until we cancel all orders here.
        for id in cancelled_ids:
            self.send_cancel_order(id)

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
        self.logger.info(f'RECEIVED ORDERBOOK SNAPSHOT, POSITION = {self.position} AND HEDGE = {self.hedged_position}')

        # check if we received an out-of-order sequence!
        if sequence_number < self.last_sequence_processed:
            self.logger.info("OLD INFORMATION RECEIVED, SKIPPING!")
            return
        self.last_sequence_processed = sequence_number # set the sequence number since we are now processing it.
        
        if bid_prices[0] == 0 or ask_prices[0] == 0:
            self.logger.info("LOOKS LIKE DA FIRST ITERATION! DOING NOTHING!")
        elif instrument == Instrument.ETF:
            # self.logger.info("RECEIVED ETF ORDER BOOK!")
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
            # self.logger.info(f'THEORETICAL PRICE CALCULATED TO BE {theoretical_price}.') 

            # standard deviation to use for spread.
            spread = np.sqrt(np.std(np.array(ask_prices + bid_prices)))*2

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
                    self.place_orders_at_two_levels(new_bid_by_tick, 2*LOT_SIZE, new_ask_by_tick, 2*LOT_SIZE)
                elif new_ask_by_tick == ask_prices[0] and new_bid_by_tick == bid_prices[0]:
                    # our interval perfectly MATCHES the actual market interval, also a little interesting, needs some thought.
                    self.logger.info("our interval perfectly MATCHES the actual market interval")
                    # self.place_orders_at_two_levels(new_bid_by_tick, LOT_SIZE, new_ask_by_tick, LOT_SIZE)
                
                # if we now have too many current orders, we should cancel some old ones.
                # this logic should be rewritten to cancel orders when they're no longer optimal!
                self.check_current_orders_optimality(new_bid_by_tick, new_ask_by_tick)
                self.check_hedged_orders_status()
                
            else:
                # we have information, so modify the theoretical_price and spread and execution duration of the orders.
                # we want to asymetrically change the spread we have based on the current order book spread.
                # after that, we want to determine our order size via a ratio of the average volume, since we now have data.
                pass
        else:
            pass
                

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        # self.logger.info(f'ORDER {client_order_id} HAS BEEN FILLED AT {price} WITH VOLUME {volume}')

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
        fill_volume -= self.current_orders[client_order_id]['filled']
        if remaining_volume == 0 and fill_volume > 0:
            self.logger.info(f'THE ORDER {client_order_id} HAS BEEN FILLED FULLY AND EXECUTED!')
            # order has been filled and executed!!!
            self.executed_orders[client_order_id] = self.current_orders[client_order_id]
            del self.current_orders[client_order_id]
            order = self.executed_orders[client_order_id]
        elif remaining_volume == 0 and fill_volume == 0:
            self.logger.info(f'THE ORDER {client_order_id} HAS BEEN CANCELLED!')
            # order has been cancelled!!!
            self.cancelled_orders[client_order_id] = self.current_orders[client_order_id]
            del self.current_orders[client_order_id]
            order = self.cancelled_orders[client_order_id]
        elif remaining_volume > 0 and fill_volume == 0:
            self.logger.info(f'THE ORDER {client_order_id} WAS JUST CREATED!')
            # order has just been created!!!
            return
        else:
            self.logger.info(f'THE ORDER {client_order_id} WAS PARTIALLY FILLED!')
            # order has been partially filled, not cancelled, not executed, not just created.
            order = self.current_orders[client_order_id]

        # some shares were bought, this is our reaction to that.        
        if fill_volume > 0:
            self.logger.info(f'FILL VOLUME WAS {fill_volume}, NOT HEDGING HERE ANYMORE!')
            next_id = next(self.order_ids)
            if order['type'] == Side.BID:
                self.logger.info(f'OUR POSITION IS CHANGIN HERE! {self.position} => {self.position + fill_volume}')
                self.position += fill_volume
                self.hedge_record_order(next_id, Side.ASK, MIN_BID_NEAREST_TICK, fill_volume, Lifespan.FILL_AND_KILL)
                self.send_hedge_order(next_id, Side.ASK, MIN_BID_NEAREST_TICK, fill_volume)
            elif order['type'] == Side.ASK:
                self.logger.info(f'OUR POSITION IS CHANGIN HERE! {self.position} => {self.position - fill_volume}')
                self.position -= fill_volume
                self.hedge_record_order(next_id, Side.BID, MAX_ASK_NEAREST_TICK, fill_volume, Lifespan.FILL_AND_KILL)
                self.send_hedge_order(next_id, Side.BID, MAX_ASK_NEAREST_TICK, fill_volume)
            else:
                self.logger.error('ORDER TYPE IS MESSED UP')
            
            # next, we must update the order's filled amount.
            order['filled'] += fill_volume

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
        # self.logger.info("received trade ticks for instrument %d with sequence number %d", instrument,
                        #  sequence_number)
