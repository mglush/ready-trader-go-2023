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
#include <memory>

#include <boost/property_tree/ptree.hpp>

#include "autotraderapphandler.h"
#include "connectivity.h"
#include "config.h"
#include "error.h"

namespace ReadyTraderGo {

void AutoTraderAppHandler::ConfigLoadedHandler(const boost::property_tree::ptree& tree)
{
    Config config;
    config.readFromPropertyTree(tree);

    if (config.mTeamName.size() > MessageFieldSize::STRING)
        throw ReadyTraderGoError("configured team name is too long");

    if (config.mSecret.size() > MessageFieldSize::STRING)
        throw ReadyTraderGoError("configured secret is too long");

    mExecConnectionFactory = std::make_unique<ConnectionFactory>(mContext,
                                                                 config.mExecHost,
                                                                 config.mExecPort);
    mInfoSubscriptionFactory = std::make_unique<SubscriptionFactory>(mContext,
                                                                     config.mInfoType,
                                                                     config.mInfoName);

    mAutoTrader.SetLoginDetails(config.mTeamName, config.mSecret);
}

void AutoTraderAppHandler::ReadyToRunHandler()
{
    auto connection = mExecConnectionFactory->Create();
    mAutoTrader.SetExecutionConnection(std::move(connection));
    auto subscription = mInfoSubscriptionFactory->Create();
    mAutoTrader.SetInformationSubscription(std::move(subscription));
}

}
