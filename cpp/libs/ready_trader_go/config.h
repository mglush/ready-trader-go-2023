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
#ifndef CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_CONFIG_H
#define CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_CONFIG_H

#include <string>

#include <boost/property_tree/ptree.hpp>

namespace ReadyTraderGo {

struct Config
{
    void readFromPropertyTree(const boost::property_tree::ptree& tree)
    {
        mExecHost = tree.get<std::string>("Execution.Host");
        mExecPort = tree.get<unsigned short>("Execution.Port");

        mInfoType = tree.get<std::string>("Information.Type");
        mInfoName = tree.get<std::string>("Information.Name");

        mTeamName = tree.get<std::string>("TeamName");
        mSecret = tree.get<std::string>("Secret");
    }

    std::string mExecHost;
    unsigned short mExecPort;

    std::string mInfoType;
    std::string mInfoName;

    std::string mTeamName;
    std::string mSecret;
};

}

#endif //CPPREADY_TRADER_GO_LIBS_READY_TRADER_GO_CONFIG_H
