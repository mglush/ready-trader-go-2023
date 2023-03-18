# if self.latest_volume_signal > LAMBDA_TWO:
#     # price moving up
#     # NEGATIVE POSITION = BUYBUYUBUYBUYUBUYBUYUBY
#     #   EITHER FUTURES OR ETF WHICHEVER IS CHEAPER TO DO
#     #   SELL ON THE WAY UP
#     # if self.position < 0:
#     #     # buy etf.
#     #     if self.real_ask[0] != 0:
#     #         self.send_cancel_order(self.real_ask[0])
#     #     self.market_move_coming(Side.BID, ask_prices[-1])
#     if self.hedged_position < 0 and self.hedge_bid_id == 0:
#         # buy hedge
#         # self.logger.critical(f'OFFSETTING POSITION BY GETTING RID OF HEDGE')
#         # self.hedge_bid_id = next(self.order_ids)
#         # self.send_hedge_order(self.hedge_bid_id, Side.BID, MAX_ASK_NEAREST_TICK, abs(self.hedged_position))
#         pass
#     # else:
#         # self.logger.critical(f'UP MOVE INCOMING BUT WE ABSOLUTELY CHILLEN WIT POSITION {self.position} HEDGE {self.hedged_position}')


# elif self.latest_volume_signal < -LAMBDA_TWO:
#     # price moving down
#     # POSITIVE POSITION = SELL SELL SELL
#     #   EITHER FUTURES OR ETF ACCORDINGLY
#     #   BUY ON THE WAY DOWN
#     # if self.position > 0:
#     #     if self.real_bid[0] != 0:
#     #         self.send_cancel_order(self.real_bid[0])
#     #     self.market_move_coming(Side.ASK, bid_prices[-1])
#     if self.hedged_position > 0  and self.hedge_ask_id == 0:
#         # sell hedge
#         # self.logger.critical(f'OFFSETTING POSITION BY GETTING RID OF HEDGE')
#         # self.hedge_ask_id = next(self.order_ids)
#         # self.send_hedge_order(self.hedge_ask_id, Side.ASK, MIN_BID_NEAREST_TICK, self.hedged_position)
#         pass
#     # else:
#     #     self.logger.critical(f'DOWN MOVE INCOMING BUT WE ABSOLUTELY CHILLEN WIT POSITION {self.position} HEDGE {self.hedged_position}')
# else:
#     pass # self.logger.critical(f'Volume signal says we are okay: {self.latest_volume_signal}')
# pass



MORE OLD CODE 



# help print to pinpoint parameters.
# fill_rate = self.get_avg_fill_percentage()
# fill_bid = fill_rate['bid']
# fill_ask = fill_rate['ask']

# if fill_bid > DESIRED_FILL_RATE:
#     self.adj_bid_up += 0.00001
# else:
#     self.adj_bid_up -= 0.00001
# if fill_ask > DESIRED_FILL_RATE:
#     self.adj_ask_up += 0.00001
# else:
#     self.adj_ask_up -= 0.00001

# self.adj_ask_up = min(self.adj_ask_up, 0.001)
# self.adj_bid_up = min(self.adj_bid_up, 0.001)



# def compute_beta(self, order_rate=None):
#     return BETA


# # we have our bounds, try to hunt some orders first
# if bid_volumes[0] <= LOT_SIZE:
#     if bid_prices[0] > p_t:
#         rel_dist = (bid_prices[0] - p_t) / new_bid
#         rel_loss = ((self.best_futures_ask - bid_prices[0]) / new_bid)
#         beta = self.compute_beta()
#         if rel_dist - rel_loss >= beta:
#             # do stuff
#             pass
#         else:
#             self.logger.critical(f'Determined their BID order not worth.')
#         self.logger.critical(f'Dist: {rel_dist}, loss: {rel_loss}, beta: {beta}.')

# if ask_volumes[0] <= LOT_SIZE:
#     if ask_prices[0] < p_t:
#         rel_dist = abs((ask_prices[0] - p_t) / new_ask)
#         rel_loss = ((ask_prices[0] - self.best_futures_bid) / new_ask)
#         beta = self.compute_beta()
#         if rel_dist - rel_loss >= beta:
#             # do stuff
#             pass
#         else:
#             self.logger.critical(f'Determined their ASK order not worth.')
#         self.logger.critical(f'Dist: {rel_dist}, loss: {rel_loss}, beta: {beta}.')



