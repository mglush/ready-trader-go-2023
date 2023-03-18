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
#ifndef CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_LOGGING_H
#define CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_LOGGING_H

#include <ostream>

#include <boost/log/keywords/channel.hpp>
#include <boost/log/sources/global_logger_storage.hpp>
#include <boost/log/sources/record_ostream.hpp>
#include <boost/log/sources/severity_channel_logger.hpp>

namespace ReadyTraderGo {

enum class LogLevel : unsigned char
{
    LL_DEBUG,
    LL_INFO,
    LL_WARNING,
    LL_ERROR,
    LL_FATAL
};

constexpr const char* LOG_LEVEL_NAMES[] = {
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "FATAL"
};

template<typename C, typename T>
std::basic_ostream<C, T>& operator<<(std::basic_ostream<C, T>& strm, LogLevel lvl)
{
    auto levelNumber = static_cast<int>(lvl);
    strm << LOG_LEVEL_NAMES[levelNumber];
    return strm;
}

#define RTG_INLINE_GLOBAL_LOGGER_WITH_CHANNEL(loggerName, channelName)\
    BOOST_LOG_INLINE_GLOBAL_LOGGER_CTOR_ARGS(loggerName,\
        boost::log::sources::severity_channel_logger<ReadyTraderGo::LogLevel>,\
        (boost::log::keywords::channel = (channelName)));

#define RLOG(loggerName, logLevel) BOOST_LOG_SEV(loggerName::get(), (logLevel))
}

#endif //CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_LOGGING_H
