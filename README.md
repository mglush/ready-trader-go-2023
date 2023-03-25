# Optiver's 2023 Ready Trader Go Competition
#### Submission by Michael Glushchenko and Vasyliy Ostapenko as Team LiquidBears.

## Table of Contents
* [Reasons to Participate](https://github.com/mglush/ready-trader-go-2023/blob/main/README.md#reasons-to-participate).
* [Rules of the Game](https://github.com/mglush/ready-trader-go-2023/blob/main/README.md#rules-of-the-game).
* [Our Strategy](https://github.com/mglush/ready-trader-go-2023/blob/main/README.md#our-strategy).
* [Results](https://github.com/mglush/ready-trader-go-2023/blob/main/README.md#results).
* [Logical Faults of the Plan](https://github.com/mglush/ready-trader-go-2023/blob/main/README.md#logical-faults-of-the-plan).
* [Bugs](https://github.com/mglush/ready-trader-go-2023/blob/main/README.md#bugs).
* [Next Steps](https://github.com/mglush/ready-trader-go-2023/blob/main/README.md#next-steps).
* [Technologies](https://github.com/mglush/ready-trader-go-2023/blob/main/README.md#technologies).

## Reasons to Participate
Financial math and algorithmic trading are a big passion of mine, but I've never coded an autotrader before. This competition allowed me to understand how an autotrader might be implemented, as well as to test, first-hand, my understanding of market making and liquidity providing.

## Rules of the Game
The rules of the game can be found on the Optiver [website](https://readytradergo.optiver.com/how-to-play/).

## How to run
1) [Python how to run](https://github.com/mglush/ready-trader-go-2023/tree/main/py#ready-trader-go).
2) [C++ how to run](https://github.com/mglush/ready-trader-go-2023/tree/main/cpp#ready-trader-go).

## Our Strategy
Coming into this competition, Vasyl and I had no market making experience. After trying many different approaches, and rewriting the code numerous times, here's what we settled on:
  1) We use the midpoint of the current orderbook as the "current theoretical price." Although using a weighted midpoint made sense at first, the large skew present in volumes of orders made it difficult to use appropriately. 
  2) We use the last 2 price changes, average out, to determine our spread (distance from the midpoint). We used a single price change at first, and realized that was not a clever thing to do. We then tried to use 3-5 of the last price changes, but the bot did not react fast enough to volatile markets. Thus, we settled on using the last 2 price changes.
  3) Using the current orderbook volume, we calculated a volume imbalance, and adjusted our ask and bid, asymetrically, to be closer/further from the midpoint based on buying/selling pressure being higher/lower.
    3.1) We would adjust our reaction to this volume imbalance based on, relatively, "how much" volume we just saw in the ticks function, and how that volume influenced the price.
    3.2) We would slow our bot down when price changed greatly on small volume -- the idea being, that price must eventually be supported by greater volume, or it will revert to where it came from. We would slow our activity down to trading the spread until one of the above happened.
    3.3) We would spped our bot up (move the bid ask spread closer towards the middle) when the order execution rate (rate with which the ticks of the market function would get called) was high, and vice versa when it was low. The idea for this was that we cannot afford to try to get a bigger spread on an instrument that trades very often, so we had to adjust accordingly.

## Results
Vasyl and I ended up making it to the 5th round out of 9, earning us a top 128/1050 spot in the tournament. Results by round are as follows:  
#### Round 1: 2/4  
![Round 1 results](https://github.com/mglush/ready-trader-go-2023/blob/main/py/analysis/result_plots/match_round1.png)  
#### Round 2: 1/6  
![Round 2 results](https://github.com/mglush/ready-trader-go-2023/blob/main/py/analysis/result_plots/match_round2.png)  
#### Round 3: 3/8  
![Round 3 results](https://github.com/mglush/ready-trader-go-2023/blob/main/py/analysis/result_plots/match_round3.png)  
#### Round 4: 4/8  
![Round 4 results](https://github.com/mglush/ready-trader-go-2023/blob/main/py/analysis/result_plots/match_round4.png)  
#### Round 5: 8/8  
![Round 5 results](https://github.com/mglush/ready-trader-go-2023/blob/main/py/analysis/result_plots/match_round5.png)  
#### Running the autotrader on the same chart with no other autotraders:  
![Tournament 2 Open Market](https://github.com/mglush/ready-trader-go-2023/blob/main/py/analysis/result_plots/result_open_market.png)  
  
More details about P/L can be found in the [tournaments](https://github.com/mglush/ready-trader-go-2023/tree/main/py/tournaments) folder. It contains our results from the test round, as well as tournament 2.  
Analysis regarding how other bots compared to ours can be found in the [analysis](https://github.com/mglush/ready-trader-go-2023/tree/main/py/analysis) folder; specifically, in model_analysis.ipynb.  

## Logical Faults of the Plan
  1) From the beginning, we tried to implement a strategy that involved placing multiple bid-ask spreads at different prices, and keeping track of each order with the help of a dictionary (or unordered map). The problem: it was incredibly slow to iterate though the orders map to cancel orders that were no longer "optimal". The solution: implementing a strategy that used only a single bid-ask spread at a time, due to the limited time we had.
  2) Given one of the rules stated that a team cannot be unhedged for longer than a minute, we put great focus into trying to always be hedged; however, the problem was that by hedging every order we made, the losses on the hedge outweighed any profits our bot made. The solution: to hedge at the last possible moment, unless there's a "profitable" opportunity to do so earlier.
  3) Initially, we made a big attempt to use volume as an indicator of market movement. While we were correct in trying to use volume as a "market conditions" indicator, we were wrong in trying to make it tell us which way the market will move. The solution: to use volume as an indicator of "price change is coming" rather than "price is moving in this direction" indicator.
  4) In general, we dove into the coding portion of the project too quickly. We should have spent an extra day or two in front of the whiteboard, perfecting the design before touching any of the code. You live, you learn.
  
## Bugs
The main bug that I did not catch in time was that our bot trades very well at first, but becomes idle for the second half of the match. Although the bug comes up in very specific market situations, it was sadly there at submission time. The trading logic tells the bot the not change its spread if prices aren't changing often, or if a price change happened on insignificant volume. The way I implemented it, however, I would not post a new spread during such conditions, rather than not changing my spread. This specific bugs seems to be the reason we did not make it through to later rounds, as you can see our P/L flattens out half way though each round, with a tiny fix letting us raise the Sharpe ratio of the model from 1.7 to 1.99.

## Next Steps 
I would very much like to improve the autotrader implementation once I have the time to do so. I am still curious about how certain optimizations help/hurt the bot's performance. Here's my plan for the future of this repository:
  1) On the open market, our autotrader makes $10-12k, on average, while staying within the pre-defined risk parameters given; however, when numerous other autotraders are introduced into the market, the performance of our bought greatly decreases. I would like to study the reasons for why that is, and implement a dynamically-changing strategy for the autotrader that trades differently based on how frequently an instrument is being traded.
  2) I did not get the chance to implement a "learning" portion for the algorithm. It currently uses pre-defined parameters set as global variables. I would like to write code to run different instances of the same bot against each other, tournament-style. I would then be able to choose the "best" parameter values with a decision tree algorithm. Automating testing of the models would also allow me to get multiple trials  done and average the results over many runs.
  3) I would like to make the proof of concept in Python, but implement a finished project in C++, and finish everything off by comparing how much latency matters when it comes to an autotrader like this.
