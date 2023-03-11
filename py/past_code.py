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
                elif new_bid_by_tick > bid_prices[0] and new_ask_by_tick < ask_prices[0]:
                    # our interval is WITHIN the actual market interval, GREAT!
                    # => just place orders at the bid and ask we calculated, ba-da-bing ba-da-bang.
                    # must be a better way than just using LOT_SIZE here!!!
                    self.logger.info("our interval is WITHIN the actual market interval")
                    self.place_orders_at_two_levels(new_bid_by_tick, LOT_SIZE, new_ask_by_tick, LOT_SIZE)
                elif new_ask_by_tick > ask_prices[0] and new_bid_by_tick < bid_prices[0]:
                    # our interval CONTAINS the actual market interval, this is a little interesting, needs some thought.
                    self.logger.info("our interval CONTAINS the actual market interval")
                    self.place_orders_at_two_levels(new_bid_by_tick, 2*LOT_SIZE, new_ask_by_tick, 2*LOT_SIZE)
                elif new_ask_by_tick == ask_prices[0] and new_bid_by_tick == bid_prices[0]:
                    # our interval perfectly MATCHES the actual market interval, also a little interesting, needs some thought.
                    # self.logger.info("THIS BRACNH IS IMPLEMENTED!")
                    self.logger.info("our interval perfectly MATCHES the actual market interval")
                    # we don't really want to trade a super tight spread for now.
                    if ask_prices[0] - bid_prices[0] != TICK_SIZE_IN_CENTS:
                        self.place_orders_at_two_levels(new_bid_by_tick, LOT_SIZE, new_ask_by_tick, LOT_SIZE)
                else:
                    # i don't think there are more cases, but should handle all branches of execution just in case.
                    # self.logger.info(f'THIS LINE SHOULD NEVER APPEAR!!! new ask = {new_ask_by_tick} real ask = {ask_prices[0]} new_bid = {new_bid_by_tick} real bid = {bid_prices[0]}')
                    pass