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
#include <csignal>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <string>

#define BOOST_BIND_GLOBAL_PLACEHOLDERS
#include <boost/log/attributes/clock.hpp>
#include <boost/log/core.hpp>
#include <boost/log/expressions.hpp>
#include <boost/log/sinks/text_ostream_backend.hpp>
#include <boost/log/support/date_time.hpp>
#include <boost/log/utility/setup/formatter_parser.hpp>
#include <boost/property_tree/ptree.hpp>
#include <boost/property_tree/json_parser.hpp>
#include <boost/shared_ptr.hpp>

#include "application.h"
#include "error.h"
#include "logging.h"

namespace logging = boost::log;
namespace sinks = boost::log::sinks;
namespace expr = boost::log::expressions;
namespace attrs = boost::log::attributes;

RTG_INLINE_GLOBAL_LOGGER_WITH_CHANNEL(LG_APP, "APP")

namespace ReadyTraderGo {

BOOST_LOG_ATTRIBUTE_KEYWORD(rtg_severity, "Severity", LogLevel)

// Return the stem of a given path, e.g. stem("/foo/bar.exe") returns "bar".
static inline std::string stem(const std::string& path)
{
    auto pos = path.find_last_of("\\/");
    std::string filename = (pos != std::string::npos) ? path.substr(pos + 1) : path;
    pos = filename.find_last_of('.');
    if (pos != std::string::npos && pos != 0)
    {
        return filename.substr(0, pos);
    }
    return filename;
}

Application::~Application()
{
    if (!mContext.stopped())
    {
        mContext.stop();
    }
    TearDownLogging();
}

void Application::LoadConfig(const std::string& filename)
{
    boost::property_tree::ptree tree;

    RLOG(LG_APP, LogLevel::LL_INFO) << "loading configuration from " << std::quoted(filename, '\'');

    try
    {
        boost::property_tree::read_json(filename, tree);
    }
    catch (boost::property_tree::json_parser_error& err)
    {
        RLOG(LG_APP, LogLevel::LL_ERROR) << "failed while reading configuration file " << std::quoted(filename, '\'')
                                         << ": " << err.message();
        throw ReadyTraderGoError("failed while reading configuration file: '" + filename + "': " + err.message());
    }

    OnConfigLoaded(tree);
}

void Application::Run(int argc, char* argv[])
{
    if (mName.empty() && argc > 0 && argv[0][0] != '\0')
    {
        mName = stem(argv[0]);
    }
    else if (mName.empty())
    {
        throw ReadyTraderGoError("application has no name");
    }

    SetUpLogging();
    RLOG(LG_APP, LogLevel::LL_INFO) << "application started";

    LoadConfig(mName + ".json");

    // Add signal handling (to handle Ctrl-C, for example)
    mSignals.add(SIGINT);
    mSignals.add(SIGTERM);
#ifdef SIGQUIT
    mSignals.add(SIGQUIT);
#endif
    mSignals.async_wait([this](const boost::system::error_code& ec, int s) { SignalHandler(ec, s); });

    OnReadyToRun();
    mContext.run();
}

void Application::SetUpLogging()
{
    std::string logFilename = mName + ".log";
    std::ofstream logStream{logFilename, std::ios_base::app};
    if (!logStream)
    {
        std::string message = "failed to open log file '" + logFilename + "': " + std::strerror(errno);
        throw ReadyTraderGoError(message);
    }

    boost::shared_ptr<boost::log::core> core = logging::core::get();
    core->add_global_attribute("TimeStamp", attrs::local_clock());

    auto backend = boost::make_shared<sinks::text_ostream_backend>();
    backend->add_stream(boost::make_shared<std::ofstream>(std::move(logStream)));
    mSink = boost::make_shared<sink_t>(backend);
    core->add_sink(mSink);

    mSink->set_formatter(
        expr::stream
            << expr::format_date_time<boost::posix_time::ptime>("TimeStamp", "%Y-%m-%d %H:%M:%S.%f")
            << " [" << std::left << std::setw(7) << std::setfill(' ') << rtg_severity << "] ["
            << expr::attr<std::string>("Channel") << "] " << expr::smessage
    );

#ifdef NDEBUG
    mSink->set_filter(rtg_severity > LogLevel::LL_DEBUG);
#endif
}

void Application::SignalHandler(const boost::system::error_code& error, int signal)
{
    if (!error)
    {
        RLOG(LG_APP, LogLevel::LL_INFO) << "application received signal " << signal << ", shutting down";
        mContext.stop();
        return;
    }

    if (error == boost::asio::error::operation_aborted)
    {
        RLOG(LG_APP, LogLevel::LL_INFO) << "signal handling cancelled: " << error.message();
    }
    else
    {
        RLOG(LG_APP, LogLevel::LL_ERROR) << "signal handling error: " << error.message();
    }
}

void Application::TearDownLogging()
{
    if (mSink)
    {
        logging::core::get()->remove_sink(mSink);
        mSink->stop();
        mSink->flush();
    }
}

}
