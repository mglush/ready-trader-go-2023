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
#ifndef CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_BASEAUTOTRADER_H
#define CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_BASEAUTOTRADER_H

#include <array>
#include <cstddef>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include <boost/asio/io_context.hpp>

#include "connectivitytypes.h"
#include "protocol.h"
#include "types.h"

namespace ReadyTraderGo {

class BaseAutoTrader
{
public:
    explicit BaseAutoTrader(boost::asio::io_context& context) : mContext(context) {};

    virtual void SendAmendOrder(unsigned long clientOrderId, unsigned long volume);
    virtual void SendCancelOrder(unsigned long clientOrderId);
    virtual void SendHedgeOrder(unsigned long clientOrderId,
                                Side side,
                                unsigned long price,
                                unsigned long volume);
    virtual void SendInsertOrder(unsigned long clientOrderId,
                                 Side side,
                                 unsigned long price,
                                 unsigned long volume,
                                 Lifespan lifespan);

    virtual void SetExecutionConnection(std::unique_ptr<IConnection>&& connection);
    virtual void SetInformationSubscription(std::shared_ptr<ISubscription>&& subscription);
    virtual void SetLoginDetails(std::string teamName, std::string secret);

protected:
    boost::asio::io_context& mContext;
    std::unique_ptr<IConnection> mExecutionConnection = nullptr;
    std::shared_ptr<ISubscription> mInformationSubscription = nullptr;

    std::string mTeamName;
    std::string mSecret;

    virtual void DisconnectHandler();
    virtual void MessageHandler(IConnection*, unsigned char, unsigned char const*, std::size_t);
    virtual void MessageHandler(ISubscription* subscription,
                                unsigned char messageType,
                                unsigned char const* data,
                                std::size_t size);

    // Message callbacks
    virtual void ErrorMessageHandler(unsigned long clientOrderId,
                                     const std::string& errorMessage) {};
    virtual void HedgeFilledMessageHandler(unsigned long clientOrderId,
                                           unsigned long price,
                                           unsigned long volume) {};
    virtual void OrderBookMessageHandler(Instrument instrument,
                                         unsigned long sequenceNumber,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT>& askPrices,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT>& askVolumes,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT>& bidPrices,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT>& bidVolumes) {};
    virtual void OrderFilledMessageHandler(unsigned long clientOrderId,
                                           unsigned long price,
                                           unsigned long volume) {};
    virtual void OrderStatusMessageHandler(unsigned long clientOrderId,
                                           unsigned long fillVolume,
                                           unsigned long remainingVolume,
                                           signed long fees) {};
    virtual void TradeTicksMessageHandler(Instrument instrument,
                                          unsigned long sequenceNumber,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT>& askPrices,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT>& askVolumes,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT>& bidPrices,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT>& bidVolumes) {};
};

inline void BaseAutoTrader::DisconnectHandler()
{
    mContext.stop();
}

inline void BaseAutoTrader::SetInformationSubscription(std::shared_ptr<ISubscription>&& subscription)
{
    mInformationSubscription = std::move(subscription);
    mInformationSubscription->SetName("Info");
    mInformationSubscription->MessageReceived = [this](ISubscription* s,
                                                       unsigned char t,
                                                       unsigned char const* d,
                                                       std::size_t z) { MessageHandler(s, t, d, z); };
    mInformationSubscription->AsyncReceive();
}

inline void BaseAutoTrader::SendAmendOrder(unsigned long clientOrderId, unsigned long volume)
{
    mExecutionConnection->SendMessage(MessageType::AMEND_ORDER,
                                      AmendMessage{clientOrderId, volume});
}

inline void BaseAutoTrader::SendCancelOrder(unsigned long clientOrderId)
{
    mExecutionConnection->SendMessage(MessageType::CANCEL_ORDER,
                                      CancelMessage{clientOrderId});
}

inline void BaseAutoTrader::SendHedgeOrder(unsigned long clientOrderId,
                                           Side side,
                                           unsigned long price,
                                           unsigned long volume)
{
    mExecutionConnection->SendMessage(MessageType::HEDGE_ORDER,
                                      HedgeMessage{clientOrderId,
                                                   side,
                                                   price,
                                                   volume});
}

inline void BaseAutoTrader::SendInsertOrder(unsigned long clientOrderId,
                                            Side side,
                                            unsigned long price,
                                            unsigned long volume,
                                            Lifespan lifespan)
{
    mExecutionConnection->SendMessage(MessageType::INSERT_ORDER,
                                      InsertMessage{clientOrderId,
                                                    side,
                                                    price,
                                                    volume,
                                                    lifespan});
}

inline void BaseAutoTrader::SetLoginDetails(std::string teamName, std::string secret)
{
    mTeamName = std::move(teamName);
    mSecret = std::move(secret);
}

}

#endif //CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_BASEAUTOTRADER_H
