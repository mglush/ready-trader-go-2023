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
from http import client
import itertools
from re import L
from textwrap import fill
from tkinter.tix import MAX
from turtle import pos, position

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side

LOT_SIZE = 25
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS

BPS_ROUND_FLAT = 0.0000
BPS_ROUND_IN_DIRECTION = -0.0032
BPS_ROUND_AGAINST_DIRECTION = 0.0064

UNHEDGED_LOTS_LIMIT = 10 # volume limit in lots.
MAX_TIME_UNHEDGED = 58  # time limit in seconds for us to re-hedge. 2 second buffer on actual limit.
HEDGE_POSITION_LIMIT_TO_UNWIND = 0  # unwind the moment it is profitable to do so.
POSITION_TOO_FAR = 25

LAMBDA_ONE = 0.5      # our first constant, by which we decide whether order imbalance is up or down or flat.]

class AutoTrader(BaseAutoTrader):
    '''
    LiquidBears AutoTrader.
    '''

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""

        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)

        self.curr_bid = None                                                     # maps curr bid to its [id, price, volume]
        self.curr_ask = None                                                     # maps curr ask to its [id, price, volume]

        self.change = True

        self.bps_round_flat = BPS_ROUND_FLAT
        self.bps_round_down_bid = self.bps_round_down_ask = BPS_ROUND_IN_DIRECTION
        self.bps_round_up_bid = self.bps_round_up_ask = BPS_ROUND_AGAINST_DIRECTION

        self.hedged_money_in = 0                                                # used for calculating average entry into position.

        # self.best_futures_bid = self.best_futures_ask = 0                        # keeping track of best ask and offer for futures for computing cost

        self.hedge_bid_id = self.hedge_ask_id = 0                                # state of the hedge order we placed so we can adjust 
                                                                                 # hedged position in the correct direction.
        self.position = self.hedged_position = 0                                 # state of each position's size.
        self.p_prime_0 = self.p_prime_1 = self.p_prime_2 = 0                     # weighted averages of last tick update.
        self.vol_prime_0 = self.vol_prime_1 = 0                                  # so we can use a weighed average of the p_primes.
        self.r_t = inf
        self.last_ticks_sequence_etf = self.last_order_book_sequence_etf = -1    # last message we processed (one for ticks one for order book). ETF
        self.last_ticks_sequence_fut = self.last_order_book_sequence_fut = -1    # last message we processed (one for ticks one for order book). FUT

        self.we_are_hedged = True                                                # flag to set for when we are set vs not.
        self.time_of_last_imbalance = self.event_loop.time()                     # used to hedge as a last resort before the minute runs out.

    #-----------------------------------HELPER FUNCTIONS WE USE-----------------------------------------------#

    def make_a_market(self, bid, bid_volume, ask, ask_volume) -> None:
        '''
        If the new bid doesnt match the old bid, we cancel the old order and
        place a new one. same thing for the ask side.

        Parameters:
        bid (int):          price to place bid at.
        bid_volume (int):   how many shares to bid?
        ask (int):          price to place ask at.
        ask_volume (int):   how many shares to ask?
        '''
        if self.curr_bid is not None and self.curr_bid['price'] != bid:
            self.send_cancel_order(self.curr_bid['id'])

        if self.curr_ask is not None and self.curr_ask['price'] != ask:
            self.send_cancel_order(self.curr_ask['id'])

        if self.curr_bid is None \
            and bid != 0 \
            and self.position + bid_volume < POSITION_LIMIT:
            self.curr_bid = {
                'id' : next(self.order_ids),
                'price' : bid,
                'volume' : bid_volume
            }
            self.send_insert_order(self.curr_bid['id'], Side.BID, bid, bid_volume, Lifespan.GOOD_FOR_DAY)
        
        if self.curr_ask is None \
            and ask != 0 \
            and self.position - ask_volume > -POSITION_LIMIT:
            self.curr_ask = {
                'id' : next(self.order_ids),
                'price' : ask,
                'volume' : ask_volume
            }
            self.send_insert_order(self.curr_ask['id'], Side.ASK, ask, ask_volume, Lifespan.GOOD_FOR_DAY)

    def hedge(self) -> None:
        '''
        Function to hedge our position using FUTURES.
        This function is called whenever we are about to reach the 60 second limit, and hedges us properly.
        '''
        self.logger.critical(f'ENTER HEDGE:')
        self.logger.critical(f'\tPOSITION IS {self.position} HEDGE IS {self.hedged_position}.')

        next_id = next(self.order_ids)
        if self.position < 0:
            if -self.position < self.hedged_position and self.hedge_ask_id == 0: # sell.
                self.hedge_ask_id = next_id
                self.send_hedge_order(next_id, Side.ASK, MIN_BID_NEAREST_TICK, abs(self.position + self.hedged_position))
            else: # buy.
                if self.hedge_bid_id == 0:
                    self.hedge_bid_id = next_id
                    self.send_hedge_order(next_id, Side.BID, MAX_ASK_NEAREST_TICK, abs(self.position + self.hedged_position))
        elif self.position == 0:
            if self.hedged_position < 0 and self.hedge_bid_id == 0: # buy.
                self.hedge_bid_id = next_id
                self.send_hedge_order(next_id, Side.BID, MAX_ASK_NEAREST_TICK, abs(self.hedged_position))
            else: # sell.
                if self.hedge_ask_id == 0:
                    self.hedge_ask_id = next_id
                    self.send_hedge_order(next_id, Side.ASK, MIN_BID_NEAREST_TICK, self.hedged_position)
        elif self.position > 0:
            if self.position > -self.hedged_position and self.hedge_ask_id == 0: # sell.
                self.hedge_ask_id = next_id
                self.send_hedge_order(next_id, Side.ASK, MIN_BID_NEAREST_TICK, abs(self.position + self.hedged_position))
            else: # buy.
                if self.hedge_bid_id == 0:
                    self.hedge_bid_id = next_id
                    self.send_hedge_order(next_id, Side.BID, MAX_ASK_NEAREST_TICK, abs(self.position + self.hedged_position))
        else:
            self.logger.error(f'CASE SHOULD NEVER HAPPEN!!!')
        
        self.logger.critical(f'\tEXIT HEDGE.')

    # def realize_hedge_PnL(self) -> None:
    #     '''
    #     Calculated average entry into the hedge, and
    #     unwinds our hedge when it is profitable to do so.
    #     '''
    #     if self.hedged_position == 0:
    #         return

    #     avg_entry = self.hedged_money_in / self.hedged_position
    #     self.logger.critical(f'OUR AVERAGE ENTRY IS {avg_entry}, POSITION IS {self.hedged_position}, CURR FUTURE BID IS {self.best_futures_bid}, CURR FUTURE ASK IS {self.best_futures_ask}.')
    #     if self.hedged_position > HEDGE_POSITION_LIMIT_TO_UNWIND:
    #         if avg_entry < 2*self.best_futures_bid - self.best_futures_ask:
    #             if self.hedge_ask_id == 0:
    #                 self.logger.critical('UNWINDING HEDGE POSITION')
    #                 self.hedge_ask_id = next(self.order_ids)
    #                 self.send_hedge_order(self.hedge_ask_id, Side.ASK, MIN_BID_NEAREST_TICK, self.hedged_position)
    #     elif self.hedged_position < -HEDGE_POSITION_LIMIT_TO_UNWIND:
    #         if avg_entry > 2*self.best_futures_ask - self.best_futures_bid:
    #             if self.hedge_bid_id == 0:
    #                 self.logger.critical('UNWINDING HEDGE POSITION')
    #                 self.hedge_ask_id = next(self.order_ids)
    #                 self.send_hedge_order(self.hedge_bid_id, Side.BID, MAX_ASK_NEAREST_TICK, abs(self.hedged_position))

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
        if client_order_id == self.hedge_bid_id:
            self.hedged_position += volume
            self.hedged_money_in += price * volume
            self.hedge_bid_id = 0
        elif client_order_id == self.hedge_ask_id:
            self.hedged_position -= volume
            self.hedged_money_in -= price * volume
            self.hedge_ask_id = 0
        else:
            self.logger.error(f'HEDGE FILLED BUT WE DONT KNOW IF IT WAS A BID OR AN ASK!!!')
        
        if self.hedged_position == 0:
            self.hedged_money_in = 0

        self.we_are_hedged = ( abs(self.position + self.hedged_position) <  UNHEDGED_LOTS_LIMIT)

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically to report the status of an order book.

        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """
        # trade!
        if bid_prices[0] <= 0 or ask_prices[0] <= 0:
            return # we got nothing in this thang.

        if instrument == Instrument.FUTURE:
            if sequence_number < self.last_order_book_sequence_fut:
                return # check sequence is in order.
            self.last_order_book_sequence_fut = sequence_number
            # self.best_futures_bid, self.best_futures_ask = bid_prices[0], ask_prices[0]

            # if sequence_number % HOW_OFTEN_TO_CHECK_HEDGE == 0:
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
                    if self.hedged_position < 0 and self.hedge_bid_id != 0:
                        self.hedge_bid_id = next(self.order_ids)
                        self.send_hedge_order(self.hedge_bid_id, Side.BID, MAX_ASK_NEAREST_TICK, abs(int(self.hedged_position)))
                    elif self.hedged_position > 0 and self.hedge_ask_id != 0:
                        self.hedge_ask_id = next(self.order_ids)
                        self.send_hedge_order(self.hedge_ask_id, Side.ASK, MIN_BID_NEAREST_TICK, int(self.hedged_position))

        elif instrument == Instrument.ETF:
            if sequence_number < self.last_order_book_sequence_etf or self.p_prime_0 == 0 or self.p_prime_1 == 0:
                return # check sequence is in order.
            self.last_order_book_sequence_etf = sequence_number
            
            if not self.change:
                if self.curr_ask is None and self.curr_ask is None:
                    self.change = True
                else:
                    return

            # -------------------------------- THE MAIN ALGORITHM -------------------------------- #

            # calculate p_t, based on the midpoint of the bid and ask we got just now.
            p_t = (ask_prices[0] + bid_prices[0]) / 2

            # calculate volume imbalance to see whether we need to adjust spread.
            lambda_imbalance = (sum(bid_volumes) - sum(ask_volumes)) / sum(bid_volumes + ask_volumes)

            # check if we need to adjust spread based on lambda imbalance.
            if -LAMBDA_ONE < lambda_imbalance and lambda_imbalance < LAMBDA_ONE:
                # the regular case, no spread adjustment.
                new_bid = p_t - (self.r_t)*p_t
                new_ask = p_t + (self.r_t)*p_t
            elif lambda_imbalance <= -LAMBDA_ONE:
                # sell order imbalance.
                new_bid = p_t - (self.r_t + self.bps_round_up_bid)*p_t
                new_ask = p_t + (self.r_t + self.bps_round_down_ask)*p_t
            elif LAMBDA_ONE <= lambda_imbalance:
                # buy order imbalance.
                new_bid = p_t - (self.r_t + self.bps_round_down_bid)*p_t
                new_ask = p_t + (self.r_t + self.bps_round_up_ask)*p_t
            else:
                self.logger.error(f'BRANCH SHOULD NEVER BE EXECUTED!')
            
            # make the new market!
            self.make_a_market(min(int(new_bid - new_bid % TICK_SIZE_IN_CENTS), bid_prices[0]), \
                                  LOT_SIZE, \
                                  max(int(new_ask + TICK_SIZE_IN_CENTS - new_ask % TICK_SIZE_IN_CENTS), ask_prices[0]), \
                                  LOT_SIZE
                              )

            # -------------------------------- THE MAIN ALGORITHM -------------------------------- #

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        if self.curr_bid is not None and self.curr_bid['id'] == client_order_id:
            self.position += volume
            self.curr_bid['volume'] -= volume
        elif self.curr_ask is not None and self.curr_ask['id'] == client_order_id:
            self.position -= volume
            self.curr_ask['volume'] -= volume
        else:
            self.logger.error(f'ORDER {client_order_id} WAS NOT IN self.orders WHEN IT REACHED ORDER FILLED MESSAGE.')

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        """Called when the status of one of your orders changes.

        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.

        If an order is cancelled its remaining volume will be zero.
        """
        if remaining_volume == 0:
            if self.curr_bid is not None and self.curr_bid['id'] == client_order_id:
                self.curr_bid = None
            elif self.curr_ask is not None and self.curr_ask['id'] == client_order_id:
                self.curr_ask = None

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
        if ask_prices[0] <= 0 and bid_prices[0] <= 0:
            return # means nothing was traded.
        
        # INSTRUMENT MUST BE ETF!!!
        if instrument == Instrument.ETF:
            # check sequence is in order.
            if sequence_number < self.last_ticks_sequence_etf:
                return
            self.last_ticks_sequence_etf = sequence_number

            sum_ask, sum_bid = sum(ask_volumes), sum(bid_volumes)

            # compute weighted average.
            numer = 0
            for vol, price in zip(bid_volumes+ask_volumes, bid_prices+ask_prices):
                numer += vol*price

            # record the total volume of this thang.
            self.vol_prime_0 = self.vol_prime_1
            self.vol_prime_1 = sum_ask+sum_bid
            
            # sliding window to keep last three weighted averages.
            self.p_prime_0 = self.p_prime_1
            self.p_prime_1 = self.p_prime_2
            self.p_prime_2 = numer / self.vol_prime_1

            # first iteration...
            if self.p_prime_0 == 0:
                return

            if self.vol_prime_1 < 0.25 * self.vol_prime_0:
                self.bps_round_down_ask = self.bps_round_down_bid = BPS_ROUND_IN_DIRECTION
                if self.p_prime_2 > 0.003:
                    # small volume big + price change. should keep our bid as low as it is.
                    self.bps_round_up_bid = -self.bps_round_up_bid
                    self.bps_round_up_ask = BPS_ROUND_AGAINST_DIRECTION
                    self.change = False
                elif self.p_prime_2 < -0.003:
                    # small volume big + price change. should keep our bid as low as it is.
                    self.bps_round_up_ask = -self.bps_round_up_ask
                    self.bps_round_up_bid = BPS_ROUND_AGAINST_DIRECTION
                    self.change = False
                else:
                    self.bps_round_up_bid = BPS_ROUND_AGAINST_DIRECTION
                    self.bps_round_up_ask = BPS_ROUND_AGAINST_DIRECTION
                    self.change = True
            else:
                self.bps_round_down_ask = self.bps_round_down_bid = BPS_ROUND_IN_DIRECTION
                self.bps_round_up_bid = self.bps_round_up_ask = BPS_ROUND_AGAINST_DIRECTION
                self.bps_round_flat = BPS_ROUND_FLAT
                self.change = True

            # calculate r_t HERE, the moment we can, so order book function can use it right when it needs to.
            self.r_t = abs( self.vol_prime_0 * ((self.p_prime_0 - self.p_prime_1) / self.p_prime_0) \
                            + self.vol_prime_1 * ((self.p_prime_1 - self.p_prime_2) / self.p_prime_1) \
                          ) / (self.vol_prime_0 + self.vol_prime_1) + self.bps_round_flat