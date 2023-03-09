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

constexpr int LOT_SIZE = 10;
constexpr int POSITION_LIMIT = 100;
constexpr int TICK_SIZE_IN_CENTS = 100;
constexpr int MIN_BID_NEARST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) / TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS;
constexpr int MAX_ASK_NEAREST_TICK = MAXIMUM_ASK / TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS;

AutoTrader::AutoTrader(boost::asio::io_context& context) : BaseAutoTrader(context)
{

}

void AutoTrader::DisconnectHandler()
{
    BaseAutoTrader::DisconnectHandler();
    RLOG(LG_AT, LogLevel::LL_INFO) << "execution connection lost";
}

void AutoTrader::ErrorMessageHandler(unsigned long clientOrderId,
                                     const std::string& errorMessage)
{
    RLOG(LG_AT, LogLevel::LL_INFO) << "error with order " << clientOrderId << ": " << errorMessage;
    if (clientOrderId != 0 && ((mAsks.count(clientOrderId) == 1) || (mBids.count(clientOrderId) == 1)))
    {
        OrderStatusMessageHandler(clientOrderId, 0, 0, 0);
    }
}

void AutoTrader::HedgeFilledMessageHandler(unsigned long clientOrderId,
                                           unsigned long price,
                                           unsigned long volume)
{
    RLOG(LG_AT, LogLevel::LL_INFO) << "hedge order " << clientOrderId << " filled for " << volume
                                   << " lots at $" << price << " average price in cents";
}

void AutoTrader::OrderBookMessageHandler(Instrument instrument,
                                         unsigned long sequenceNumber,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT>& askPrices,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT>& askVolumes,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT>& bidPrices,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT>& bidVolumes)
{
    RLOG(LG_AT, LogLevel::LL_INFO) << "order book received for " << instrument << " instrument"
                                   << ": ask prices: " << askPrices[0]
                                   << "; ask volumes: " << askVolumes[0]
                                   << "; bid prices: " << bidPrices[0]
                                   << "; bid volumes: " << bidVolumes[0];

    if (instrument == Instrument::ETF) {
        // re-hedge is necessary.
        if ((mPosition > 0 && abs(mPosition) < abs(hPosition))
            || (mPosition < 0 && abs(mPosition) > abs(hPosition))) {
            SendHedgeOrder(mNextMessageId++, Side::BUY, MAX_ASK_NEAREST_TICK, abs(mPosition + hPosition));
            RLOG(LG_AT, LogLevel::LL_DEBUG) << "Re-hedged position by buying futures: " << mPosition << " is now equal to " << hPosition;
        } else if ((mPosition > 0 && abs(mPosition) > abs(hPosition))
            || (mPosition < 0 && abs(mPosition) < abs(hPosition))) {
            SendHedgeOrder(mNextMessageId++, Side::SELL, MIN_BID_NEARST_TICK, abs(mPosition + hPosition));
            RLOG(LG_AT, LogLevel::LL_DEBUG) << "Re-hedged position by selling futures: " << mPosition << " is now equal to " << hPosition;
        } else {
            RLOG(LG_AT, LogLevel::LL_DEBUG) << "Position is hedged correctly: " << mPosition << " is equal to " << hPosition;
        }

        // check if we can profitably unwind a position.
        if (mPosition > 0 && last_bid != 0 && bidPrices[0] < last_bid) {
            SendInsertOrder(mAskId, Side::SELL, bidPrices[0], LOT_SIZE, Lifespan::GOOD_FOR_DAY);
        } else if (mPosition < 0 && last_ask != 0 && askPrices[0] > last_ask) {
            SendInsertOrder(mBidId, Side::BUY, askPrices[0], LOT_SIZE, Lifespan::GOOD_FOR_DAY);
        } else { 
            // nothing to unwind. 
        }

        // find the current fair market price.
        // attempt 1: micro-pricing: weight the bid price by the ask size and vice-versa.
        // ONLY USING FIRST ELEMENTS OF ARRAY FOR NOW.
        float totalVolume = askVolumes[0] + bidVolumes[0];
        float newFairPrice = askPrices[0] * (static_cast<float>(bidVolumes[0]) / totalVolume) + bidPrices[0] * (static_cast<float>(askVolumes[0]) / totalVolume);
        RLOG(LG_AT, LogLevel::LL_ERROR) << "newFairPrice is now = " << newFairPrice;

        // find the fair spread.
        // attempt 1: leave as current spread.
        unsigned long spread = askPrices[0] - bidPrices[0];

        // find the new ask and bid prices.
        // attempt 1: simply use fair price and spread above.
        float newAskPrice = newFairPrice + static_cast<int>((spread / 2));
        float newBidPrice = newFairPrice - static_cast<int>((spread / 2));

        // check if calculated ask and bid prices require us to cancel trade.
        if (mAskId != 0 && newAskPrice != 0 && newAskPrice < mAskPrice) {
            SendCancelOrder(mAskId);
            mAskId = 0;
        }

        if (mBidId != 0 && newBidPrice != 0 && newBidPrice > mBidPrice) {
            SendCancelOrder(mBidId);
            mBidId = 0;
        }

        // check if risk parameters allow for a trade,
        // then check if calculated ask and bid prices allow for a trade.
        if (mAskId == 0 && newAskPrice != 0 && mPosition > LOT_SIZE - POSITION_LIMIT) {
            // can try to sell.
            mAskId = mNextMessageId++;
            mAskPrice = newAskPrice;
            SendInsertOrder(mAskId, Side::SELL, static_cast<int>((static_cast<int>(newAskPrice) / TICK_SIZE_IN_CENTS) * TICK_SIZE_IN_CENTS), LOT_SIZE, Lifespan::GOOD_FOR_DAY);
            RLOG(LG_AT, LogLevel::LL_ERROR) << "Sent order in to sell for " << static_cast<int>((static_cast<int>(newAskPrice) / TICK_SIZE_IN_CENTS) * TICK_SIZE_IN_CENTS);
            mAsks.emplace(mAskId);
        }

        if (mBidId == 0 && newBidPrice != 0 && mPosition < POSITION_LIMIT - LOT_SIZE) {
            // can try to buy.
            mBidId = mNextMessageId++;
            mBidPrice = newBidPrice;
            SendInsertOrder(mBidId, Side::BUY, static_cast<int>((static_cast<int>(newBidPrice) / TICK_SIZE_IN_CENTS) * TICK_SIZE_IN_CENTS), LOT_SIZE, Lifespan::GOOD_FOR_DAY);
            RLOG(LG_AT, LogLevel::LL_ERROR) << "Sent order in to buy for " << static_cast<int>((static_cast<int>(newBidPrice) / TICK_SIZE_IN_CENTS) * TICK_SIZE_IN_CENTS);
            mBids.emplace(mBidId);
        }

    } else if (instrument == Instrument::FUTURE) {
        // implement future action  
    } else {
        RLOG(LG_AT, LogLevel::LL_ERROR) << "unknown instrument type!";
    }
}

void AutoTrader::OrderFilledMessageHandler(unsigned long clientOrderId,
                                           unsigned long price,
                                           unsigned long volume)
{
    RLOG(LG_AT, LogLevel::LL_INFO) << "order " << clientOrderId << " filled for " << volume
                                   << " lots at $" << price << " cents";
    if (mAsks.count(clientOrderId) == 1)
    {
        mPosition -= (long)volume;
        last_bid = price;
        SendHedgeOrder(mNextMessageId++, Side::BUY, MAX_ASK_NEAREST_TICK, volume);
        hPosition += (long)volume;
    }
    else if (mBids.count(clientOrderId) == 1)
    {
        mPosition += (long)volume;
        last_ask = price;
        SendHedgeOrder(mNextMessageId++, Side::SELL, MIN_BID_NEARST_TICK, volume);
        hPosition -= (long)volume;
    }
    else
    {
        RLOG(LG_AT, LogLevel::LL_ERROR) << "NOTHING WILL BE HDEGED HERE!!!";
    }
}

void AutoTrader::OrderStatusMessageHandler(unsigned long clientOrderId,
                                           unsigned long fillVolume,
                                           unsigned long remainingVolume,
                                           signed long fees)
{
    if (remainingVolume == 0)
    {
        if (clientOrderId == mAskId)
        {
            mAskId = 0;
        }
        else if (clientOrderId == mBidId)
        {
            mBidId = 0;
        }

        mAsks.erase(clientOrderId);
        mBids.erase(clientOrderId);
    }
}

void AutoTrader::TradeTicksMessageHandler(Instrument instrument,
                                          unsigned long sequenceNumber,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT>& askPrices,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT>& askVolumes,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT>& bidPrices,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT>& bidVolumes)
{
    RLOG(LG_AT, LogLevel::LL_INFO) << "trade ticks received for " << instrument << " instrument"
                                   << ": ask prices: " << askPrices[0]
                                   << "; ask volumes: " << askVolumes[0]
                                   << "; bid prices: " << bidPrices[0]
                                   << "; bid volumes: " << bidVolumes[0];
}