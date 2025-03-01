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
from re import L
from textwrap import fill
from tkinter.tix import MAX
from turtle import pos, position
import numpy as np

import time as TIME_MODULE

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side

LOT_SIZE = 25
POSITION_LIMIT = 75 # prevents one-off error that makes us go over 100... i think...
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS

BPS_ROUND_FLAT = 0.0000
BPS_ROUND_DOWN = 0.0001
BPS_ROUND_UP = 0.001

UNHEDGED_LOTS_LIMIT = 10    # volume limit in lots.
MAX_TIME_UNHEDGED = 58      # time limit in seconds.
LAMBDA_ONE = 0.5            # our first constant, by which we decide whether order imbalance is up or down or flat.
ATV_WIN_SIZE = 10

POSITION_LIMIT_TO_UNWIND = 49
HEDGE_POSITION_LIMIT_TO_UNWIND = 5          # hedged position limit at which we wanna start unwinding. we don't like hedges, we can unwind quick.
HOW_OFTEN_TO_CHECK_HEDGE = 3                # how many snapshots do we wait before checking if we are in a hedged state?

# FILL_RATE_WINDOW_SIZE = 50
# DESIRED_FILL_RATE = 0.85

#------------------VARIABLES NOT BEING USED ANYMORE------------------#

# BETA = 0.25                 # our second constant, by which we decide whether we like a potential FAK order
# LAMBDA_TWO = 1.5

#------------------VARIABLES NOT BEING USED ANYMORE------------------#

class AutoTrader(BaseAutoTrader):
    '''
    LiquidBears AutoTrader.
    '''

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""

        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)

        self.adj_bid_up = self.adj_ask_up = 0

        self.bid_fill_amounts = list()
        self.ask_fill_amounts = list()

        self.unwinding = False

        self.money_in = self.hedged_money_in = 0                                # helps us keep track of our average entry.

        self.best_futures_bid = self.best_futures_ask = 0                       # keeping track of best ask and offer for futures for computing cost.

        self.real_bid = [0, 0, 0]                                               # (id, price, volume) for bid thats out there
        self.real_ask = [0, 0, 0]                                               # (id, price, volume) for ask thats out there

        self.fak_orders = dict()                                                # id -> (side, price, volume)
        self.theo_orders = dict()                                               # id -> (side, price, volume)
        self.traded_volumes = list()                                            # n-most recent traded volumes from on_ticks_update_message for ATV in volume indicator
        self.latest_volume_signal = 0                                           # volume signal baby.

        self.hedge_bid_id = self.hedge_ask_id = 0                               # state of the hedge order we placed so we can adjust 
                                                                                # hedged position in the correct direction.
        
        self.last_fak_id = self.last_fak_price = 0                              # state of the last fill and kill we sent.
        
        self.position = self.hedged_position = 0                                # state of each position's size.
        self.p_prime_0 = self.p_prime_1 = 0                                     # weighted averages of last tick update.
        self.last_ticks_sequence_etf = self.last_order_book_sequence_etf = -1   # last message we processed (one for ticks one for order book). ETF
        self.last_ticks_sequence_fut = self.last_order_book_sequence_fut = -1   # last message we processed (one for ticks one for order book). FUT

        self.we_are_hedged = True                                               # flag to set for when we are set vs not.
        self.time_of_last_imbalance = self.event_loop.time()                    # used to hedge as a last resort before the minute runs out.

    #-----------------------------------HELPER FUNCTIONS WE USE-----------------------------------------------#

    # def get_avg_fill_percentage(self) -> dict:
    #     '''
    #     Returns average fill proportion of our orders for the bid and ask side, as a dict.
    #     '''
    #     bid_res = 0 if len(self.bid_fill_amounts) == 0 else ((sum(self.bid_fill_amounts[-FILL_RATE_WINDOW_SIZE:]) / len(self.bid_fill_amounts[-FILL_RATE_WINDOW_SIZE:])) / LOT_SIZE)
    #     ask_res = 0 if len(self.ask_fill_amounts) == 0 else ((sum(self.ask_fill_amounts[-FILL_RATE_WINDOW_SIZE:]) / len(self.ask_fill_amounts[-FILL_RATE_WINDOW_SIZE:])) / LOT_SIZE)
    #     return {
    #         'bid' : bid_res,
    #         'ask' : ask_res
    #     }
    
    def compute_volume_signal(self, ask_vol: int, bid_vol: int) -> float:
        '''
        Compute volume pressure magnitude and side based on newest ticks update message.
        If positive, asks are getting knocked out and price should be rising.
        If negative, bids are getting cleared and price should be falling. We could reverse this.
        Returns: the indicator as a float.
        '''
        return (bid_vol - ask_vol) / (sum(self.traded_volumes[-ATV_WIN_SIZE:]) / len(self.traded_volumes[-ATV_WIN_SIZE:]))

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
        # self.logger.critical(f'MAKING A MARKET, POSTIION {self.position} HEDGE {self.hedged_position}')

        # try to place em both at once if we can. otherwise place one of them.
        if bid > 0 and ask > 0 \
            and self.position + LOT_SIZE < POSITION_LIMIT \
            and self.position - LOT_SIZE > -POSITION_LIMIT \
            and bid != self.real_bid[1] \
            and ask != self.real_ask[1]:

            # cancel the previous ask and bids we had. ID OF 0 MEANS WE DONT HAVE AN ORDER PLACED
            if self.real_bid[0] != 0:
                self.send_cancel_order(self.real_bid[0])
            if self.real_ask[0] != 0:
                self.send_cancel_order(self.real_ask[0])

            # record info about the new ask and bid.
            new_bid_id = next(self.order_ids)
            new_ask_id = next(self.order_ids)
            # we need to segment orders we are about to place because we dont know what will happen
            self.theo_orders[new_bid_id] = (Side.BID, bid, bid_volume)
            self.theo_orders[new_ask_id] = (Side.ASK, ask, ask_volume)

            # send da order out.
            self.send_insert_order(new_bid_id, Side.BID, bid, bid_volume, Lifespan.GOOD_FOR_DAY)
            self.send_insert_order(new_ask_id, Side.ASK, ask, ask_volume, Lifespan.GOOD_FOR_DAY)
        elif bid > 0 \
            and self.position + LOT_SIZE < POSITION_LIMIT \
            and bid != self.real_bid[1]:
            
            # cancel bid because we are about to place a new bid.
            if self.real_bid[0] != 0:
                self.send_cancel_order(self.real_bid[0])
            
            # record new info about the thang.
            new_bid_id = next(self.order_ids)
            self.theo_orders[new_bid_id] = (Side.BID, bid, bid_volume)

            # place dat order baby.
            self.send_insert_order(new_bid_id, Side.BID, bid, bid_volume, Lifespan.GOOD_FOR_DAY)
        elif ask > 0 \
            and self.position - LOT_SIZE > -POSITION_LIMIT \
            and ask != self.real_ask[1]:

            # cancel ask order, about to place a new one.
            if self.real_ask[0] != 0:
                self.send_cancel_order(self.real_ask[0])
            
            # record new info about the thang.
            new_ask_id = next(self.order_ids)
            self.theo_orders[new_ask_id] = (Side.ASK, ask, ask_volume)

            # place dat order baby.
            self.send_insert_order(new_ask_id, Side.ASK, ask, ask_volume, Lifespan.GOOD_FOR_DAY)
        else:
            pass

    def hedge(self) -> None:
        '''
        Function to hedge our position using FUTURES.
        This function is called whenever we are about to reach the 60 second limit, and hedges us properly.
        '''
        self.logger.critical(f'ENTER HEDGE:')
        self.logger.critical(f'\tPOSITION IS {self.position} HEDGE IS {self.hedged_position}.')

        next_id = next(self.order_ids)
        if self.position < 0:
            if -self.position < self.hedged_position: # sell.
                if self.hedge_ask_id == 0:
                    self.hedge_ask_id = next_id
                    self.send_hedge_order(next_id, Side.ASK, MIN_BID_NEAREST_TICK, abs(self.position + self.hedged_position))
            else: # buy.
                if self.hedge_bid_id == 0:
                    self.hedge_bid_id = next_id
                    self.send_hedge_order(next_id, Side.BID, MAX_ASK_NEAREST_TICK, abs(self.position + self.hedged_position))
        elif self.position == 0:
            if self.hedged_position < 0: # buy.
                if self.hedge_bid_id == 0:
                    self.hedge_bid_id = next_id
                    self.send_hedge_order(next_id, Side.BID, MAX_ASK_NEAREST_TICK, abs(self.hedged_position))
            else: # sell.
                if self.hedge_ask_id == 0:
                    self.hedge_ask_id = next_id
                    self.send_hedge_order(next_id, Side.ASK, MIN_BID_NEAREST_TICK, self.hedged_position)
        elif self.position > 0:
            if self.position > -self.hedged_position: # sell.
                if self.hedge_ask_id == 0:
                    self.hedge_ask_id = next_id
                    self.send_hedge_order(next_id, Side.ASK, MIN_BID_NEAREST_TICK, abs(self.position + self.hedged_position))
            else: # buy.
                if self.hedge_bid_id == 0:
                    self.hedge_bid_id = next_id
                    self.send_hedge_order(next_id, Side.BID, MAX_ASK_NEAREST_TICK, abs(self.position + self.hedged_position))
        else:
            self.logger.critical(f'CASE SHOULD NEVER HAPPEN!!!')

        self.logger.critical(f'\tEXIT HEDGE.')

    def realize_hedge_PnL(self) -> None:
        '''
        Unwinds our hedge when it is profitable to do so.
        '''
        avg_entry = self.hedged_money_in / self.hedged_position if self.hedged_position != 0 else 0
        if self.hedged_position > POSITION_LIMIT_TO_UNWIND:
            if avg_entry < self.best_futures_bid - (self.best_futures_ask / 2):
                if self.hedge_ask_id == 0:
                    self.logger.info(f'HEDGE LUCRATIVE UNWIND SPOT, SELLLING {self.hedged_position}, COLLECTING {(self.best_futures_bid - avg_entry) * self.hedged_position} DOLLARS BABY')
                    self.hedge_ask_id = next(self.order_ids)
                    self.send_hedge_order(self.hedge_ask_id, Side.ASK, MIN_BID_NEAREST_TICK, self.hedged_position)
        elif self.hedged_position < -POSITION_LIMIT_TO_UNWIND:
            if avg_entry > self.best_futures_ask - (self.best_futures_bid / 2):
                if self.hedge_bid_id == 0:
                    self.logger.info(f'HEDGE LUCRATIVE UNWIND SPOT, SELLLING {abs(self.hedged_position)}, COLLECTING {(avg_entry - self.best_futures_ask) * abs(self.hedged_position)} DOLLARS BABY')
                    self.hedge_ask_id = next(self.order_ids)
                    self.send_hedge_order(self.hedge_ask_id, Side.BID, MAX_ASK_NEAREST_TICK, abs(self.hedged_position))
        
    def realize_PnL(self, bid, ask) -> None:
        '''
        This function realizes out PnL if we have any past a certain threshold.
        That is, if we have +50 etf and the stock moved up, we should realize our PnL
        by selling off some of the etf at the new higher price via a fill and kill.
        '''
        if ask - bid == TICK_SIZE_IN_CENTS:
            return
        avg_entry = self.money_in / self.position if self.position != 0 else 0
        if self.position > POSITION_LIMIT_TO_UNWIND:
            if avg_entry < 2*bid - ask:
                next_id = next(self.order_ids)
                # realize da PnL
                self.fak_orders[next_id] = [Side.ASK, bid, 1]
                self.send_insert_order(next_id, Side.ASK, bid, 1, Lifespan.FILL_AND_KILL)

        elif 0 < self.position and self.position <= POSITION_LIMIT_TO_UNWIND:
            cushion = (ask-bid) * (POSITION_LIMIT_TO_UNWIND / self.position)
            if avg_entry < bid - cushion:
                next_id = next(self.order_ids)
                # realize da PnL
                self.fak_orders[next_id] = [Side.ASK, bid, 1]
                self.send_insert_order(next_id, Side.ASK, bid, 1, Lifespan.FILL_AND_KILL)

        elif -POSITION_LIMIT_TO_UNWIND <= self.position and self.position < 0:
            cushion = (ask-bid) * (POSITION_LIMIT_TO_UNWIND / self.position)
            if avg_entry > ask + cushion:
                next_id = next(self.order_ids)
                # realize da PnL
                self.fak_orders[next_id] = [Side.BID, ask, 1]
                self.send_insert_order(next_id, Side.BID, ask, 1, Lifespan.FILL_AND_KILL)

        elif self.position < -POSITION_LIMIT_TO_UNWIND:
            if avg_entry > 2*ask - bid:
                next_id = next(self.order_ids)
                # realize da PnL
                self.fak_orders[next_id] = [Side.BID, ask, 1]
                self.send_insert_order(next_id, Side.BID, ask, 1, Lifespan.FILL_AND_KILL)

    #-----------------------------------HELPER FUNCTIONS WE USE-----------------------------------------------#

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error.

        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        """

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your hedge orders is filled.

        The price is the average price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info(f'FILLED A HEDGE {client_order_id} PRICE {price} VOLUME {volume}')
        self.we_are_hedged = True # for the moment of this function we will call ourselves hedged and not send out any more orders.
        if client_order_id == self.hedge_bid_id:
            self.hedged_position += volume
            self.hedge_bid_id = 0
            self.hedged_money_in += price*volume
        elif client_order_id == self.hedge_ask_id:
            self.hedged_position -= volume
            self.hedge_ask_id = 0
            self.hedged_money_in -= price*volume
        else:
            self.logger.critical(f'I BELIEVER THIS CASE SHOULD NEVER HAPPEN')

        self.we_are_hedged = ( abs(self.position + self.hedged_position) < UNHEDGED_LOTS_LIMIT )
        if self.hedged_position == 0:
            self.hedged_money_in == 0

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically to report the status of an order book.

        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """        
                
        # trade!
        if bid_prices[0] == 0 or ask_prices[0] == 0 or self.p_prime_0 == 0 or self.p_prime_1 == 0:
            # we got nothing in this thang. 
            return

        if instrument == Instrument.FUTURE:
            if sequence_number < self.last_order_book_sequence_fut:
                # check sequence is in order.
                return
            self.last_order_book_sequence_fut = sequence_number
            self.best_futures_bid, self.best_futures_ask = bid_prices[0], ask_prices[0]

            if sequence_number % HOW_OFTEN_TO_CHECK_HEDGE == 0:
                # check if we are hedged! duh.
                if self.we_are_hedged:
                    if abs(self.position + self.hedged_position) > UNHEDGED_LOTS_LIMIT:
                        # start da timer!
                        self.time_of_last_imbalance = self.event_loop.time()
                        self.we_are_hedged = False
                else:
                    if self.event_loop.time() - self.time_of_last_imbalance > MAX_TIME_UNHEDGED:
                        self.hedge() # hedge only if absolutely necessary!
                    else:
                        # new strat to get rid of hedge: get rid of it when we are profiting from that.
                        self.realize_hedge_PnL()
        elif instrument == Instrument.ETF:
            if sequence_number < self.last_order_book_sequence_etf:
                # check sequence is in order.
                return
            self.last_order_book_sequence_etf = sequence_number

            # next, calculate current entry, and see if we have a PnL to collect.
            self.realize_PnL(bid_prices[0], ask_prices[0])
            
            # calculate p_t, based on the midpoint of the bid and ask we got just now.
            p_t = (ask_prices[0] + bid_prices[0]) / 2

            # calculate r_t based on our p_prime values collected in order ticks.
            r_t = abs((self.p_prime_0 - self.p_prime_1) / self.p_prime_0) + BPS_ROUND_FLAT

            # calculate volume imbalance to see whether we need to adjust spread.
            lambda_imbalance = (sum(bid_volumes) - sum(ask_volumes)) / sum(bid_volumes + ask_volumes)

            # check if we need to adjust spread based on lambda imbalance.
            if -LAMBDA_ONE < lambda_imbalance and lambda_imbalance < LAMBDA_ONE:
                # the regular case, no spread adjustment.
                new_bid = p_t - (r_t)*p_t
                new_ask = p_t + (r_t)*p_t
            elif lambda_imbalance < -LAMBDA_ONE:
                # sell order imbalance.
                new_bid = p_t - (r_t + self.adj_bid_up + BPS_ROUND_UP)*p_t
                new_ask = p_t + (r_t + BPS_ROUND_DOWN)*p_t
            elif lambda_imbalance > LAMBDA_ONE:
                # buy order imbalance.
                new_bid = p_t - (r_t + BPS_ROUND_DOWN)*p_t
                new_ask = p_t + (r_t + self.adj_ask_up + BPS_ROUND_UP)*p_t
            else:
                self.logger.critical(f'BRANCH SHOULD NEVER BE EXECUTED!')

            # round new bid and new ask outward to the nearest TICK_SIZE, check if interval too tight.
            new_bid = min(int(new_bid - new_bid % TICK_SIZE_IN_CENTS), bid_prices[0])
            new_ask = max(int(new_ask + TICK_SIZE_IN_CENTS - new_ask % TICK_SIZE_IN_CENTS), ask_prices[0])

            # make the new market!
            self.make_a_market(new_bid, LOT_SIZE, new_ask, LOT_SIZE)

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        if client_order_id == self.real_bid[0]:
            self.position += volume
            self.real_bid[2] -= volume
            self.money_in += price*volume
            if self.real_bid[2] <= 0:
                # self.bid_fill_amounts.append(1)
                # this is a full fill
                self.real_bid[0] = self.real_bid[1] = self.real_bid[2] = 0
        
        elif client_order_id == self.real_ask[0]:
            self.position -= volume
            self.real_ask[2] -= volume
            self.money_in -= price*volume
            if self.real_ask[2] <= 0:
                # self.ask_fill_amounts.append(1)
                self.real_ask[0] = self.real_ask[1] = self.real_ask[2] = 0
        
        elif client_order_id in self.theo_orders.keys():
            if self.theo_orders[client_order_id][0] == Side.BID:
                self.position += volume
                self.money_in += price*volume
                vol_rem = self.theo_orders[client_order_id][2] - volume
                if vol_rem > 0:
                    # order partially filled, it exists
                    self.real_bid = [client_order_id, self.theo_orders[client_order_id][1], vol_rem]
                else:
                    # order fully filled, not exists anymore
                    # self.bid_fill_amounts.append(1)
                    self.real_bid[0] = self.real_bid[1] = self.real_bid[2] = 0
            
            elif self.theo_orders[client_order_id][0] == Side.ASK:
                self.position -= volume
                self.money_in -= price*volume
                vol_rem = self.theo_orders[client_order_id][2] - volume
                if vol_rem > 0:
                    # order partially filled, it exists
                    self.real_ask = [client_order_id, self.theo_orders[client_order_id][1], vol_rem]
                else:
                    # order fully filled, not exists anymore
                    # self.ask_fill_amounts.append(1)
                    self.real_ask[0] = self.real_ask[1] = self.real_ask[2] = 0

            else:
                self.logger.critical('SIDE NOT EQUAL TO BID OR ASK IN DICT PART 1???')
            
            # remove virtual order because it was instantiated
            del self.theo_orders[client_order_id]
        
        elif client_order_id in self.fak_orders.keys():
            if self.fak_orders[client_order_id][0] == Side.BID:
                self.position += volume
                self.money_in += price*volume
                self.fak_orders[client_order_id][2] -= volume
            elif self.fak_orders[client_order_id][0] == Side.ASK:
                self.position -= volume
                self.money_in -= price*volume
                self.fak_orders[client_order_id][2] -= volume
            else:
                self.logger.critical(f'THIS CASE SHOULD NEVER HAPPEN')
        else:
            self.logger.critical(f'\n\n\nTHIS SHOULDNT HAPPEN BRUH FUCK THE POSITION AINT FUCKING UPDATING ORDER {client_order_id} IS THE CULPRIT FILL {volume}\n\n\n')

        if self.position == 0:
            self.money_in == 0
                
    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        """Called when the status of one of your orders changes.

        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.

        If an order is cancelled its remaining volume will be zero.
        """
        # created, cancelled, partially filled, fully filled are the options

        if fill_volume == 0 and remaining_volume > 0:
            # order was just created. we need to instantiate it if not already.
            if client_order_id in self.theo_orders.keys():
                if self.theo_orders[client_order_id][0] == Side.BID:
                    self.real_bid = [client_order_id, self.theo_orders[client_order_id][1], self.theo_orders[client_order_id][2]]
                    # del self.theo_orders[client_order_id]
            
                elif self.theo_orders[client_order_id][0] == Side.ASK:
                    self.real_ask = [client_order_id, self.theo_orders[client_order_id][1], self.theo_orders[client_order_id][2]]
                    # del self.theo_orders[client_order_id]
            
                else:
                    self.logger.critical('SIDE NOT EQUAL TO ASK OR BID IN DICT PART 2???')
            elif client_order_id in self.fak_orders.keys():
                del self.fak_orders[client_order_id]
            else:
                pass # self.logger.critical(f'Order {client_order_id} was instantiated by make_a_market.')

        elif remaining_volume == 0:
            # order was cancelled or order was filled. order filled function takes care of the latter case.
            # this part takes care of the former, case: cancelled order.
            if client_order_id == self.real_bid[0]:
                # self.bid_fill_amounts.append(self.real_bid[2])
                self.real_bid[0] = self.real_bid[1] = self.real_bid[2] = 0
            elif client_order_id == self.real_ask[0]:
                # self.ask_fill_amounts.append(self.real_ask[2])
                self.real_ask[0] = self.real_ask[1] = self.real_ask[2] = 0
            elif client_order_id in self.fak_orders and self.fak_orders[client_order_id][2] <= 0:
                pass # del self.fak_orders[client_order_id]
            else:
                pass # self.logger.critical('Remaining volume is 0 but order not exists. This means on_order_filled_message took care of it.')

        else:
            # partially filled, fill volume and remaining volume both above 0.
            pass # self.logger.critical('Fill volume and remaining volume above 0. This means on_order_filled_message took care of it.')

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically when there is trading activity on the market.

        The five best ask (i.e. sell) and bid (i.e. buy) prices at which there
        has been trading activity are reported along with the aggregated volume
        traded at each of those price levels.

        If there are less than five prices on a side, then zeros will appear at
        the end of both the prices and volumes arrays.
        """
        # Here, we just calculate the P' value for our formula and store is as a global variable.

        if ask_prices[0] == 0 and bid_prices[0] == 0:
            return # means nothing was traded.
        
        # INSTRUMENT MUST BE ETF!!!
        if instrument == Instrument.ETF:
            # check sequence is in order.
            if sequence_number < self.last_ticks_sequence_etf:
                return
            self.last_ticks_sequence_etf = sequence_number

            sum_ask, sum_bid = sum(ask_volumes), sum(bid_volumes)

            # add traded volume to container list for average traded volume computation.
            # if len(self.traded_volumes) == ATV_WIN_SIZE:
            #     self.traded_volumes.pop(0)
            self.traded_volumes.append(sum_ask + sum_bid)

            # compute signal
            self.latest_volume_signal = self.compute_volume_signal(ask_vol=sum_ask, bid_vol=sum_bid)

            # compute weighted average.
            numer = 0
            for vol, price in zip(bid_volumes+ask_volumes, bid_prices+ask_prices):
                numer += vol*price
            
            # sliding window to keep last two weighted averages.
            self.p_prime_0 = self.p_prime_1
            self.p_prime_1 = numer / (sum_ask+sum_bid)