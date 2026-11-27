import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wanstats import analysis, synthetic  # noqa: E402
from wanstats.analysis import Series  # noqa: E402

MIDNIGHT = 1_700_006_400      # 2023-11-15 00:00 UTC
END = MIDNIGHT + 400 * 86400


def series(name='Alpha', minutes=3000, start=MIDNIGHT, org='Org', uplink='wan1', level=10.0, serial=None,
           network='N_1', ip='8.8.8.8'):
    """A flat, lossless link with one reading a minute."""
    ts = start + 60 * np.arange(minutes, dtype=np.int64)
    return Series(org, network, serial or 'S-' + name, name, uplink, ip, ts, np.full(minutes, float(level)),
                  np.zeros(minutes))


def as_series(x):
    return Series(x['org'], x['network'], x['serial'], x['name'], x['uplink'], x['ip'], x['ts'], x['latency'],
                  x['loss'])


@pytest.fixture(scope='session')
def demo():
    """The invented fleet, analysed once for every test that wants a realistic data set."""
    return analysis.analyse([as_series(x) for x in synthetic.fleet(END, days=150)])
