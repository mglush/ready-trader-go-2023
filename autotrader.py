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
from textwrap import fill
from tkinter.tix import MAX
import numpy as np

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side

LOT_SIZE = 10
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS

UNHEDGED_LOTS_LIMIT = 10 # volume limit in lots.
MAX_TIME_UNHEDGED = 58  # time limit in seconds.
LAMBDA_ONE = 0.5    # our first constant, by which we decide whether order imbalance is up or down or flat.

class AutoTrader(BaseAutoTrader):
    '''
    LiquidBears AutoTrader.
    '''

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)

        self.our_spread_bid_id = self.our_spread_bid_price = 0          # state of the bid of our interval.
        self.our_spread_ask_id = self.our_spread_ask_price = 0          # state of the ask of our interval.

        self.hedge_bid_id = self.hedge_ask_id = 0                       # state of the hedge order we placed so we can adjust hedged position in the correct direction.
        self.last_fak_id = self.last_fak_price = 0                      # state of the last fill and kill we sent.
        
        self.position = self.hedged_position = 0                        # state of each position's size.
        self.p_prime_0 = self.p_prime_1 = 0                             # weighted averages of last tick update.
        self.last_ticks_sequence = self.last_order_book_sequence = -1   # last message we processed (one for ticks one for order book).

        self.we_are_hedged = True                                       # flag to set for when we are set vs not.
        self.time_of_last_imbalance = self.event_loop.time()            # used to hedge as a last resort before the minute runs out.

        self.bids = set()                                               # we need these in case an order gets filled as we are placing a new one and the bid id doesn't match up.
        self.asks = set()                                               # we need these in case an order gets filled as we are placing a new one and the bid id doesn't match up.

    #-----------------------------------HELPER FUNCTIONS WE USE-----------------------------------------------#

    def make_a_market(self, bid, bid_volume, ask, ask_volume) -> None:
        '''
        Tries to place both a bid and an ask at the provided prices and volumes.
        If one of the orders cannot be placed, we still place the other one.

        Parameters:
        bid (int):          price to place bid at.
        bid_volume (int):   how many shares to bid?
        ask (int):          price to place ask at.
        ask_volume (int):   how many shares to ask?
        '''

        # try to place em both at once if we can. otherwise place one of them.
        if bid > 0 and ask > 0 \
            and self.position + LOT_SIZE < POSITION_LIMIT \
            and self.position - LOT_SIZE > -POSITION_LIMIT \
            and bid != self.our_spread_bid_price \
            and ask != self.our_spread_ask_price:

            self.logger.info(f'MAKING A MARKET AT BID {bid} VOLUME {bid_volume} ASK {ask} VOLUME {ask_volume}!') 
            
            # cancel the previous ask and bids we had.
            if self.our_spread_bid_id != 0:
                self.bids.add(self.our_spread_bid_id)
                self.send_cancel_order(self.our_spread_bid_id)
            if self.our_spread_ask_id != 0:
                self.asks.add(self.our_spread_ask_id)
                self.send_cancel_order(self.our_spread_ask_id)

            # record info about the new ask and bid.
            self.our_spread_bid_id = next(self.order_ids)
            self.our_spread_ask_id = next(self.order_ids)
            self.our_spread_bid_price = bid
            self.our_spread_ask_price = ask

            # send da order out.
            self.send_insert_order(self.our_spread_bid_id, Side.BID, bid, bid_volume, Lifespan.GOOD_FOR_DAY)
            self.send_insert_order(self.our_spread_ask_id, Side.ASK, ask, ask_volume, Lifespan.GOOD_FOR_DAY)
        elif bid > 0 \
            and self.position + LOT_SIZE < POSITION_LIMIT \
            and bid != self.our_spread_bid_price:

            self.logger.info(f'PLACING ORDER AT BID {bid} VOLUME {bid_volume}!') 
            
            # cancel bid because we are about to place a new bid.
            if self.our_spread_bid_id != 0:
                self.bids.add(self.our_spread_bid_id)
                self.send_cancel_order(self.our_spread_bid_id)
            
            # record new info about the thang.
            self.our_spread_bid_id = next(self.order_ids)
            self.our_spread_bid_price = bid

            # place dat order baby.
            self.send_insert_order(self.our_spread_bid_id, Side.BID, bid, bid_volume, Lifespan.GOOD_FOR_DAY)
        elif ask > 0 \
            and self.position - LOT_SIZE > -POSITION_LIMIT \
            and ask != self.our_spread_ask_price:

            self.logger.info(f'PLACING ORDER AT ASK {ask} VOLUME {ask_volume}!')

            # cancel ask order, about to place a new one.
            if self.our_spread_ask_id != 0:
                self.asks.add(self.our_spread_ask_id)
                self.send_cancel_order(self.our_spread_ask_id)
            
            # record new info about the thang.
            self.our_spread_ask_id = next(self.order_ids)
            self.our_spread_ask_price = ask

            # place dat order baby.
            self.send_insert_order(self.our_spread_ask_id, Side.ASK, ask, ask_volume, Lifespan.GOOD_FOR_DAY)
        else:
            self.logger.info(f'CANNOT PLACE PAIR OF ORDERS AT THIS MOMENT, RISK PARAMETERS DO NOT ALLOW FOR THIS.')

    def hedge(self) -> None:
        '''
        Function to hedge our position using FUTURES.
        This function is called whenever we are about to reach the 60 second limit, and hedges us properly.
        '''
        self.logger.critical(f'POSITION UPDATE:')
        self.logger.critical(f'\tPOSITION IS {self.position} HEDGE IS {self.hedged_position}.')
        self.logger.critical(f'\tENTERING HEDGE FUNCTION TO FIX DIS MESS!')
        
        amt_to_hedge = self.position + self.hedged_position
        next_id = next(self.order_ids)
        if self.position < 0:
            if amt_to_hedge > 0:
                self.logger.critical(f'TRYING TO SELL {amt_to_hedge} HEDGE!')
                # sell hedge.
                self.hedge_ask_id = next_id
                self.send_hedge_order(next_id, Side.ASK, MIN_BID_NEAREST_TICK, amt_to_hedge)
            else:
                self.logger.critical(f'TRYING TO BUY {amt_to_hedge} HEDGE!')
                # buy hedge.
                self.hedge_bid_id = next_id
                self.send_hedge_order(next_id, Side.BID, MAX_ASK_NEAREST_TICK, abs(amt_to_hedge))
        else:
            if amt_to_hedge > 0:
                self.logger.critical(f'TRYING TO BUY {amt_to_hedge} HEDGE!')
                #buy hedge.
                self.hedge_bid_id = next_id
                self.send_hedge_order(next_id, Side.BID, MAX_ASK_NEAREST_TICK, amt_to_hedge)
            else:
                self.logger.critical(f'TRYING TO SELL {amt_to_hedge} HEDGE!')
                # sell hedge.
                self.hedge_ask_id = next_id
                self.send_hedge_order(next_id, Side.ASK, MIN_BID_NEAREST_TICK, abs(amt_to_hedge))

        # reset the we are hedged flag.
        self.we_are_hedged = True


    #-----------------------------------HELPER FUNCTIONS WE USE-----------------------------------------------#


    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error.

        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        """
        self.logger.warning(f'ERROR WITH ORDER {client_order_id}')
        if client_order_id != 0:
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your hedge orders is filled.

        The price is the average price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info(f'FILLED A HEDGE {client_order_id} PRICE {price} VOLUME {volume}')
        if client_order_id == self.hedge_bid_id:
            self.hedged_position += volume
        elif client_order_id == self.hedge_ask_id:
            self.hedged_position -= volume
        else:
            self.logger.critical(f'I BELIEVER THIS CASE SHOULD NEVER HAPPEN')

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically to report the status of an order book.

        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """
        # check if we are hedged! duh.
        if self.we_are_hedged:
            if abs(self.position + self.hedged_position) > UNHEDGED_LOTS_LIMIT:
                # start da timer!
                self.time_of_last_imbalance = self.event_loop.time()
                self.we_are_hedged = False
            else:
                self.we_are_hedged = True # we are hedged and chillen.
        else:
            # check how long it has been, hedge if absolutely necessary.
            if self.event_loop.time() - self.time_of_last_imbalance > MAX_TIME_UNHEDGED:
                # need to hedge the difference.
                self.hedge()

        # trade!
        if bid_prices[0] == 0 or ask_prices[0] == 0 or self.p_prime_0 == 0 or self.p_prime_1 == 0:
            return # we got nothing in this thang. 
        if instrument == Instrument.ETF:
            # check sequence is in order.
            if sequence_number < self.last_order_book_sequence:
                return
            self.last_order_book_sequence = sequence_number

            # calculate p_t, based on the midpoint of the bid and ask we got just now.
            p_t = (ask_prices[0] + bid_prices[0]) / 2

            # calculate r_t based on our p_prime values collected in order ticks.
            r_t = abs((self.p_prime_0 - self.p_prime_1) / self.p_prime_0)

            # calculate volume imbalance to see whether we need to adjust spread.
            lambda_imbalance = (sum(bid_volumes) - sum(ask_volumes)) / sum(bid_volumes + ask_volumes)

            # check if we need to adjust spread based on lambda imbalance.
            if -LAMBDA_ONE < lambda_imbalance and lambda_imbalance < LAMBDA_ONE:
                # the regular case, no spread adjustment.
                new_bid = p_t - (r_t)*p_t
                new_ask = p_t + (r_t)*p_t
            elif lambda_imbalance < -LAMBDA_ONE:
                # sell order imbalance.
                new_bid = p_t - (r_t + 0.0002)*p_t
                new_ask = p_t + (r_t + 0.0001)*p_t
            elif lambda_imbalance > LAMBDA_ONE:
                # buy order imbalance.
                new_bid = p_t - (r_t + 0.0001)*p_t
                new_ask = p_t + (r_t + 0.0002)*p_t
            else:
                self.logger.critical(f'BRANCH SHOULD NEVER BE EXECUTED!')

            # round new bid and new ask outward to the nearest TICK_SIZE.
            new_bid = int(new_bid - new_bid % TICK_SIZE_IN_CENTS) # more conservative to round bid down.
            new_ask = int(new_ask + TICK_SIZE_IN_CENTS - new_ask % TICK_SIZE_IN_CENTS) # more conservative to round ask up.

            # make the new market!
            self.make_a_market(new_bid, LOT_SIZE, new_ask, LOT_SIZE)

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info(f'ORDER {client_order_id} FILLED AT PRICE {price} VOLUME {volume}')
        if client_order_id == self.our_spread_bid_id:
            self.position += volume
            
        elif client_order_id == self.our_spread_ask_id:
            self.position -= volume
        else:
            self.logger.critical('ORDER WAS FILLED BEFORE CANCELLING IT WORKED...')
            if client_order_id in self.bids:
                self.position += volume
                self.bids.discard(client_order_id)
            elif client_order_id in self.asks:
                self.position -= volume
                self.asks.discard(client_order_id)
            else:
                self.logger.critical(f'\n\n\nTHIS SHOULDNT HAPPEN BRUH\n\n\n')
                

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        """Called when the status of one of your orders changes.

        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.

        If an order is cancelled its remaining volume will be zero.
        """
        self.logger.info("received order status for order %d with fill volume %d remaining %d and fees %d",
                         client_order_id, fill_volume, remaining_volume, fees)
        # if client_order_id == 0:
        #     return
        if remaining_volume == 0:
            # order was cancelled or order was filled. order filled function takes care of the latter case.
            # this part takes care of the former, case: cancelled order.
            if client_order_id == self.our_spread_bid_id:
                self.our_spread_bid_id = self.our_spread_bid_price = 0
            elif client_order_id == self.our_spread_ask_id:
                self.our_spread_ask_id = self.our_spread_ask_price = 0
            else:
                pass # filled function above takes care of other cases.

        elif fill_volume == 0:
            # order was just created!
            side = "BID" if client_order_id == self.our_spread_bid_id else "ASK"
            self.logger.critical(f'CREATED ORDER {client_order_id} WITH VOLUME {remaining_volume} ON SIDE {side}')
            self.logger.critical(f'POSITION {self.position} HEDGE {self.hedged_position}')

        else:
            # partially filled, fill volume and remaining volume both above 0.
            # cancel the rest of this bad boy, place a new order of LOT SIZE at this price.
            
            if client_order_id == self.our_spread_bid_id:
                self.bids.add(client_order_id)
                self.send_cancel_order(client_order_id)
                self.position += fill_volume
                self.our_spread_bid_id = 0
                self.make_a_market(self.our_spread_bid_price, LOT_SIZE, 0, 0) # bid order.
            elif client_order_id == self.our_spread_ask_id:
                self.asks.add(client_order_id)
                self.send_cancel_order(client_order_id)
                self.position -= fill_volume
                self.our_spread_ask_id = 0
                self.make_a_market(0, 0, self.our_spread_ask_price, LOT_SIZE) # ask order.
            

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

        # Here, we just calculate the P' value for our formula and store is as a global variable.

        if ask_prices[0] == 0 and bid_prices[0] == 0:
            return # means nothing was traded.
        
        # check sequence is in order.
        if sequence_number < self.last_ticks_sequence:
            return
        self.last_ticks_sequence = sequence_number

        # compute weighted average.
        numer = denom = total = 0
        for vol, price in zip(bid_volumes+ask_volumes, bid_prices+ask_prices):
            numer += vol*price
            denom += vol
        
        # sliding window to keep last two weighted averages.
        self.p_prime_0 = self.p_prime_1
        self.p_prime_1 = numer / denom
