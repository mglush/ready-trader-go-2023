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
#include <cstring>
#include <string>

#include <boost/endian/conversion.hpp>

#include "protocol.h"

namespace ReadyTraderGo {

static std::string readFixedLengthString(unsigned char const* data, std::size_t maxSize)
{
    auto loc = (decltype(data)) std::memchr(data, 0, maxSize);
    auto len = (loc != nullptr) ? loc - data : maxSize;
    return std::string((char const*) data, len);
}

static void writeFixedLengthString(const std::string& message, unsigned char* buf, std::size_t maxSize)
{
    auto len = message.length();
    std::memcpy(buf, message.c_str(), len);
    if (len < maxSize)
    {
        std::memset(buf + len, 0, maxSize - len);
    }
}

void AmendMessage::Deserialise(unsigned char const* data, std::size_t)
{
    mClientOrderId = boost::endian::big_to_native(*(uint32_t*)data);
    data += MessageFieldSize::LONG;
    mNewVolume = boost::endian::big_to_native(*(uint32_t*)data);
}

void AmendMessage::Serialise(unsigned char* buf) const
{
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mClientOrderId);
    buf += MessageFieldSize::LONG;
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mNewVolume);
}

void CancelMessage::Deserialise(unsigned char const* data, std::size_t)
{
    mClientOrderId = boost::endian::big_to_native(*(uint32_t*)data);
}

void CancelMessage::Serialise(unsigned char* buf) const
{
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mClientOrderId);
}

void ErrorMessage::Deserialise(unsigned char const* data, std::size_t)
{
    mClientOrderId = boost::endian::big_to_native(*(uint32_t*)data);
    data += MessageFieldSize::LONG;
    mMessage = readFixedLengthString(data, MessageFieldSize::STRING);
}

void ErrorMessage::Serialise(unsigned char* buf) const
{
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mClientOrderId);
    buf += MessageFieldSize::LONG;
    writeFixedLengthString(mMessage, buf, MessageFieldSize::STRING);
}

void HedgeMessage::Deserialise(unsigned char const* data, std::size_t)
{
    mClientOrderId = boost::endian::big_to_native(*(uint32_t*)data);
    data += MessageFieldSize::LONG;
    mSide = Side(*data);
    data += MessageFieldSize::BYTE;
    mPrice = boost::endian::big_to_native(*(uint32_t*)data);
    data += MessageFieldSize::LONG;
    mVolume = boost::endian::big_to_native(*(uint32_t*)data);
}

void HedgeMessage::Serialise(unsigned char* buf) const
{
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mClientOrderId);
    buf += MessageFieldSize::LONG;
    *buf = static_cast<unsigned char>(mSide);
    buf += MessageFieldSize::BYTE;
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mPrice);
    buf += MessageFieldSize::LONG;
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mVolume);
}

void HedgeFilledMessage::Deserialise(unsigned char const* data, std::size_t)
{
    mClientOrderId = boost::endian::big_to_native(*(uint32_t*)data);
    data += MessageFieldSize::LONG;
    mPrice = boost::endian::big_to_native(*(uint32_t*)data);
    data += MessageFieldSize::LONG;
    mVolume = boost::endian::big_to_native(*(uint32_t*)data);
}

void HedgeFilledMessage::Serialise(unsigned char* buf) const
{
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mClientOrderId);
    buf += MessageFieldSize::LONG;
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mPrice);
    buf += MessageFieldSize::LONG;
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mVolume);
}

void InsertMessage::Deserialise(unsigned char const* data, std::size_t)
{
    mClientOrderId = boost::endian::big_to_native(*(uint32_t*)data);
    data += MessageFieldSize::LONG;
    mSide = Side(*data);
    data += MessageFieldSize::BYTE;
    mPrice = boost::endian::big_to_native(*(uint32_t*)data);
    data += MessageFieldSize::LONG;
    mVolume = boost::endian::big_to_native(*(uint32_t*)data);
    data += MessageFieldSize::LONG;
    mLifespan = Lifespan(*data);
}

void InsertMessage::Serialise(unsigned char* buf) const
{
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mClientOrderId);
    buf += MessageFieldSize::LONG;
    *buf = static_cast<unsigned char>(mSide);
    buf += MessageFieldSize::BYTE;
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mPrice);
    buf += MessageFieldSize::LONG;
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mVolume);
    buf += MessageFieldSize::LONG;
    *buf = static_cast<unsigned char>(mLifespan);
}

void LoginMessage::Deserialise(unsigned char const* data, std::size_t)
{
    mName = readFixedLengthString(data, MessageFieldSize::STRING);
    data += MessageFieldSize::STRING;
    mSecret = readFixedLengthString(data, MessageFieldSize::STRING);
}

void LoginMessage::Serialise(unsigned char* buf) const
{
    writeFixedLengthString(mName, buf, MessageFieldSize::STRING);
    buf += MessageFieldSize::STRING;
    writeFixedLengthString(mSecret, buf, MessageFieldSize::STRING);
}

void OrderBookMessage::Deserialise(unsigned char const* data, std::size_t)
{
    mInstrument = Instrument(*data);
    data += MessageFieldSize::BYTE;
    mSequenceNumber = boost::endian::big_to_native(*(uint32_t*)data);
    data += MessageFieldSize::LONG;

    for (auto& p : mAskPrices)
    {
        p = boost::endian::big_to_native(*(uint32_t*)data);
        data += MessageFieldSize::LONG;
    }
    for (auto& v : mAskVolumes)
    {
        v = boost::endian::big_to_native(*(uint32_t*)data);
        data += MessageFieldSize::LONG;
    }
    for (auto& p : mBidPrices)
    {
        p = boost::endian::big_to_native(*(uint32_t*)data);
        data += MessageFieldSize::LONG;
    }
    for (auto& v : mBidVolumes)
    {
        v = boost::endian::big_to_native(*(uint32_t*)data);
        data += MessageFieldSize::LONG;
    }
}

void OrderBookMessage::Serialise(unsigned char* buf) const
{
    *buf = static_cast<unsigned char>(mInstrument);
    buf += MessageFieldSize::BYTE;
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mSequenceNumber);
    buf += MessageFieldSize::LONG;

    for (auto p : mAskPrices)
    {
        *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)p);
        buf += MessageFieldSize::LONG;
    }
    for (auto v : mAskVolumes)
    {
        *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)v);
        buf += MessageFieldSize::LONG;
    }
    for (auto p : mBidPrices)
    {
        *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)p);
        buf += MessageFieldSize::LONG;
    }
    for (auto v : mBidVolumes)
    {
        *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)v);
        buf += MessageFieldSize::LONG;
    }
}

void OrderFilledMessage::Deserialise(unsigned char const* data, std::size_t)
{
    mClientOrderId = boost::endian::big_to_native(*(uint32_t*)data);
    data += MessageFieldSize::LONG;
    mPrice = boost::endian::big_to_native(*(uint32_t*)data);
    data += MessageFieldSize::LONG;
    mVolume = boost::endian::big_to_native(*(uint32_t*)data);
}

void OrderFilledMessage::Serialise(unsigned char* buf) const
{
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mClientOrderId);
    buf += MessageFieldSize::LONG;
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mPrice);
    buf += MessageFieldSize::LONG;
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mVolume);
}

void OrderStatusMessage::Deserialise(unsigned char const* data, std::size_t)
{
    mClientOrderId = boost::endian::big_to_native(*(uint32_t*)data);
    data += MessageFieldSize::LONG;
    mFillVolume = boost::endian::big_to_native(*(uint32_t*)data);
    data += MessageFieldSize::LONG;
    mRemainingVolume = boost::endian::big_to_native(*(uint32_t*)data);
    data += MessageFieldSize::LONG;
    mFees = boost::endian::big_to_native(*(int32_t*)data);
}

void OrderStatusMessage::Serialise(unsigned char* buf) const
{
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mClientOrderId);
    buf += MessageFieldSize::LONG;
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mFillVolume);
    buf += MessageFieldSize::LONG;
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mRemainingVolume);
    buf += MessageFieldSize::LONG;
    *(int32_t*)buf = boost::endian::native_to_big((int32_t)mFees);
}

void TradeTicksMessage::Deserialise(unsigned char const* data, std::size_t)
{
    mInstrument = Instrument(*data);
    data += MessageFieldSize::BYTE;
    mSequenceNumber = boost::endian::big_to_native(*(uint32_t*)data);
    data += MessageFieldSize::LONG;

    for (auto& p : mAskPrices)
    {
        p = boost::endian::big_to_native(*(uint32_t*)data);
        data += MessageFieldSize::LONG;
    }
    for (auto& v : mAskVolumes)
    {
        v = boost::endian::big_to_native(*(uint32_t*)data);
        data += MessageFieldSize::LONG;
    }
    for (auto& p : mBidPrices)
    {
        p = boost::endian::big_to_native(*(uint32_t*)data);
        data += MessageFieldSize::LONG;
    }
    for (auto& v : mBidVolumes)
    {
        v = boost::endian::big_to_native(*(uint32_t*)data);
        data += MessageFieldSize::LONG;
    }
}

void TradeTicksMessage::Serialise(unsigned char* buf) const
{
    *buf = static_cast<unsigned char>(mInstrument);
    buf += MessageFieldSize::BYTE;
    *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)mSequenceNumber);
    buf += MessageFieldSize::LONG;

    for (auto p : mAskPrices)
    {
        *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)p);
        buf += MessageFieldSize::LONG;
    }
    for (auto v : mAskVolumes)
    {
        *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)v);
        buf += MessageFieldSize::LONG;
    }
    for (auto p : mBidPrices)
    {
        *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)p);
        buf += MessageFieldSize::LONG;
    }
    for (auto v : mBidVolumes)
    {
        *(uint32_t*)buf = boost::endian::native_to_big((uint32_t)v);
        buf += MessageFieldSize::LONG;
    }
}

}
