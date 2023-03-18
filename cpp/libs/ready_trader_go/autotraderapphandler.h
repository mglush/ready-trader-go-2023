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
#ifndef CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_AUTOTRADERAPPHANDLER_H
#define CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_AUTOTRADERAPPHANDLER_H

#include <memory>

#include <boost/asio/io_context.hpp>

#include "application.h"
#include "baseautotrader.h"
#include "connectivity.h"

namespace ReadyTraderGo {

class AutoTraderAppHandler
{
public:
    explicit AutoTraderAppHandler(Application& application, BaseAutoTrader& autoTrader)
        : mApplication(application), mAutoTrader(autoTrader), mContext(mApplication.GetContext())
    {
        mApplication.ConfigLoaded = [this](auto& tree) { ConfigLoadedHandler(tree); };
        mApplication.ReadyToRun = [this] { ReadyToRunHandler(); };
    }

private:
    void ConfigLoadedHandler(const boost::property_tree::ptree&);
    void ReadyToRunHandler();

    Application& mApplication;
    BaseAutoTrader& mAutoTrader;
    boost::asio::io_context& mContext;

    std::unique_ptr<ConnectionFactory> mExecConnectionFactory;
    std::unique_ptr<SubscriptionFactory> mInfoSubscriptionFactory;
};

}

#endif //CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_AUTOTRADERAPPHANDLER_H
