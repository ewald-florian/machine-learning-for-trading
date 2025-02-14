"""
Explanation:
------------

This file is based on the original open ai gym compatible TradingEnvironment 
class and extends it to the Ray Rllib compatible RayTradingEnvironment class.

Rllib (RL Framework): https://docs.ray.io/en/latest/rllib/index.html

------------

Code is based on "Machine Learning for Algorithmic Trading, 2nd edition" by 
Stefan Jansen:

https://github.com/stefan-jansen/machine-learning-for-trading/blob/main/22_deep_
reinforcement_learning/trading_env.py

Copyright note and permission of original code:

    'The MIT License (MIT)
    Copyright (c) 2016 Tito Ingargiola
    Copyright (c) 2019 Stefan Jansen
    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:
    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.'
    
"""

import logging
import tempfile

import gym
import numpy as np
import pandas as pd
from gym import spaces
from gym.utils import seeding
from sklearn.preprocessing import scale
import talib

logging.basicConfig()
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
log.info('%s logger started.', __name__)


class DataSource:
    """
    Data source for TradingEnvironment

    Loads & preprocesses daily price & volume data
    Provides data for each new episode.
    Stocks with longest history:

    ticker  # obs
    KO      14155
    GE      14155
    BA      14155
    CAT     14155
    DIS     14155

    Data can be loaded either from a hdf file with a specific 
    ticker as key or from a csv file which already contains
    only data for s specific asset.
    
    Params:
    -------
    trading_days: int
        Number of trading days (equal to episode length).
    ticker: str
        Name of ticker used.
    normalized: bool:
        define if data should be normalized.
    get_data_from: str
        define if data should be imported from csv or hdf.
        
    """

    def __init__(self, trading_days=252, ticker='AAPL', normalize=True, 
                 data_path=None, get_data_from_csv=True):
        
        self.ticker = ticker
        self.trading_days = trading_days
        self.normalize = normalize

        # note: the original trading_env loads data only from hdf5
        if get_data_from_csv:
            if data_path: # if path is specified load from path
                self.filename = data_path
                self.data = self.load_data_from_csv()
          
        else:
            self.load_data()
            
        self.preprocess_data()
        self.min_values = self.data.min()
        self.max_values = self.data.max()
        self.step = 0
        self.offset = None
    
    # load data from hdf (for multi asset)
    def load_data(self):
        log.info('loading data for {}...'.format(self.ticker))
        idx = pd.IndexSlice
        #with pd.HDFStore('../data/assets.h5') as store:
        #todo: insert correct path
        with pd.HDFStore('assets.h5') as store:
            df = (store['quandl/wiki/prices']
                  .loc[idx[:, self.ticker],
                       ['adj_close', 'adj_volume', 'adj_low', 'adj_high']]
                  .dropna()
                  .sort_index())
        df.columns = ['close', 'volume', 'low', 'high']
        log.info('got data for {}...'.format(self.ticker))
        return df
    
    # load data from csv (for single asset)
    def load_data_from_csv(self):
            
        df = pd.read_csv(self.filename, parse_dates=["date"], index_col=["date", "ticker"])
        log.info('got data for {}...'.format(self.ticker))
        return df


    def preprocess_data(self):
        """calculate returns and percentiles, then removes missing values"""

        self.data['returns'] = self.data.close.pct_change()
        self.data['ret_2'] = self.data.close.pct_change(2)
        self.data['ret_5'] = self.data.close.pct_change(5)
        self.data['ret_10'] = self.data.close.pct_change(10)
        self.data['ret_21'] = self.data.close.pct_change(21)
        self.data['rsi'] = talib.STOCHRSI(self.data.close)[1]
        self.data['macd'] = talib.MACD(self.data.close)[1]
        self.data['atr'] = talib.ATR(self.data.high, self.data.low, self.data.close)

        slowk, slowd = talib.STOCH(self.data.high, self.data.low, self.data.close)
        self.data['stoch'] = slowd - slowk
        self.data['atr'] = talib.ATR(self.data.high, self.data.low, self.data.close)
        self.data['ultosc'] = talib.ULTOSC(self.data.high, self.data.low, self.data.close)
        self.data = (self.data.replace((np.inf, -np.inf), np.nan)
                     .drop(['high', 'low', 'close', 'volume'], axis=1)
                     .dropna())

        r = self.data.returns.copy()
        if self.normalize:
            self.data = pd.DataFrame(scale(self.data),
                                     columns=self.data.columns,
                                     index=self.data.index)
        features = self.data.columns.drop('returns')
        self.data['returns'] = r  # don't scale returns
        self.data = self.data.loc[:, ['returns'] + list(features)]
        #log.info(self.data.info()) #-> prints df info

    def reset(self):
        """Provides starting index for time series and resets step"""
        high = int(len(self.data.index)) - int(self.trading_days)
        self.offset = np.random.randint(low=0, high=high)
        self.step = 0

    def take_step(self):
        """Returns data for current trading day and done signal"""
        obs = self.data.iloc[self.offset + self.step].values
        self.step += 1
        done = self.step > self.trading_days
        return obs, done


class TradingSimulator:
    """ Implements core trading simulator for single-instrument univ """

    def __init__(self, steps, trading_cost_bps, time_cost_bps):
        # invariant for object life
        self.trading_cost_bps = trading_cost_bps
        self.time_cost_bps = time_cost_bps
        self.steps = steps

        # change every step
        self.step = 0
        self.actions = np.zeros(self.steps)
        self.navs = np.ones(self.steps)
        self.market_navs = np.ones(self.steps)
        self.strategy_returns = np.ones(self.steps)
        self.positions = np.zeros(self.steps)
        self.costs = np.zeros(self.steps)
        self.trades = np.zeros(self.steps)
        self.market_returns = np.zeros(self.steps)

    def reset(self):
        self.step = 0
        self.actions.fill(0)
        self.navs.fill(1)
        self.market_navs.fill(1)
        self.strategy_returns.fill(0)
        self.positions.fill(0)
        self.costs.fill(0)
        self.trades.fill(0)
        self.market_returns.fill(0)

    def take_step(self, action, market_return):
        """ Calculates NAVs, trading costs and reward
            based on an action and latest market return
            and returns the reward and a summary of the day's activity. """
        
        start_position = self.positions[max(0, self.step - 1)]
        start_nav = self.navs[max(0, self.step - 1)]
        start_market_nav = self.market_navs[max(0, self.step - 1)]
        self.market_returns[self.step] = market_return
        self.actions[self.step] = action

        end_position = action - 1  # short, neutral, long
        n_trades = end_position - start_position
        self.positions[self.step] = end_position
        self.trades[self.step] = n_trades

        # roughly value based since starting NAV = 1
        trade_costs = abs(n_trades) * self.trading_cost_bps
        time_cost = 0 if n_trades else self.time_cost_bps
        self.costs[self.step] = trade_costs + time_cost
        reward = start_position * market_return - self.costs[self.step]
        self.strategy_returns[self.step] = reward

        if self.step != 0:
            self.navs[self.step] = start_nav * (1 + self.strategy_returns[self.step])
            self.market_navs[self.step] = start_market_nav * (1 + self.market_returns[self.step])

        info = {'reward': reward,
                'nav'   : self.navs[self.step],
                'costs' : self.costs[self.step]}

        self.step += 1
        
        return reward, info

    def result(self):
        """returns current state as pd.DataFrame """
        return pd.DataFrame({'action'         : self.actions,  # current action
                             'nav'            : self.navs,  # starting Net Asset Value (NAV)
                             'market_nav'     : self.market_navs,
                             'market_return'  : self.market_returns,
                             'strategy_return': self.strategy_returns,
                             'position'       : self.positions,  # eod position
                             'cost'           : self.costs,  # eod costs
                             'trade'          : self.trades})  # eod trade)


class RayTradingEnvironment(gym.Env):
    """A simple trading environment for reinforcement learning.

    Trading Simulator can be used plug and play with the Rllib 
    reinforecemnt learning framework, e.g. with the PPOTrainer.
    
    Provides daily observations for a stock price series
    An episode is defined as a sequence of 252 trading days with random start
    Each day is a 'step' that allows the agent to choose one of three actions:
    - 0: SHORT
    - 1: HOLD
    - 2: LONG

    Trading has an optional cost (default: 10bps) of the change in position value.
    Going from short to long implies two trades.
    Not trading also incurs a default time cost of 1bps per step.

    An episode begins with a starting Net Asset Value (NAV) of 1 unit of cash.
    If the NAV drops to 0, the episode ends with a loss.
    If the NAV hits 2.0, the agent wins.

    The trading simulator tracks a buy-and-hold strategy as benchmark.
    
    Params:
    -------
    config: dict
        Configuration dict for the environment, part of the 
        config dict for Rllib.
        
        takes the following format:
        
        "config": {
            "trading_days": 252,
            "trading_cost_bps": 1e-3,
            "time_cost_bps": 1e-4,
            "ticker": "AAPL",
            "get_data_from": "csv", # "csv" or "hdf"
        }
        
        Note: When using the Rllib config dict of the following form,
        the env config parameters will be automatically extracted:
        
        config = {
                "env": RayTradingEnvironment,
                "env_config": {
                    "config": {
                        "trading_days": 252,
                        "trading_cost_bps": 1e-3,
                        "time_cost_bps": 1e-4,
                        "ticker": "AAPL",
                        "get_data_from": "csv", # "csv" or "hdf"
                        },
                    },

                "create_env_on_driver": True,
            }
        
        Note: The RayTradingEnvironment class can still be used as a gym environment,
        similarly to the original TradingEnvironment, it just takes a dictionary as
        configuration input instead of seperate parameters.
    """
    metadata = {'render.modes': ['human']}

    def __init__(self, config=None):
        
        config = config or {}
        self.trading_days = config.get("trading_days", 252)
        self.trading_cost_bps = config.get("trading_cost_bps", 1e-3)
        self.time_cost_bps = config.get("time_cost_bps", 1e-4)
        self.ticker = config.get("ticker", "AAPL")
        self.get_data_from_csv = config.get("get_data_from_csv", True)
        self.data_path = config.get("data_path", '/home/jovyan/machine-learning-for-trading/AAPL_prices.csv')
        
        # the standard version of trading_env takes params directly as inputs (no dict used)
        #self.trading_days = trading_days
        #self.trading_cost_bps = trading_cost_bps
        #self.ticker = ticker
        #self.time_cost_bps = time_cost_bps
        
        self.data_source = DataSource(trading_days=self.trading_days,
                                          ticker=self.ticker,
                                          data_path=self.data_path,
                                          get_data_from_csv=self.get_data_from_csv)
        
        self.simulator = TradingSimulator(steps=self.trading_days,
                                          trading_cost_bps=self.trading_cost_bps,
                                          time_cost_bps=self.time_cost_bps)
        self.action_space = spaces.Discrete(3)
        self.observation_space = spaces.Box(self.data_source.min_values,
                                            self.data_source.max_values)
        self.reset()

    def seed(self, seed=None):
        self.np_random, seed = seeding.np_random(seed)
        return [seed]

    def step(self, action):
        """Returns state observation, reward, done and info"""
        assert self.action_space.contains(action), '{} {} invalid'.format(action, type(action))
        observation, done = self.data_source.take_step()
        reward, info = self.simulator.take_step(action=action,
                                                market_return=observation[0])
        return observation, reward, done, info

    def reset(self):
        """Resets DataSource and TradingSimulator; returns first observation"""
        self.data_source.reset()
        self.simulator.reset()
        return self.data_source.take_step()[0]

    # TODO
    def render(self, mode='human'):
        """Not implemented"""
        pass

