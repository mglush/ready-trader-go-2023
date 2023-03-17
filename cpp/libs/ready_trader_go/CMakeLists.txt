set(sources
        application.cc
        application.h
        autotraderapphandler.cc
        autotraderapphandler.h
        baseautotrader.cc
        baseautotrader.h
        config.h
        connectivity.cc
        connectivity.h
        connectivitytypes.h
        error.h
        logging.h
        protocol.cc
        protocol.h
        types.h)

add_library(ready_trader_go_lib ${sources})
