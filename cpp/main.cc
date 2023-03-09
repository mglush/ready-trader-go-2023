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
#include <cstdlib>
#include <iostream>

#include <ready_trader_go/application.h>
#include <ready_trader_go/autotraderapphandler.h>
#include <ready_trader_go/error.h>

#include "autotrader.h"

int main(int argc, char* argv[])
{
    try
    {
        ReadyTraderGo::Application app;
        AutoTrader trader{app.GetContext()};
        ReadyTraderGo::AutoTraderAppHandler appHandler{app, trader};
        app.Run(argc, argv);
    }
    catch (const ReadyTraderGo::ReadyTraderGoError& e)
    {
        std::cerr << e.what() << std::endl;
        return EXIT_FAILURE;
    }
    catch (...)
    {
        // Catch block added so the Application object gets destructed
        // and the log gets flushed.
        throw;
    }

    return EXIT_SUCCESS;
}
