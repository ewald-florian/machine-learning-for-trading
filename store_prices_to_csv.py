import pandas as pd


def store_prices_to_csv(ticker='AAPL',
                       path = '../data/assets.h5')
    """
    Read the prices from hdf file into a dataframe and 
    store them to a csv file.
    Params:
    -------
    ticker: str
        Ticker of the respective asset.
    path: str
        Path to respective hdf file.
    """

    idx = pd.IndexSlice
    
    with pd.HDFStore(path) as store:
                df = (store['quandl/wiki/prices']
                      .loc[idx[:, ticker],
                           ['adj_close', 'adj_volume', 'adj_low', 'adj_high']]
                      .dropna()
                      .sort_index())

    filename = str(ticker) + "_prices.csv"

    # store to csv
    df.to_csv(filename)