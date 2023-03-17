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
#ifndef CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_PROTOCOL_H
#define CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_PROTOCOL_H

#include <array>
#include <cstddef>
#include <string>
#include <utility>
#include <vector>

#include "connectivitytypes.h"
#include "types.h"

namespace ReadyTraderGo {

enum MessageType : unsigned char
{
    AMEND_ORDER = 1,
    CANCEL_ORDER = 2,
    ERROR_MESSAGE = 3,
    HEDGE_FILLED = 4,
    HEDGE_ORDER = 5,
    INSERT_ORDER = 6,
    LOGIN = 7,
    ORDER_BOOK_UPDATE = 10,
    ORDER_FILLED = 8,
    ORDER_STATUS = 9,
    TRADE_TICKS = 11
};

enum MessageFieldSize : std::size_t
{
    BYTE = 1,
    LONG = 4,
    STRING = 50
};

struct AmendMessage : ISerialisable
{
    AmendMessage() = default;
    AmendMessage(unsigned long clientOrderId, unsigned long newVolume)
        : mClientOrderId(clientOrderId), mNewVolume(newVolume) {}

    std::size_t Size() const noexcept override { return MessageFieldSize::LONG * 2; }

    void Deserialise(unsigned char const* data, std::size_t size) override;
    void Serialise(unsigned char* buf) const override;

    unsigned long mClientOrderId = 0;
    unsigned long mNewVolume = 0;
};

struct CancelMessage : ISerialisable
{
    CancelMessage() = default;
    explicit CancelMessage(unsigned long clientOrderId) : mClientOrderId(clientOrderId) {}

    std::size_t Size() const noexcept override { return MessageFieldSize::LONG; }

    void Deserialise(unsigned char const* data, std::size_t size) override;
    void Serialise(unsigned char* buf) const override;

    unsigned long mClientOrderId = 0;
};

struct ErrorMessage : ISerialisable
{
    ErrorMessage() = default;
    ErrorMessage(unsigned long clientOrderId, std::string message)
        : mClientOrderId(clientOrderId), mMessage(std::move(message)) {}

    std::size_t Size() const noexcept override
    {
        return MessageFieldSize::LONG + MessageFieldSize::STRING;
    }

    void Deserialise(unsigned char const* data, std::size_t size) override;
    void Serialise(unsigned char* buf) const override;

    unsigned long mClientOrderId = 0;
    std::string mMessage;
};

struct HedgeMessage : ISerialisable
{
    HedgeMessage() = default;
    HedgeMessage(unsigned long clientOrderId,
                  Side side,
                  unsigned long price,
                  unsigned long volume)
        : mClientOrderId(clientOrderId),
          mSide(side),
          mPrice(price),
          mVolume(volume) {}

    std::size_t Size() const noexcept override { return MessageFieldSize::LONG * 3 + MessageFieldSize::BYTE; }

    void Deserialise(unsigned char const* data, std::size_t size) override;
    void Serialise(unsigned char* buf) const override;

    unsigned long mClientOrderId = 0;
    Side mSide = Side::SELL;
    unsigned long mPrice = 0;
    unsigned long mVolume = 0;
};

struct HedgeFilledMessage : ISerialisable
{
    HedgeFilledMessage() = default;
    HedgeFilledMessage(unsigned long clientOrderId,
                       unsigned long price,
                       unsigned long volume)
        : mClientOrderId(clientOrderId),
          mPrice(price),
          mVolume(volume) {}

    std::size_t Size() const noexcept override { return MessageFieldSize::LONG * 3; }

    void Deserialise(unsigned char const* data, std::size_t size) override;
    void Serialise(unsigned char* buf) const override;

    unsigned long mClientOrderId = 0;
    unsigned long mPrice = 0;
    unsigned long mVolume = 0;
};

struct InsertMessage : ISerialisable
{
    InsertMessage() = default;
    InsertMessage(unsigned long clientOrderId,
                  Side side,
                  unsigned long price,
                  unsigned long volume,
                  Lifespan lifespan)
        : mClientOrderId(clientOrderId),
          mSide(side),
          mPrice(price),
          mVolume(volume),
          mLifespan(lifespan) {}

    std::size_t Size() const noexcept override { return MessageFieldSize::LONG * 3 + MessageFieldSize::BYTE * 2; }

    void Deserialise(unsigned char const* data, std::size_t size) override;
    void Serialise(unsigned char* buf) const override;

    unsigned long mClientOrderId = 0;
    Side mSide = Side::SELL;
    unsigned long mPrice = 0;
    unsigned long mVolume = 0;
    Lifespan mLifespan = Lifespan::FILL_AND_KILL;
};

struct LoginMessage : ISerialisable
{
    LoginMessage() = default;
    LoginMessage(std::string name, std::string secret)
        : mName(std::move(name)), mSecret(std::move(secret)) {}

    std::size_t Size() const noexcept override
    {
        return MessageFieldSize::STRING * 2;
    }

    void Deserialise(unsigned char const* data, std::size_t size) override;
    void Serialise(unsigned char* buf) const override;

    std::string mName;
    std::string mSecret;
};

struct OrderBookMessage : ISerialisable
{
    OrderBookMessage() = default;
    OrderBookMessage(Instrument instrument,
                     unsigned long sequenceNumber,
                     const std::array<unsigned long, TOP_LEVEL_COUNT>& askPrices,
                     const std::array<unsigned long, TOP_LEVEL_COUNT>& askVolumes,
                     const std::array<unsigned long, TOP_LEVEL_COUNT>& bidPrices,
                     const std::array<unsigned long, TOP_LEVEL_COUNT>& bidVolumes)
        : mInstrument(instrument),
          mSequenceNumber(sequenceNumber),
          mAskPrices(askPrices),
          mAskVolumes(askVolumes),
          mBidPrices(bidPrices),
          mBidVolumes(bidVolumes) {}

    std::size_t Size() const noexcept override
    {
        return MessageFieldSize::BYTE
            + MessageFieldSize::LONG
            + MessageFieldSize::LONG * TOP_LEVEL_COUNT * 4;
    }

    void Deserialise(unsigned char const* data, std::size_t size) override;
    void Serialise(unsigned char* buf) const override;

    Instrument mInstrument = Instrument::FUTURE;
    unsigned long mSequenceNumber = 0;
    std::array<unsigned long, TOP_LEVEL_COUNT> mAskPrices = {};
    std::array<unsigned long, TOP_LEVEL_COUNT> mAskVolumes = {};
    std::array<unsigned long, TOP_LEVEL_COUNT> mBidPrices = {};
    std::array<unsigned long, TOP_LEVEL_COUNT> mBidVolumes = {};
};

struct OrderFilledMessage : ISerialisable
{
    OrderFilledMessage() = default;
    OrderFilledMessage(unsigned long clientOrderId,
                       unsigned long price,
                       unsigned long volume)
        : mClientOrderId(clientOrderId),
          mPrice(price),
          mVolume(volume) {}

    std::size_t Size() const noexcept override { return MessageFieldSize::LONG * 3; }

    void Deserialise(unsigned char const* data, std::size_t size) override;
    void Serialise(unsigned char* buf) const override;

    unsigned long mClientOrderId = 0;
    unsigned long mPrice = 0;
    unsigned long mVolume = 0;
};

struct OrderStatusMessage : ISerialisable
{
    OrderStatusMessage() = default;
    OrderStatusMessage(unsigned long clientOrderId,
                       unsigned long fillVolume,
                       unsigned long remainingVolume,
                       signed long fees)
        : mClientOrderId(clientOrderId),
          mFillVolume(fillVolume),
          mRemainingVolume(remainingVolume),
          mFees(fees) {}

    std::size_t Size() const noexcept override { return MessageFieldSize::LONG * 4; }

    void Deserialise(unsigned char const* data, std::size_t size) override;
    void Serialise(unsigned char* buf) const override;

    unsigned long mClientOrderId = 0;
    unsigned long mFillVolume = 0;
    unsigned long mRemainingVolume = 0;
    signed long mFees = 0;
};

struct TradeTicksMessage : ISerialisable
{
    TradeTicksMessage() = default;
    TradeTicksMessage(Instrument instrument,
                      unsigned long sequenceNumber,
                      const std::array<unsigned long, TOP_LEVEL_COUNT> &askPrices,
                      const std::array<unsigned long, TOP_LEVEL_COUNT> &askVolumes,
                      const std::array<unsigned long, TOP_LEVEL_COUNT> &bidPrices,
                      const std::array<unsigned long, TOP_LEVEL_COUNT> &bidVolumes)
            : mInstrument(instrument),
              mSequenceNumber(sequenceNumber),
              mAskPrices(askPrices),
              mAskVolumes(askVolumes),
              mBidPrices(bidPrices),
              mBidVolumes(bidVolumes) {}

    std::size_t Size() const noexcept override
    {
        return MessageFieldSize::BYTE
               + MessageFieldSize::LONG
               + MessageFieldSize::LONG * TOP_LEVEL_COUNT * 4;
    }

    void Deserialise(unsigned char const* data, std::size_t size) override;
    void Serialise(unsigned char* buf) const override;

    Instrument mInstrument = Instrument::FUTURE;
    unsigned long mSequenceNumber = 0;
    std::array<unsigned long, TOP_LEVEL_COUNT> mAskPrices = {};
    std::array<unsigned long, TOP_LEVEL_COUNT> mAskVolumes = {};
    std::array<unsigned long, TOP_LEVEL_COUNT> mBidPrices = {};
    std::array<unsigned long, TOP_LEVEL_COUNT> mBidVolumes = {};
};

template<class T>
T makeMessage(unsigned char const* data, std::size_t size)
{
    T message;
    message.Deserialise(data, size);
    return message;
}

}

#endif //CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_PROTOCOL_H
