"""Made-up readings, for trying the reports without a Meraki network and for the tests.

`fleet()` invents a handful of links with the things the method looks for: one link whose
latency steps up and stays up, one that creeps, one that gets faster, a few outages, and a
stretch where the collector itself was down.
"""
from __future__ import annotations

import numpy as np

DAY = 86400


def link(org, name, uplink, start, days, level, rng, jitter=0.4, serial=None, ip='8.8.8.8'):
    """A steady link: one reading a minute around `level` ms, with a daily rhythm and a few slow minutes."""
    n = days * 1440
    ts = start + 60 * np.arange(n, dtype=np.int64) + int(rng.integers(0, 50))
    daily = 0.03 * level * np.sin(2 * np.pi * (ts % DAY) / DAY)
    latency = level + daily + rng.normal(0, jitter, n)
    slow = rng.random(n) < 0.004
    latency[slow] += rng.exponential(60, int(slow.sum()))
    loss = np.zeros(n)
    lossy = rng.random(n) < 0.015
    loss[lossy] = rng.choice([1.7, 1.7, 1.7, 1.7, 3.3, 5.0], int(lossy.sum()))   # one lost probe in sixty is 1.7%
    serial = serial or 'Q2XX-%s-%s' % (name[:4].upper(), uplink.upper())
    return dict(org=org, network='N_%s' % name.lower(), serial=serial, name=name, uplink=uplink, ip=ip, ts=ts,
                latency=np.maximum(latency, 0.5), loss=loss)


def step(x, day, delta):
    """From `day` (days after the first reading) on, the link is `delta` ms slower."""
    x['latency'][x['ts'] >= x['ts'][0] // DAY * DAY + day * DAY] += delta


def ramp(x, day, delta):
    """From `day` on, the link slows steadily, by `delta` ms in all at the last reading."""
    t0 = x['ts'][0] // DAY * DAY + day * DAY
    x['latency'] += delta * np.clip((x['ts'] - t0) / float(x['ts'][-1] - t0), 0, 1)


def outage(x, day, minutes, silent=False):
    """An outage starting `day` days in. Either the link reports total loss, or it reports nothing."""
    t0 = x['ts'][0] + int(day * DAY)
    hit = (x['ts'] >= t0) & (x['ts'] < t0 + minutes * 60)
    if silent:
        for k in ('ts', 'latency', 'loss'):
            x[k] = x[k][~hit]
    else:
        x['loss'][hit] = 100.0
        x['latency'][hit] = np.nan


def rows(x):
    """A link as the rows the collector would have written."""
    out = []
    for ts, latency, loss in zip(x['ts'].tolist(), x['latency'].tolist(), x['loss'].tolist()):
        empty = latency != latency
        out.append({'organizationName': x['org'], 'networkId': x['network'], 'serial': x['serial'],
                    'deviceName': x['name'], 'uplink': x['uplink'], 'ip': x['ip'], 'ts': ts,
                    'lossPercent': None if empty and loss < 100 else loss,
                    'latencyMs': None if empty else round(latency, 1)})
    return out


def fleet(end, days=240, seed=7):
    """Seven links at six made-up sites, ending at `end` (seconds since 1970)."""
    rng = np.random.default_rng(seed)
    start = int(end) // 60 * 60 - days * DAY

    def make(org, name, uplink, level, **kw):
        return link(org, name, uplink, start, days, level, rng, **kw)

    harbor = make('Example Org', 'Harbor', 'wan1', 6.0)
    step(harbor, int(days * 0.55), 11.0)                       # steps up and stays up: Worse
    quarry = make('Example Org', 'Quarry', 'wan1', 22.0, jitter=1.2)
    ramp(quarry, int(days * 0.2), 9.0)                         # creeps: Drifting
    orchard = make('Example Org', 'Orchard', 'wan1', 48.0, jitter=1.5)
    step(orchard, int(days * 0.3), -30.0)                      # gets faster: a shift, but nothing to flag
    mill = make('Example Org', 'Mill', 'wan1', 14.0)
    outage(mill, days * 0.4, 190)
    outage(mill, days - 3.2, 420, silent=True)
    ferry1 = make('Example Org', 'Ferry', 'wan1', 9.0)
    ferry2 = make('Example Org', 'Ferry', 'wan2', 31.0, jitter=2.5)
    outage(ferry1, days - 2.5, 700)
    depot = make('Example Org', 'Depot', 'wan1', 17.0)
    outage(depot, days * 0.7, 45)
    links = [harbor, quarry, orchard, mill, ferry1, ferry2, depot]
    for x in links:                                            # the collector was down for 50 minutes
        outage(x, days * 0.6, 50, silent=True)
    return links
