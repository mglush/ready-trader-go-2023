# Ready Trader Go

## IT'S READY TRADER GO TIME

Welcome to the ultimate team student trading competition. Competitors get to
learn all the fundamentals of trading and truly test their coding and analytical
skills. The aim is to build and optimise a trading algorithm that outperforms
all others on a virtual exchange.

To learn more about the competition, visit [https://readytradergo.optiver.com]
(https://readytradergo.optiver.com).

## Getting started

To run Ready Trader Go, you'll need Python version 3.11 and PySide6. You
can download Python from [www.python.org](https://www.python.org).

Once you have installed Python, you'll need to create a Python virtual
environment, and you can find instructions for creating and using virtual
environments at
[docs.python.org/3/library/venv.html](https://docs.python.org/3/library/venv.html).

To use the Ready Trader Go graphical user interface, you'll need to install
the [PySide6 package](https://pypi.org/project/PySide6/) which you can do by
running

```shell
pip3 install PySide6
```

in your Python virtual environment.

To build an autotrader you'll need the [CMake](https://cmake.org) family
of tools version 3.17 or higher. Ready Trader Go requires the free
[Boost](https://www.boost.org) C++ libraries, version 1.74.0 or above. 

To compile an autotrader:

```shell
cmake -DCMAKE_BUILD_TYPE=Debug -B build
cmake --build build --config Debug
```

Replace "Debug" with "Release" in the above to build with CMake's
'Release' build configuration. For more information, see the
[CMake Tutorial](https://cmake.org/cmake/help/latest/guide/tutorial/index.html).

**Note:** Your autotrader will be built using the 'Release' build configuration
for the competition.

### Running a Ready Trader Go match

Before you can run an autotrader there must be a corresponding JSON configuration
file in the same directory as your autotrader executable. CMake will place
the executable in a directory called 'build' so you'll need to copy it from
there to your ready-trader-go folder. On Windows, you can use:

```cmd
copy build\autotrader.exe .
```

On macOS and Linux, you can use:

```shell
cp build/autotrader .
```

To run a Ready Trader Go match with one or more autotraders, simply run:

```shell
python3 rtg.py run [AUTOTRADER FILENAME [AUTOTRADER FILENAME]]
```

For example:

```shell
python3 rtg.py run autotrader
```

## What's in this archive?

This archive contains everything needed to run a Ready Trader Go *match*
in which multiple autotraders compete against each other in a simulated
market. For the exact definition of a match, see the competition terms and
conditions.

The archive contains:

* autotrader.cc - implement your autotrader by modifying this file
* autotrader.h - implement your autotrader by modifying this file
* autotrader.json - configuration file for an autotrader
* CMakeLists.txt - configuration file for the CMake family of tools
* libs - contains the Ready Trader Go source code (don't modify this)
* main.cc - contains the *main* function for an autotrader (don't modify this)

### Autotrader configuration

Each autotrader is configured with a JSON file like this:

```json
{
  "Execution": {
    "Host": "127.0.0.1",
    "Port": 12345
  },
  "Information": {
    "Type": "mmap",
    "Name": "info.dat"
  },
  "TeamName": "TraderOne",
  "Secret": "secret"
}
```

The elements of the autotrader configuration are:

* Execution - network address for sending execution requests (e.g. to place
an order)
* Information - details of a memory-mapped file used for information messages
broadcast by the exchange simulator
* TeamName - name of the team for this autotrader (each autotrader in a match
  must have a unique team name)
* Secret - password for this autotrader

### Simulator configuration

The market simulator is configured with a JSON file called "exchange.json".
Here is an example:

```json
{
  "Engine": {
    "MarketDataFile": "data/market_data.csv",
    "MarketEventInterval": 0.05,
    "MarketOpenDelay": 5.0,
    "MatchEventsFile": "match_events.csv",
    "ScoreBoardFile": "score_board.csv",
    "Speed": 1.0,
    "TickInterval": 0.25
  },
  "Execution": {
    "host": "127.0.0.1",
    "Port": 12345
  },
  "Fees": {
    "Maker": -0.0001,
    "Taker": 0.0002
  },
  "Information": {
    "Type": "mmap",
    "Name": "info.dat"
  },
  "Instrument": {
    "EtfClamp": 0.002,
    "TickSize": 1.00
  },
  "Limits": {
    "ActiveOrderCountLimit": 10,
    "ActiveVolumeLimit": 200,
    "MessageFrequencyInterval": 1.0,
    "MessageFrequencyLimit": 50,
    "PositionLimit": 100
  },
  "Traders": {
    "TraderOne": "secret",
    "ExampleOne": "qwerty",
    "ExampleTwo": "12345"
  }
}
```

The elements of the autotrader configuration are:

* Engine - source data file, output filename, simulation speed and tick interval
* Execution - network address to listen for autotrader connections
* Fees - details of the fee structure
* Information - details of a memory-mapped file use to broadcast information
messages to autotraders
* Instrument - details of the instrument to be traded
* Limits - details of the limits by which autotraders must abide
* Traders - team names and secrets of the autotraders

**Important:** Each autotrader must have a unique team name and password
listed in the 'Traders' section of the `exchange.json` file.

## The Ready Trader Go command line utility

The Ready Trader Go command line utility, `rtg.py`, can be used to run or
replay a match. For help, run:

```shell
python3 rtg.py --help
```

### Running a match

To run a match, use the "run" command and specify the autotraders you
wish to participate in the match:

```shell
python3 rtg.py run [AUTOTRADER FILENAME [AUTOTRADER FILENAME]]
```

Each autotrader must have a corresponding JSON file (with the same filename,
but ending in ".json" instead of ".py") which contains a unique team name
and the team name and secret must be listed in the `exchange.json` file.

It will take approximately 60 minutes for the match to complete and several
files will be produced:

* `autotrader.log` - log file for an autotrader
* `exchange.log` - log file for the simulator
* `match_events.csv` - a record of events during the match
* `score_board.csv` - a record of each autotrader's score over time

To aid testing, you can speed up the match by modifying the "Speed" setting
in the "exchange.json" configuration file - for example, setting the speed
to 2.0 will halve the time it takes to run a match. Note, however, that
increasing the speed may change the results.

When testing your autotrader, you should try it with different sample data
files by modifying the "MarketDataFile" setting in the "exchange.json"
file.

### Replaying a match

To replay a match, use the "replay" command and specify the name of the
match events file you wish to replay:

```shell
python3 rtg.py replay match_events.csv
```

### Autotrader environment

Autotraders in Ready Trader Go will be run in the following environment:

* Operating system: Linux
* C++ Compiler: GCC version 10.2.1
* Available libraries: Boost 1.74.0 (the available components are listed in the
  CMakeLists.txt file)
* Memory limit: 2GB
* Total disk usage limit: 100MB (including the log file)
* Maximum number of autotraders per match: 8
* Autotraders may not create sub-processes but may have multiple threads
* Autotraders may not access the internet

## How do I submit my AutoTrader?

Shortly after the competition begins you'll be supplied with the details of
a [GIT repository](https://git-scm.com) which you can use to submit your
autotrader. To access the GIT repository, you'll first need to 'clone' it.

For example:

```shell
git clone https://git-codecommit.eu-central-1.amazonaws.com/v1/repos/TEAM_NAME
```

(replace 'TEAM_NAME' with your team name.)

To submit your autotrader, you need to _commit_ your `autotrader.h` and
`autotrader.cc` files to the GIT repository and then _push_ that commit to
your Git repository. For example:

```shell
git add autotrader.h autotrader.cc
git commit -m "Updating my autotrader"
git push
```

Do _not_ put the `autotrader.h` and `autotrader.cc` files in a folder and do
_not_ include any other files (any other files will be ignored). You may only
submit one autotrader (i.e. you cannot submit both a Python and a C++
autotrader). 

You may replace your autotrader with a new one at any time. When each
tournament starts we'll use the autotrader in your GIT repository at the
cut-off time for that tournament.
