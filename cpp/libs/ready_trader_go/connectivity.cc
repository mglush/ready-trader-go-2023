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
#include <cstddef>
#include <iomanip>
#include <memory>
#include <string>
#include <vector>

#include <boost/asio/connect.hpp>
#include <boost/asio/io_context.hpp>
#include <boost/asio/error.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/post.hpp>
#include <boost/endian/conversion.hpp>
#include <boost/interprocess/file_mapping.hpp>
#include <boost/interprocess/mapped_region.hpp>
#include <boost/system/error_code.hpp>

#include "connectivity.h"
#include "error.h"
#include "logging.h"

namespace error = boost::asio::error;
namespace interprocess = boost::interprocess;
namespace ip = boost::asio::ip;
using boost::asio::ip::tcp;

RTG_INLINE_GLOBAL_LOGGER_WITH_CHANNEL(LG_CON, "CON")

namespace ReadyTraderGo {

// Theoretical maximum size of an (IPv4) UDP packet (actual maximum is lower).
constexpr std::size_t READ_SIZE = 65535;

Connection::Connection(boost::asio::io_context& context, tcp::socket&& socket)
    : mContext(context),
      mInBuffer(),
      mOutBuffer(),
      mSocket(std::move(socket))
{
    SetName('\'' + std::to_string(mSocket.local_endpoint().port()) + '\'');
}

Connection::~Connection()
{
    RLOG(LG_CON, LogLevel::LL_INFO) << std::quoted(mName, '\'') << " closing";
    if (mSocket.is_open())
    {
        mSocket.close();
    }
}

void Connection::AsyncRead()
{
    auto buf = mInBuffer.prepare(READ_SIZE);
    mSocket.async_read_some(
        buf,
        [this](auto& error, auto size) { ReadSomeHandler(error, size); });
}

void Connection::ReadSomeHandler(const boost::system::error_code& error, std::size_t size)
{
    if (error)
    {
        if (error == error::eof)
        {
            RLOG(LG_CON, LogLevel::LL_INFO) << std::quoted(mName, '\'') << " remote disconnect";
        }
        else if (error == error::interrupted || error == error::try_again || error == error::would_block)
        {
            RLOG(LG_CON, LogLevel::LL_DEBUG) << std::quoted(mName, '\'') << " read interrupted: "
                                             << error.message();
            AsyncRead();
            return;
        }
        else
        {
            RLOG(LG_CON, LogLevel::LL_ERROR) << std::quoted(mName, '\'') << " read error: "
                                             << error.message();
        }
        OnDisconnect();
        return;
    }

    RLOG(LG_CON, LogLevel::LL_DEBUG) << std::quoted(mName, '\'') << " received " << size
                                     << " bytes";
    mInBuffer.commit(size);

    auto* const begin = (unsigned char const*) mInBuffer.data().data();
    auto* upto = begin;
    auto available = size;

    while (available >= MESSAGE_HEADER_SIZE)
    {
        const std::size_t messageLength = boost::endian::big_to_native(*(uint16_t*)upto);
        if (available < messageLength)
            break;

        const unsigned char messageType = upto[MESSAGE_TYPE_OFFSET];
        RLOG(LG_CON, LogLevel::LL_DEBUG) << std::quoted(mName, '\'')
                                         << " received message with type=" << static_cast<int>(messageType)
                                         << " and size=" << messageLength;
        OnMessageReceipt(messageType, upto + MESSAGE_HEADER_SIZE, messageLength - MESSAGE_HEADER_SIZE);

        upto += messageLength;
        available -= messageLength;
    }

    mInBuffer.consume(upto - begin);
    AsyncRead();
}

void Connection::Send()
{
    mIsSending = true;
    mSocket.async_write_some(mOutBuffer.data(),
                             [this](auto& err, auto sz) { WriteSomeHandler(err, sz); });
}

void Connection::Send(SendMode mode)
{
    if (mode == SendMode::ASAP)
    {
        Send();
    }
    else if (!mIsSendPosted)
    {
        boost::asio::post(mContext, [this] {
            mIsSendPosted = false;
            if (!mIsSending)
            {
                Send();
            }
        });
        mIsSendPosted = true;
    }
}

void Connection::SendMessage(unsigned char messageType, const ISerialisable& serialisable, SendMode mode)
{
    const std::size_t size = MESSAGE_HEADER_SIZE + serialisable.Size();
    auto buf = mOutBuffer.prepare(size);
    auto* data = static_cast<unsigned char*>(buf.data());
    *(uint16_t*)data = boost::endian::native_to_big((uint16_t)size);
    data[MESSAGE_TYPE_OFFSET] = messageType;
    serialisable.Serialise(data + MESSAGE_HEADER_SIZE);
    mOutBuffer.commit(size);
    if (!mIsSending)
    {
        Send(mode);
    }
}

void Connection::WriteSomeHandler(const boost::system::error_code& error, std::size_t size)
{
    if (error)
    {
        if (error != error::interrupted && error != error::would_block && error != error::try_again)
        {
            RLOG(LG_CON, LogLevel::LL_ERROR) << std::quoted(mName, '\'') << " send failed: "
                                             << error.message();
            throw ReadyTraderGoError("send failed: " + error.message());
        }
        RLOG(LG_CON, LogLevel::LL_DEBUG) << std::quoted(mName, '\'') << " send interrupted: "
                                         << error.message();
    }
    else
    {
        RLOG(LG_CON, LogLevel::LL_DEBUG) << std::quoted(mName, '\'') << " sent "
                                         << size << " bytes";
        mOutBuffer.consume(size);
    }

    if (mOutBuffer.size() > 0)
    {
        mSocket.async_write_some(
            mOutBuffer.data(), [this](auto& err, auto sz) { WriteSomeHandler(err, sz); });
    }
    else
    {
        mIsSending = false;
    }
}

Subscription::Subscription(boost::asio::io_context& context, interprocess::file_mapping& file, interprocess::mapped_region& region)
    : mContext(context), mFile(std::move(file)), mRegion(std::move(region))
{
    SetName(std::string(mFile.get_name()));
}

Subscription::~Subscription()
{
    RLOG(LG_CON, LogLevel::LL_INFO) << std::quoted(mName, '\'') << " closing";
}

void Subscription::AsyncReceive()
{
    std::weak_ptr<ISubscription> weak_this = shared_from_this();
    mContext.post([this, weak_this](){ AsyncReceive(0, weak_this); });
}

void Subscription::AsyncReceive(unsigned long pos, std::weak_ptr<ISubscription> weak_this)
{
    if (weak_this.expired())
    {
        // The 'this' object has been deleted out from underneath us!
        return;
    }

    unsigned char* addr = ((unsigned char*)mRegion.get_address()) + pos;

    if (addr[0] != 0)
    {
        const uint32_t* payload_size_ptr = (uint32_t*)(addr + FRAME_PAYLOAD_SIZE_OFFSET);
        const std::size_t payloadSize = boost::endian::big_to_native(*payload_size_ptr);
        ReceiveFromHandler(addr + FRAME_HEADER_SIZE, payloadSize);
        pos = (pos + FRAME_SIZE) & (SUBSCRIPTION_TRANSPORT_BUFFER_SIZE - 1);
    }

    mContext.post([this, pos, weak_this](){ AsyncReceive(pos, weak_this); });
}

void Subscription::ReceiveFromHandler(unsigned char const* data, std::size_t size)
{
    RLOG(LG_CON, LogLevel::LL_DEBUG) << std::quoted(mName, '\'') << " received "
                                     << size << " bytes";

    const std::size_t messageLength = boost::endian::big_to_native(*(uint16_t*)data);
    const unsigned char messageType = data[MESSAGE_TYPE_OFFSET];

    if (size != messageLength)
    {
        RLOG(LG_CON, LogLevel::LL_ERROR) << std::quoted(mName, '\'')
                                         << " malformed message with type=" << static_cast<int>(messageType)
                                         << " and size=" << messageLength;
        return;
    }

    RLOG(LG_CON, LogLevel::LL_DEBUG) << std::quoted(mName, '\'')
                                     << " received message with type=" << static_cast<int>(messageType)
                                     << " and size=" << messageLength;
    OnMessageReceipt(messageType, data + MESSAGE_HEADER_SIZE, messageLength - MESSAGE_HEADER_SIZE);
}

ConnectionFactory::ConnectionFactory(boost::asio::io_context& context,
                                     std::string host,
                                     unsigned short port)
    : mContext(context), mHost(std::move(host)), mPort(port)
{
    boost::system::error_code error;
    tcp::resolver resolver(mContext);
    auto endpoints = resolver.resolve(mHost, std::to_string(mPort), error);

    if (error)
    {
        throw ReadyTraderGoError("failed to resolve '" + mHost + "': " + error.message());
    }

    for (auto& ep : endpoints)
    {
        mEndpoints.emplace_back(ep);
    }
}

template<typename C, typename T>
static std::basic_ostream<C, T>& operator<<(std::basic_ostream<C, T>& strm, const tcp::endpoint& ep)
{
    strm << ep.address().to_string() << ":" << ep.port();
    return strm;
}

std::unique_ptr<IConnection> ConnectionFactory::Create()
{
    boost::system::error_code error;
    tcp::socket sock(mContext);

    RLOG(LG_CON, LogLevel::LL_INFO) << "connecting to: " << mEndpoints[0];
    boost::asio::connect(sock, mEndpoints, error);

    if (error)
    {
        RLOG(LG_CON, LogLevel::LL_ERROR) << "connect failed: " << error.message();
        throw ReadyTraderGoError("connect to '" + mHost + ":" + std::to_string(mPort)
                                      + "' failed: " + error.message());
    }

    RLOG(LG_CON, LogLevel::LL_INFO) << "connected successfully to: " << sock.remote_endpoint();
    sock.non_blocking(true);

    // It's not the end of the world if this fails, so any error is ignored.
    sock.set_option(tcp::no_delay(true), error);

    return std::make_unique<Connection>(mContext, std::move(sock));
}

SubscriptionFactory::SubscriptionFactory(boost::asio::io_context& context,
                                         const std::string& type,
                                         const std::string& name)
    : mContext(context), mType(type), mName(name)
{
}

std::shared_ptr<ISubscription> SubscriptionFactory::Create()
{
    interprocess::file_mapping file{mName.c_str(), interprocess::read_only};
    interprocess::mapped_region region{file, interprocess::read_only};
    return std::make_shared<Subscription>(mContext, file, region);
}

}