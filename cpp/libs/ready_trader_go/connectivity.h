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
#ifndef CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_CONNECTIVITY_H
#define CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_CONNECTIVITY_H

#include <cstddef>
#include <memory>
#include <string>
#include <vector>

#include <boost/asio/io_context.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/streambuf.hpp>
#include <boost/interprocess/file_mapping.hpp>
#include <boost/interprocess/mapped_region.hpp>
#include <boost/system/error_code.hpp>

#include "connectivitytypes.h"

namespace interprocess = boost::interprocess;
using boost::asio::ip::tcp;

namespace ReadyTraderGo {

// Each message begins with a two-part header:
//   1. length - a two-byte, big endian, unsigned integer; and
//   2. type - a one-byte unsigned integer.
constexpr std::size_t MESSAGE_HEADER_SIZE = 3;
constexpr std::size_t MESSAGE_TYPE_OFFSET = 2;

// Each subscription transport frame begins with a two-part header:
//    1. spinlock - a four-byte little-endian flag (either 0 or 1); and
//    2. payload size - a four-byte, big endian, unsigned intteger.
constexpr std::size_t FRAME_PAYLOAD_SIZE_OFFSET = 4;
constexpr std::size_t FRAME_HEADER_SIZE = 8;
constexpr std::size_t FRAME_SIZE = 128;
constexpr std::size_t SUBSCRIPTION_TRANSPORT_BUFFER_SIZE = 8182;


class Connection : public IConnection
{
public:
    Connection(boost::asio::io_context& context, tcp::socket&& socket);
    ~Connection() override;
    void AsyncRead() override;
    void SendMessage(unsigned char messageType, const ISerialisable& serialisable, SendMode mode) override;

private:
    void Send();
    void Send(SendMode mode);

    void ReadSomeHandler(const boost::system::error_code& error, std::size_t size);
    void WriteSomeHandler(const boost::system::error_code& error, std::size_t size);

    boost::asio::io_context& mContext;
    boost::asio::streambuf mInBuffer;
    boost::asio::streambuf mOutBuffer;
    bool mIsSending = false;
    bool mIsSendPosted = false;
    tcp::socket mSocket;
};

class Subscription : public ISubscription
{
public:
    Subscription(boost::asio::io_context& context,
                 interprocess::file_mapping& file,
                 interprocess::mapped_region& region);
    ~Subscription() override;
    void AsyncReceive() override;

private:
    void AsyncReceive(unsigned long, std::weak_ptr<ISubscription>);
    void ReceiveFromHandler(unsigned char const*, std::size_t size);

    boost::asio::io_context& mContext;
    interprocess::file_mapping mFile;
    interprocess::mapped_region mRegion;
};

class ConnectionFactory : public IConnectionFactory
{
public:
    ConnectionFactory(boost::asio::io_context& context,
                      std::string host,
                      unsigned short port);

    std::unique_ptr<IConnection> Create() override;

private:
    boost::asio::io_context& mContext;
    std::vector<tcp::endpoint> mEndpoints;
    std::string mHost;
    unsigned short mPort;
};

class SubscriptionFactory : public ISubscriptionFactory
{
public:
    SubscriptionFactory(boost::asio::io_context& context,
                        const std::string& type,
                        const std::string& name);

    std::shared_ptr<ISubscription> Create() override;

private:
    boost::asio::io_context& mContext;
    std::string mType;
    std::string mName;
};

}

#endif //CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_CONNECTIVITY_H
