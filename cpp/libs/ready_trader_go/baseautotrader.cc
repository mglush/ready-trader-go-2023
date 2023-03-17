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
#include "baseautotrader.h"
#include "error.h"
#include "logging.h"
#include "protocol.h"

RTG_INLINE_GLOBAL_LOGGER_WITH_CHANNEL(LG_BAT, "BASE")

namespace ReadyTraderGo {

void BaseAutoTrader::SetExecutionConnection(std::unique_ptr<IConnection>&& connection)
{
    mExecutionConnection = std::move(connection);
    mExecutionConnection->SetName("Exec");
    mExecutionConnection->Disconnected = [this] { DisconnectHandler(); };
    mExecutionConnection->MessageReceived = [this](IConnection* c,
                                                   unsigned char t,
                                                   unsigned char const* d,
                                                   std::size_t s) { MessageHandler(c, t, d, s); };

    RLOG(LG_BAT, LogLevel::LL_INFO) << "logging in with teamname='" << mTeamName
                                    << "' and secret='" << mSecret << '\'';
    mExecutionConnection->SendMessage(MessageType::LOGIN,
                                      LoginMessage{mTeamName, mSecret});

    mExecutionConnection->AsyncRead();
}

void BaseAutoTrader::MessageHandler(IConnection* connection,
                                    unsigned char messageType,
                                    unsigned char const* data,
                                    std::size_t size)
{
    switch (messageType)
    {
    case MessageType::ERROR_MESSAGE:
    {
        auto err = makeMessage<ErrorMessage>(data, size);
        ErrorMessageHandler(err.mClientOrderId, err.mMessage);
        break;
    }
    case MessageType::HEDGE_FILLED:
    {
        auto filled = makeMessage<HedgeFilledMessage>(data, size);
        HedgeFilledMessageHandler(filled.mClientOrderId, filled.mPrice, filled.mVolume);
        break;
    }
    case MessageType::ORDER_FILLED:
    {
        auto filled = makeMessage<OrderFilledMessage>(data, size);
        OrderFilledMessageHandler(filled.mClientOrderId, filled.mPrice, filled.mVolume);
        break;
    }
    case MessageType::ORDER_STATUS:
    {
        auto status = makeMessage<OrderStatusMessage>(data, size);
        OrderStatusMessageHandler(status.mClientOrderId, status.mFillVolume,
                                  status.mRemainingVolume, status.mFees);
        break;
    }
    default:
    {
        RLOG(LG_BAT, LogLevel::LL_ERROR) << "received execution message with unexpected type: "
                                         << static_cast<int>(messageType);
        throw ReadyTraderGoError("received execution message with unexpected type");
    }
    }
}

void BaseAutoTrader::MessageHandler(ISubscription* subscription,
                                    unsigned char messageType,
                                    unsigned char const* data,
                                    std::size_t size)
{
    switch (messageType)
    {
    case MessageType::ORDER_BOOK_UPDATE:
    {
        auto book = makeMessage<OrderBookMessage>(data, size);
        OrderBookMessageHandler(book.mInstrument, book.mSequenceNumber, book.mAskPrices,
                                book.mAskVolumes, book.mBidPrices, book.mBidVolumes);
        break;
    }
    case MessageType::TRADE_TICKS:
    {
        auto ticks = makeMessage<TradeTicksMessage>(data, size);
        TradeTicksMessageHandler(ticks.mInstrument, ticks.mSequenceNumber, ticks.mAskPrices,
                                 ticks.mAskVolumes, ticks.mBidPrices, ticks.mBidVolumes);
        break;
    }
    default:
    {
        RLOG(LG_BAT, LogLevel::LL_ERROR) << "received information message with unexpected type: "
                                         << static_cast<int>(messageType);
        throw ReadyTraderGoError("received information message with unexpected type");
    }
    }
}

}