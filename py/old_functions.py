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

def send_FAK_order(self, side, price, volume):
    '''
    Function that helps us safely send a Fill And Kill
    order at the given price and volume and direciton.

    Parameters:
    side(type):     Side.BID or Side.ASK
    volume (int):   how many?
    price (int):    for how much?
    '''
    if abs(self.position) + volume < POSITION_LIMIT:
        next_id = next(self.order_ids)
        self.send_insert_order(next_id, side, price, volume, Lifespan.FILL_AND_KILL)

def place_one_order(self, type, volume, price) -> None:
    '''
    Places a single order with given volume and price, as a limit order.

    Returns: nothing.
    '''
    if volume == 0: return # base case, don't do anything on 0 volume orders.
    
    flag = self.check_wash_order(type, price)
    if type == Side.BID:
        if len(self.current_orders) < LIVE_ORDER_LIMIT \
        and self.total_volume_of_current_orders()['bid'] + self.position + volume < POSITION_LIMIT \
        and not flag \
        and self.check_num_operations():
            self.logger.info(f'PLACING SINGLE ORDER AT BID {price} VOLUME {volume}!') 
            bid_id = next(self.order_ids)
            self.last_orders.append(bid_id)
            self.record_order(bid_id, Side.BID, price, volume, Lifespan.GOOD_FOR_DAY)
            self.send_insert_order(bid_id, Side.BID, price, volume, Lifespan.GOOD_FOR_DAY) # LIMIT ORDER = GOOD FOR DAY ORDER
            self.times_of_events.insert(0, TIME_MODULE.time())
    elif type == Side.ASK:
        if len(self.current_orders) < LIVE_ORDER_LIMIT \
        and -self.total_volume_of_current_orders()['ask'] + self.position - volume > -POSITION_LIMIT \
        and not flag \
        and self.check_num_operations():
            self.logger.info(f'PLACING ORDER AT ASK {price} VOLUME {volume}!')
            ask_id = next(self.order_ids)
            self.last_orders.append(ask_id)
            self.record_order(ask_id, Side.ASK, price, volume, Lifespan.GOOD_FOR_DAY)
            self.send_insert_order(ask_id, Side.ASK, price, volume, Lifespan.GOOD_FOR_DAY) # LIMIT ORDER = GOOD FOR DAY ORDER
            self.times_of_events.insert(0, TIME_MODULE.time())

def place_two_orders(self, bid, bid_volume, ask, ask_volume) -> None:
    '''
    Places two orders at the given bid and ask with given volumes,
    inserts orders into current_orders data structure.
    in the function, bid total temp and ask total temp are the
    total volumes of current bids and asks we have placed.

    Returns: nothing.
    '''
    # make sure we are not placing orders that are potential wash trades.
    # this function could be made more specific, but for now it's kept general as it works.
    bid_wash_flag = self.check_wash_order(Side.BID, bid)
    ask_wash_flag = self.check_wash_order(Side.ASK, ask)
        
    # try to place em both at once if we can. otherwise place one of them.
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
        
        self.last_orders.append(bid_id)
        self.last_orders.append(ask_id)

        self.send_insert_order(bid_id, Side.BID, bid, bid_volume, Lifespan.GOOD_FOR_DAY) # LIMIT ORDER = GOOD FOR DAY ORDER
        self.times_of_events.insert(0, TIME_MODULE.time())
        self.send_insert_order(ask_id, Side.ASK, ask, ask_volume, Lifespan.GOOD_FOR_DAY) # LIMIT ORDER = GOOD FOR DAY ORDER
        self.times_of_events.insert(0, TIME_MODULE.time())
    elif bid_volume > 0 \
        and len(self.current_orders) < LIVE_ORDER_LIMIT \
        and self.total_volume_of_current_orders()['bid'] + self.position + bid_volume < POSITION_LIMIT \
        and not bid_wash_flag \
        and self.check_num_operations():

        self.logger.info(f'PLACING ORDER AT BID {bid} VOLUME {bid_volume}!') 
        bid_id = next(self.order_ids)
        self.record_order(bid_id, Side.BID, bid, bid_volume, Lifespan.GOOD_FOR_DAY, None)
        self.last_orders.append(bid_id)
        self.send_insert_order(bid_id, Side.BID, bid, bid_volume, Lifespan.GOOD_FOR_DAY) # LIMIT ORDER = GOOD FOR DAY ORDER
        self.times_of_events.insert(0, TIME_MODULE.time())
    elif ask_volume > 0 \
        and len(self.current_orders) < LIVE_ORDER_LIMIT \
        and -self.total_volume_of_current_orders()['ask'] + self.position - ask_volume > -POSITION_LIMIT \
        and not ask_wash_flag \
        and self.check_num_operations():

        self.logger.info(f'PLACING ORDER AT ASK {ask} VOLUME {ask_volume}!')
        ask_id = next(self.order_ids)
        self.record_order(ask_id, Side.ASK, ask, ask_volume, Lifespan.GOOD_FOR_DAY, None)
        self.last_orders.append(ask_id)
        self.send_insert_order(ask_id, Side.ASK, ask, ask_volume, Lifespan.GOOD_FOR_DAY) # LIMIT ORDER = GOOD FOR DAY ORDER
        self.times_of_events.insert(0, TIME_MODULE.time())
    else:
        self.logger.info(f'CANNOT PLACE PAIR OF ORDERS AT THIS MOMENT, RISK PARAMETERS DO NOT ALLOW FOR THIS.')

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


# ------------------------- UNUSED COMPUTRATIONS ------------------------- #

# --------------- LOGIC THAT WAS USED FOR THEO PRICE AND SPREAD --------------- #
# weighted average to compute theoretical_price, to be modified later.
total_volume = sum(ask_volumes) + sum(bid_volumes)
ask_volume_ratios = np.array(np.array(ask_volumes)/total_volume)
bid_volume_ratios = np.array(np.array(bid_volumes)/total_volume)
theoretical_price = np.dot(np.array(ask_prices), ask_volume_ratios) + np.dot(np.array(bid_prices), bid_volume_ratios)

# standard deviation to use for spread.

spread = 2 * abs(self.latest_volume_signal) * np.sqrt(np.std(np.array(ask_prices + bid_prices)))
spread = ask_prices[0] - bid_prices[0]

# scale = 1 / (self.average_time_to_fill() / (ORDER_TTL / 2))
# spread = scale * spread if scale > 0 else spread

# --------------- LOGIC THAT WAS USED TO PLACE HEDGE ORDER ANY TIME WE FILL ANY SHARES --------------- #

# some shares were bought, this is our reaction to that.        
if fill_volume > 0:
    if order['type'] == Side.BID:
        self.position += fill_volume # adjust our position here based on what type of order was exceuted.
        if self.check_num_operations(): # removing this line will allow for auto-hedging every micro-change in position.
            next_id = next(self.order_ids)
            self.hedge_record_order(next_id, Side.ASK, fill_volume)
            self.send_hedge_order(next_id, Side.ASK, order['price'], fill_volume)
            self.times_of_events.insert(0, TIME_MODULE.time())
    elif order['type'] == Side.ASK:
        self.position -= fill_volume # adjust our position here based on what type of order was exceuted.
        if self.check_num_operations(): # removing this line will allow for auto-hedging every micro-change in position.
            next_id = next(self.order_ids)
            self.hedge_record_order(next_id, Side.BID, fill_volume)
            self.send_hedge_order(next_id, Side.BID, order['price'], fill_volume)
            self.times_of_events.insert(0, TIME_MODULE.time())
    else:
        self.logger.error('ORDER TYPE IS MESSED UP')