// Copyright 2021 Optiver Asia Pacific Pty. Ltd.
//
// This file is part of Ready Trader Go.
//
//     Ready Trader Go is free software: you can redistribute it and/or
//     modify it under the terms of the GNU Affero General Public License
//     as published by the Free Software Foundation, either version 3 of
//     the License, or (at your option) any later version.
//
//     Ready Trader Go is distributed in the hope that it will be useful,
//     but WITHOUT ANY WARRANTY; without even the implied warranty of
//     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
//     GNU Affero General Public License for more details.
//
//     You should have received a copy of the GNU Affero General Public
//     License along with Ready Trader Go.  If not, see
//     <https://www.gnu.org/licenses/>.
#include <array>

#include <boost/asio/io_context.hpp>

#include <ready_trader_go/logging.h>

#include "autotrader.h"

using namespace ReadyTraderGo;

RTG_INLINE_GLOBAL_LOGGER_WITH_CHANNEL(LG_AT, "AUTO")

constexpr int LOT_SIZE = 20;
constexpr int POSITION_LIMIT = 80;
constexpr int TICK_SIZE_IN_CENTS = 100;
constexpr int MIN_BID_NEARST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) / TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS;
constexpr int MAX_ASK_NEAREST_TICK = MAXIMUM_ASK / TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS;

constexpr float BPS_ROUND_FLAT = 0.0000;
constexpr float BPS_ROUND_DOWN = 0.0001;
constexpr float BPS_ROUND_UP = 0.001;
constexpr float LAMBDA_ONE = 0.5;

constexpr int UNHEDGED_LOTS_LIMIT = 10;
constexpr int MAX_TIME_UNHEDGED = 58;
constexpr int ATV_WIN_SIZE = 20;

// unwinding variables:
constexpr int POSITION_LIMIT_TO_UNWIND = 25;
constexpr int HEDGE_POSITION_LIMIT_TO_UNWIND = 5;
constexpr int HOW_OFTEN_TO_CHECK_HEDGE = 3;
constexpr int AMOUNT_TO_UNWIND_PER_ORDER = 2;
constexpr int UNWIND_FACTOR = 1.005;

AutoTrader::AutoTrader(boost::asio::io_context& context) : BaseAutoTrader(context)
{
    time_of_last_imbalance = 0;

    real_bid = std::vector<int>(3, 0);
    real_ask = std::vector<int>(3, 0);
    traded_volumes = boost::circular_buffer<int>(ATV_WIN_SIZE);
    traded_volumes.push_back(0);

    RLOG(LG_AT, LogLevel::LL_WARNING) << "AutoTrader has been constructed properly";
}

/* THIS SECTION CONTAINS OUR HELPER FUNCTION DEFINITIONS! */

// Compute volume pressure magnitude and side based on newest ticks update message.
// If positive, asks are getting knocked out and price should be rising.
// If negative, bids are getting cleared and price should be falling. We could reverse this.
// Returns: the indicator as a float.
float AutoTrader::compute_volume_signal(unsigned long ask_vol,
                                        unsigned long bid_vol)
{
    if (traded_volumes.size() == 0) return 0.0;
    unsigned long total_traded_volume = 0;
    for (int i = 0; i < traded_volumes.size(); ++i)
        total_traded_volume += traded_volumes[i];
    return (bid_vol - ask_vol) / (total_traded_volume/ traded_volumes.size());
}

// Tries to place both a bid and an ask at the provided prices and volumes.
// If one of the orders cannot be placed, we still place the other one.
void AutoTrader::make_a_market(unsigned long ask,
                                unsigned long ask_vol,
                                unsigned long bid, 
                                unsigned long bid_vol)
{
    // RLOG(LG_AT, LogLevel::LL_INFO) << "TRYING TO MAKE  A MARKET!";
    if (bid > 0 && ask > 0 \
            && position + LOT_SIZE < POSITION_LIMIT \
            && position - LOT_SIZE > -POSITION_LIMIT \
            && bid != real_bid[1] \
            && ask != real_ask[1])
    {
        if (real_bid[0] != 0)
            SendCancelOrder(real_bid[0]);
        if (real_ask[0] != 0)
            SendCancelOrder(real_ask[0]);

        theo_orders[mNextMessageId++] = boost::make_tuple<Side, unsigned long, unsigned long>(Side::BUY, bid, bid_vol);
        theo_orders[mNextMessageId++] = boost::make_tuple<Side, unsigned long, unsigned long>(Side::SELL, ask, ask_vol);

        SendInsertOrder(mNextMessageId - 2, Side::BUY, bid, bid_vol, Lifespan::GOOD_FOR_DAY);
        SendInsertOrder(mNextMessageId - 1, Side::SELL, ask, ask_vol, Lifespan::GOOD_FOR_DAY);
    } else if (bid > 0 \
            && position + LOT_SIZE < POSITION_LIMIT \
            && bid != real_bid[1])
    {
        if (real_bid[0] != 0)
            SendCancelOrder(real_bid[0]);

        theo_orders[mNextMessageId] = boost::make_tuple<Side, unsigned long, unsigned long>(Side::BUY, bid, bid_vol);
        SendInsertOrder(mNextMessageId++, Side::BUY, bid, bid_vol, Lifespan::GOOD_FOR_DAY);
    } else if (ask > 0 \
            && position - LOT_SIZE > -POSITION_LIMIT \
            && ask != real_ask[1])
    {
        if (real_ask[0] != 0)
            SendCancelOrder(real_ask[0]);

        theo_orders[mNextMessageId] = boost::make_tuple<Side, unsigned long, unsigned long>(Side::SELL, ask, ask_vol);
        SendInsertOrder(mNextMessageId++, Side::SELL, ask, ask_vol, Lifespan::GOOD_FOR_DAY);
    }
    else {
        
    }
}

// Function to hedge us. Called only as a last resort before timer runs out.
void AutoTrader::hedge() {
    RLOG(LG_AT, LogLevel::LL_INFO) << "ENTER HEDGE";
    RLOG(LG_AT, LogLevel::LL_INFO) << "POSITION IS " << std::to_string(position) << " HEDGE IS " << std::to_string(hedged_position);

    if (position < 0) {
        if (-position < hedged_position) { // sell
            if (hedge_ask_id == 0) {
                hedge_ask_id = mNextMessageId;
                SendHedgeOrder(mNextMessageId++, Side::SELL, MIN_BID_NEARST_TICK, abs(position + hedged_position));
            }
        } else { // buy
            if (hedge_bid_id == 0) {
                hedge_bid_id = mNextMessageId;
                SendHedgeOrder(mNextMessageId++, Side::BUY, MAX_ASK_NEAREST_TICK, abs(position + hedged_position));
            }
        }
    } else if (position == 0) {
        if (hedged_position < 0) { // buy.
            if (hedge_bid_id == 0) {
                hedge_bid_id = mNextMessageId;
                SendHedgeOrder(mNextMessageId++, Side::BUY, MAX_ASK_NEAREST_TICK, abs(position + hedged_position));
            }
        } else { // sell
            if (hedge_ask_id == 0) {
                hedge_ask_id = mNextMessageId;
                SendHedgeOrder(mNextMessageId++, Side::SELL, MIN_BID_NEARST_TICK, abs(position + hedged_position));
            }
        }
            
    } else if (position > 0) {
        if (position > -hedged_position) { // sell.
            if (hedge_ask_id == 0) {
                hedge_ask_id = mNextMessageId;
                SendHedgeOrder(mNextMessageId++, Side::SELL, MIN_BID_NEARST_TICK, abs(position + hedged_position));
            }
        } else { // buy.
            if (hedge_bid_id == 0) {
                hedge_bid_id = mNextMessageId;
                SendHedgeOrder(mNextMessageId++, Side::BUY, MAX_ASK_NEAREST_TICK, abs(position + hedged_position));
            }
        }
            
    } else {
        RLOG(LG_AT, LogLevel::LL_FATAL) << "2 THIS CASE SHOULD NEVER HAPPEN";
    }
}

// Unwinds our hedge when it is profitable to do so.
void AutoTrader::realize_hedge_PnL() {
    if (hedged_position == 0)
        return;
    
    float avg_entry = hedged_money_in / static_cast<float>(hedged_position);
    if (hedged_position > POSITION_LIMIT_TO_UNWIND) {
        if (avg_entry < best_futures_bid - (best_futures_ask - best_futures_bid)) {
            if (hedge_ask_id == 0) {
                hedge_ask_id = mNextMessageId;
                SendHedgeOrder(mNextMessageId++, Side::SELL, MIN_BID_NEARST_TICK, hedged_position);
            }
        }
    } else if (hedged_position < -POSITION_LIMIT_TO_UNWIND) {
        if (avg_entry > best_futures_ask + (best_futures_ask - best_futures_bid)) {
            if (hedge_bid_id == 0) {
                hedge_bid_id = mNextMessageId;
                SendHedgeOrder(mNextMessageId++, Side::BUY, MAX_ASK_NEAREST_TICK, abs(hedged_position));
            }
        }
    }
}

// This function realizes out PnL if we have any past a certain threshold.
// That is, if we have +50 etf and the stock moved up, we should realize our PnL
// by selling off some of the etf at the new higher price via a fill and kill.
void AutoTrader::realize_PnL(unsigned long bid,
                            unsigned long ask)
{
    if (position == 0)
        return;

    float avg_entry = money_in / static_cast<float>(position);

    RLOG(LG_AT, LogLevel::LL_INFO) << "AVERAGE POSITION CALCULATED AT " << std::to_string(avg_entry);
    if (position > POSITION_LIMIT_TO_UNWIND) {
        if (avg_entry < bid / UNWIND_FACTOR) { // REALIZE PNL BY SELLING A SHARE
            RLOG(LG_AT, LogLevel::LL_INFO) << "OPPORTUNITY";
            fak_orders[mNextMessageId] = boost::tuple<Side, unsigned long, unsigned long>(Side::SELL, bid, 1);
            SendInsertOrder(mNextMessageId++, Side::SELL, bid, 1, Lifespan::FILL_AND_KILL);
        } else {
            
        }
    }
    else if (0 < position && position <= POSITION_LIMIT_TO_UNWIND) {
        float cushion = (ask - bid) * (POSITION_LIMIT_TO_UNWIND / static_cast<float>(position));
        if (avg_entry < bid - cushion) { // REALIZE PNL BY SELLING A SHARE
            RLOG(LG_AT, LogLevel::LL_INFO) << "OPPORTUNITY";
            fak_orders[mNextMessageId] = boost::tuple<Side, unsigned long, unsigned long>(Side::SELL, bid, LOT_SIZE);
            SendInsertOrder(mNextMessageId++, Side::SELL, bid, LOT_SIZE, Lifespan::FILL_AND_KILL);
        } else {

        }
    } else if (-POSITION_LIMIT_TO_UNWIND <= position && position < 0) {
        float cushion = (ask - bid) * (POSITION_LIMIT_TO_UNWIND / static_cast<float>(position));
        if (avg_entry > ask + cushion) { // REALIZE PNL BY BUYING A SHARE
            RLOG(LG_AT, LogLevel::LL_INFO) << "OPPORTUNITY";
            fak_orders[mNextMessageId] = boost::tuple<Side, unsigned long, unsigned long>(Side::BUY, ask, LOT_SIZE);
            SendInsertOrder(mNextMessageId++, Side::BUY, ask, LOT_SIZE, Lifespan::FILL_AND_KILL);
        } else {

        }
    } 
    else if (position < -POSITION_LIMIT_TO_UNWIND) {
        if (avg_entry > ask * UNWIND_FACTOR) { // REALIZE PNL BY BUYING A SHARE
            RLOG(LG_AT, LogLevel::LL_INFO) << "OPPORTUNITY";
            fak_orders[mNextMessageId] = boost::tuple<Side, unsigned long, unsigned long>(Side::BUY, ask, 1);
            SendInsertOrder(mNextMessageId++, Side::BUY, ask, 1, Lifespan::FILL_AND_KILL);
        } else {

        }
    }
}

/* THIS SECTION CONTAINS OUR HELPER FUNCTION DEFINITIONS! */

void AutoTrader::DisconnectHandler()
{
    BaseAutoTrader::DisconnectHandler();
}

void AutoTrader::ErrorMessageHandler(unsigned long clientOrderId, const std::string& errorMessage) { }

void AutoTrader::HedgeFilledMessageHandler(unsigned long clientOrderId,
                                        unsigned long price, 
                                        unsigned long volume)
{
    RLOG(LG_AT, LogLevel::LL_INFO) << "FILLED A HEDGE" << std::to_string(clientOrderId) << " PRICE " << std::to_string(price) << " VOLUME " << std::to_string(volume);
    we_are_hedged = true; // for the moment of this function, we are counting ourselves as hedged so
                            // that this function doesn't get called more than once at the same time.

    if (clientOrderId == hedge_bid_id) { // hedge buy order was filled.
        hedged_position += volume;
        hedge_bid_id = 0;
        hedged_money_in += price * volume;
    } else if (clientOrderId == hedge_ask_id) { // hedge sell order was filled.
        hedged_position -= volume;
        hedge_ask_id = 0;
        hedged_money_in -= price * volume;
    }
    else {
        RLOG(LG_AT, LogLevel::LL_FATAL) << "3 THIS CASE SHOULD NEVER HAPPEN";
    }

    if (abs(position + hedged_position) < UNHEDGED_LOTS_LIMIT)
        we_are_hedged = true;
    else we_are_hedged = false;

    if (hedged_position == 0)
        hedged_money_in = 0;
}

void AutoTrader::OrderBookMessageHandler(Instrument instrument,
                                         unsigned long sequenceNumber,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT>& askPrices,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT>& askVolumes,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT>& bidPrices,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT>& bidVolumes)
{

    RLOG(LG_AT, LogLevel::LL_INFO) << "\tPOSITION = " << std::to_string(position) << " HEDGE = " << std::to_string(hedged_position);
    if (bidPrices[0] == 0 || askPrices[0] == 0 || p_prime_0 == 0 || p_prime_1 == 0)
        return; // we don't got shid to do here.

    if (instrument == Instrument::FUTURE) {
        if (sequenceNumber < last_order_book_sequence_fut) {
            RLOG(LG_AT, LogLevel::LL_INFO) << "OLD INFORMATION!!!";
            return; // old sequence
        }
        last_order_book_sequence_fut = sequenceNumber;
        best_futures_bid = bidPrices[0];
        best_futures_ask = askPrices[0];

        if (sequenceNumber % HOW_OFTEN_TO_CHECK_HEDGE == 0) {
            if (we_are_hedged == true) { // if we think we are hedged.
                if (abs(position + hedged_position) > UNHEDGED_LOTS_LIMIT) { // if we actually are not hedged.
                    time_of_last_imbalance = sequenceNumber;
                    we_are_hedged = false;
                }
            } 
            else { // we are not hedged, les do something about it!
                // if (sequenceNumber - time_of_last_imbalance > 500) // absolutely must hedge.
                //     hedge();
                // else {} // realize_hedge_PnL();
            }
        }
    } else if (instrument == Instrument::ETF) {
        if (sequenceNumber < last_order_book_sequence_etf) {
            RLOG(LG_AT, LogLevel::LL_INFO) << "OLD INFORMATION!!!";
            return; // old sequence
        }
        last_order_book_sequence_etf = sequenceNumber;

        // # next, calculate current entry, and see if we have a PnL to collect.
        // realize_PnL(bidPrices[0], askPrices[0]);
        
        // # calculate p_t, based on the midpoint of the bid and ask we got just now.
        float p_t = (askPrices[0] + bidPrices[0]) / 2;

        // # calculate r_t based on our p_prime values collected in order ticks.
        float r_t = abs((p_prime_0 - p_prime_1) / p_prime_0) + BPS_ROUND_FLAT;

        // # calculate volume imbalance to see whether we need to adjust spread.
        unsigned long bidVolumeSum = 0;
        unsigned long askVolumeSum = 0;
        for (int i = 0; i < bidVolumes.size(); i++)
            bidVolumeSum += bidVolumes[i];
        for (int i = 0; i < askVolumes.size(); i++)
            askVolumeSum += askVolumes[i];

        float lambda_imbalance = static_cast<float>(bidVolumeSum - askVolumeSum) / static_cast<float>(bidVolumeSum + askVolumeSum);
        float new_bid = 0.0, new_ask = 0.0;

        // # check if we need to adjust spread based on lambda imbalance.
        if (-LAMBDA_ONE < lambda_imbalance && lambda_imbalance < LAMBDA_ONE) {
            // # the regular case, no spread adjustment.
            new_bid = p_t - (r_t)*p_t;
            new_ask = p_t + (r_t)*p_t;
        } else if (lambda_imbalance < -LAMBDA_ONE) {
            // # sell order imbalance.
            new_bid = p_t - (r_t + BPS_ROUND_UP)*p_t;
            new_ask = p_t + (r_t + BPS_ROUND_DOWN)*p_t;
        } else if (lambda_imbalance > LAMBDA_ONE) {
            // # buy order imbalance.
            new_bid = p_t - (r_t + BPS_ROUND_DOWN)*p_t;
            new_ask = p_t + (r_t + BPS_ROUND_UP)*p_t;
        } else {
            RLOG(LG_AT, LogLevel::LL_FATAL) << "4 THIS CASE SHOULD NEVER HAPPEN";
        }

        // # round new bid and new ask outward to the nearest TICK_SIZE, check if interval too tight.
        new_bid = ((int) new_bid) - (((int) new_bid) % TICK_SIZE_IN_CENTS);
        new_ask = ((int) new_ask) + TICK_SIZE_IN_CENTS - (((int) new_ask) % TICK_SIZE_IN_CENTS);

        if (new_bid > bidPrices[0])
            new_bid = bidPrices[0];
        if (new_ask < askPrices[0])
            new_ask = askPrices[0];

        // # make the new market!
        make_a_market(new_ask, LOT_SIZE, new_bid, LOT_SIZE);
    }
}

void AutoTrader::OrderFilledMessageHandler(unsigned long clientOrderId,
                                           unsigned long price,
                                           unsigned long volume)
{
    if (clientOrderId == real_bid[0]) {
        position += volume;
        real_bid[2] -= volume;
        money_in += price * volume;
        if (real_bid[2] <= 0)
            real_bid[0] = real_bid[1] = real_bid[2] = 0;
    } else if (clientOrderId == real_ask[0]) {
        position -= volume;
        real_ask[2] -= volume;
        money_in -= price * volume;
        if (real_ask[2] <= 0)
            real_ask[0] = real_ask[1] = real_ask[2] = 0;
    } else if (theo_orders.find(clientOrderId) != theo_orders.end()) {
        if (theo_orders[clientOrderId].get<0>() == Side::BUY) {
            position += volume;
            money_in += price * volume;
            signed long vol_rem = theo_orders[clientOrderId].get<2>() - volume;
            if (vol_rem > 0) {
                real_bid[0] = clientOrderId;
                real_bid[1] = theo_orders[clientOrderId].get<1>();
                real_bid[2] = vol_rem;
            } else {
                real_bid[0] = real_bid[1] = real_bid[2] = 0;
            }
        } else if (theo_orders[clientOrderId].get<0>() == Side::SELL) {
            position -= volume;
            money_in -= price*volume;
            signed long vol_rem = theo_orders[clientOrderId].get<2>() - volume;
            if (vol_rem > 0) {
                real_ask[0] = clientOrderId;
                real_ask[1] = theo_orders[clientOrderId].get<1>();
                real_ask[2] = vol_rem;
            } else {
                real_ask[0] = real_ask[1] = real_ask[2] = 0;
            }
        }
        
        theo_orders.erase(clientOrderId);
    }
    
    else if (fak_orders.find(clientOrderId) != fak_orders.end()) {
        RLOG(LG_AT, LogLevel::LL_INFO) << "FAK ORDER " << std::to_string(clientOrderId) << " FILLED FOR " << std::to_string(volume);
        if (fak_orders[clientOrderId].get<0>() == Side::BUY) {
            position += volume;
            money_in += price * volume;
            fak_orders[clientOrderId].get<2>() -= volume;
        }
        else if (fak_orders[clientOrderId].get<0>() == Side::SELL) {
            position -= volume;
            money_in -= price * volume;
            fak_orders[clientOrderId].get<2>() -= volume;
        } else {
            RLOG(LG_AT, LogLevel::LL_FATAL) << "5 THIS CASE SHOULD NEVER HAPPEN";
        }
    } else {
        RLOG(LG_AT, LogLevel::LL_FATAL) << "6 THIS CASE SHOULD NEVER HAPPEN";
    }

    if (position == 0)
        money_in = 0;
}

void AutoTrader::OrderStatusMessageHandler(unsigned long clientOrderId,
                                           unsigned long fillVolume,
                                           unsigned long remainingVolume,
                                           signed long fees)
{
    if (fillVolume == 0 and remainingVolume > 0) {
        if (theo_orders.find(clientOrderId) != theo_orders.end()) {
            if (boost::get<0>(theo_orders[clientOrderId]) == Side::BUY) {
                real_bid[0] = clientOrderId;
                real_bid[1] = theo_orders[clientOrderId].get<1>();
                real_bid[2] = theo_orders[clientOrderId].get<2>();
            } else if (boost::get<0>(theo_orders[clientOrderId]) == Side::SELL) {
                real_ask[0] = clientOrderId;
                real_ask[1] = theo_orders[clientOrderId].get<1>();
                real_ask[2] = theo_orders[clientOrderId].get<2>();
            }
        } else if (fak_orders.find(clientOrderId) != theo_orders.end()) {
            fak_orders.erase(clientOrderId);
        }
    }

    else if (remainingVolume == 0) {
        if (clientOrderId == real_bid[0])
            real_bid[0] = real_bid[1] = real_bid[2] = 0;
        else if (clientOrderId == real_ask[0])
            real_ask[0] = real_ask[1] = real_ask[2] = 0;
    }
}

void AutoTrader::TradeTicksMessageHandler(Instrument instrument,
                                          unsigned long sequenceNumber,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT>& askPrices,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT>& askVolumes,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT>& bidPrices,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT>& bidVolumes)
{
   if (askPrices[0] == 0 and bidPrices[0] == 0)
        return;
    
    // # INSTRUMENT MUST BE ETF!!!
    if (instrument == Instrument::ETF) {
        // # check sequence is in order.
        if (sequenceNumber < last_ticks_sequence_etf) {
            RLOG(LG_AT, LogLevel::LL_INFO) << "OLD INFORMATION!!!";
            return; // old sequence
        }

        last_ticks_sequence_etf = sequenceNumber;

        unsigned long bidVolumeSum = 0;
        unsigned long askVolumeSum = 0;
        signed long numer = 0;

        for (int i = 0; i < bidVolumes.size(); i++) {
            bidVolumeSum += bidVolumes[i];
            numer += bidVolumes[i] * bidPrices[i];
        }
        for (int i = 0; i < askVolumes.size(); i++) {
            askVolumeSum += askVolumes[i];
            numer += askVolumes[i] * askPrices[i];
        }

        traded_volumes.push_back(bidVolumeSum + askVolumeSum);

        // # compute signal
        latest_volume_signal = compute_volume_signal(askVolumeSum, bidVolumeSum);

        // # record this weighted average in memory.
        p_prime_0 = p_prime_1;
        p_prime_1 = numer / (bidVolumeSum + askVolumeSum);
    }
}
