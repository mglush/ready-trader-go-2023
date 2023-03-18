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
#ifndef CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_TYPES_H
#define CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_TYPES_H

#include <cstddef>
#include <ostream>
#include <stdexcept>

namespace ReadyTraderGo {

constexpr unsigned long MAXIMUM_ASK = 2147483647;
constexpr unsigned long MINIMUM_BID = 1;
constexpr std::size_t TOP_LEVEL_COUNT = 5;

enum class Instrument : unsigned char { FUTURE, ETF };
enum class Lifespan : unsigned char { FILL_AND_KILL, GOOD_FOR_DAY };
enum class Side : unsigned char { SELL, BUY };

template<typename C, typename T>
std::basic_ostream<C, T>& operator<<(std::basic_ostream<C, T>& strm, Instrument inst)
{
    strm << ((inst == Instrument::FUTURE) ? "Future" : "ETF");
    return strm;
}

template<typename C, typename T>
std::basic_ostream<C, T>& operator<<(std::basic_ostream<C, T>& strm, Lifespan span)
{
    strm << ((span == Lifespan::FILL_AND_KILL) ? "FAK" : "GFD");
    return strm;
}

template<typename C, typename T>
std::basic_ostream<C, T>& operator<<(std::basic_ostream<C, T>& strm, Side side)
{
    strm << ((side == Side::BUY) ? "Buy" : "Sell");
    return strm;
}

}

#endif //CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_TYPES_H
