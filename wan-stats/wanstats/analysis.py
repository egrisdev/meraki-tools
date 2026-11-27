"""The statistics behind WAN stats, in one file.

Everything here works on numpy arrays and knows nothing about databases or
charts. The notebook, the console report and the validation script all call
`analyse()`, so their numbers always agree.

The method, in the order the code runs it:

1. `daily()`      one median and one 95th percentile per link per UTC day
2. `find_shifts`  dated level shifts in a daily series
3. `judge()`      baseline, trend test and verdict for one daily series
4. `Verdict`      the worse of the median's and the 95th percentile's verdicts
5. `outages()`    outages, with collector failures kept out of the count
"""
from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field

import numpy as np

DAY = 86400
RANKS = ('steady', 'drifting', 'worse')


@dataclass(frozen=True)
class Settings:
    """Every threshold the method uses. Pass a changed copy to `analyse()` to try other values."""
    window: int = 14              # days compared on each side of a possible shift
    min_before: int = 7           # at the start of the data, the fewest days allowed before a shift
    min_segment: int = 3          # when a date is refined, the shortest stretch allowed on either side
    shift_ms: float = 3.0         # the smallest shift recorded is this many ms ...
    shift_ratio: float = 0.10     # ... and this share of the lower level ...
    shift_noise: float = 4.0      # ... and this many times the link's day-to-day noise
    sustained_days: int = 30      # a level must hold this long to be the baseline
    trend_p: float = 0.001        # a rise counts when the Mann-Kendall p-value is below this
    drifting: tuple = (1.10, 3.0)  # Drifting: at least this ratio above baseline and this many ms
    worse: tuple = (1.50, 10.0)    # Worse: the same pair, larger
    day_readings: int = 300       # a day needs this many readings to get a daily value
    outage_loss: float = 80.0     # loss, in percent, that counts as down
    outage_seconds: int = 180     # an outage is at least this long
    silence_seconds: int = 600    # a link with no readings for longer than this is down
    bad_loss: float = 2.0         # a bad minute has this much loss or more ...
    bad_latency: float = 150.0    # ... or latency above this
    shared_share: float = 0.6     # a silence shared by this share of an organization's links is the collector's
    shared_links: int = 3         # ... when the organization has at least this many links reporting
    live_days: int = 30           # a link with no reading for this long has been removed
    min_readings: int = 10_000    # a removed link with fewer readings than this (about a week) is left out


@dataclass
class Series:
    """Every reading of one uplink to one test address, in time order."""
    org: str
    network: str
    serial: str
    name: str
    uplink: str
    ip: str
    ts: np.ndarray        # int64, seconds since 1970 UTC
    latency: np.ndarray   # float64 milliseconds, nan where the reading was empty
    loss: np.ndarray      # float64 percent, nan where the reading was empty


@dataclass
class Level:
    start: int    # first day (days since 1970-01-01 UTC)
    end: int      # last day
    days: int     # days with a value
    level: float  # median of the daily values


@dataclass
class Shift:
    day: int
    before: float
    after: float


@dataclass
class Result:
    """What `judge()` found in one daily series."""
    levels: list
    shifts: list
    baseline: float = math.nan
    current: float = math.nan
    verdict: str = 'new'       # 'new' (too little history), 'steady', 'drifting' or 'worse'
    why: str = ''              # 'shift' or 'trend' when flagged
    slope_month: float = 0.0   # Theil-Sen slope inside the current level, ms per month
    p: float = 1.0             # Mann-Kendall p-value of that slope
    start: float = math.nan    # median of the first four weeks of the current level
    gain: float = 0.0          # last four weeks minus the first four
    weeks: int = 0
    fit0: float = math.nan     # the two ends of the fitted line, for charts
    fit1: float = math.nan


# ---------------------------------------------------------------- 1. daily summary

def daily(ts, latency, min_readings=300):
    """Reduce readings to one median and one 95th percentile per UTC calendar day.

    Returns (days, medians, p95s). A day with fewer than `min_readings` readings is left out.
    """
    days, med, p95 = [], [], []
    if len(ts):
        d = ts // DAY
        edges = np.searchsorted(d, np.arange(d[0], d[-1] + 2))
        for k, (a, b) in enumerate(zip(edges[:-1], edges[1:])):
            v = latency[a:b]
            v = v[~np.isnan(v)]
            if len(v) >= min_readings:
                days.append(int(d[0]) + k)
                med.append(float(np.median(v)))
                p95.append(float(np.percentile(v, 95)))
    return np.array(days, dtype=np.int64), np.array(med, dtype=float), np.array(p95, dtype=float)


# ---------------------------------------------------------------- 2. level shifts

def _spread(v):
    """Sum of absolute deviations from the median: how badly one level fits a stretch."""
    return float(np.abs(v - np.median(v)).sum())


def find_shifts(x, s=Settings()):
    """Indexes into `x` where a new level starts.

    For every day, the `window` days after are compared with the `window` days before. A shift is a
    difference of medians of at least `shift_ms`, `shift_ratio` and `shift_noise` times the noise.
    """
    n, w = len(x), s.window
    if n < s.min_before + w:
        return []
    # A robust standard deviation of the daily values, from the median absolute day-to-day change.
    noise = 1.4826 * float(np.median(np.abs(np.diff(x)))) / math.sqrt(2)

    def big(a, b, noise=0.0):
        return abs(b - a) >= max(s.shift_ms, s.shift_ratio * min(a, b), s.shift_noise * noise)

    found = []
    for t in range(s.min_before, n - w + 1):
        a = float(np.median(x[max(0, t - w):t]))
        b = float(np.median(x[t:t + w]))
        if big(a, b, noise):
            found.append((abs(b - a), t))
    cuts = []
    for _, t in sorted(found, reverse=True):          # largest first, and no two within a window of each other
        if all(abs(t - c) >= w for c in cuts):
            cuts.append(t)
    cuts.sort()
    anchors = list(cuts)

    def prune():
        """Drop a shift whose two sides turn out to be the same level once whole stretches are compared."""
        while cuts:
            b = [0] + cuts + [n]
            lv = [float(np.median(x[i:j])) for i, j in zip(b[:-1], b[1:])]
            small = [(abs(lv[i + 1] - lv[i]), i) for i in range(len(cuts)) if not big(lv[i], lv[i + 1])]
            if not small:
                return
            k = min(small)[1]
            cuts.pop(k)
            anchors.pop(k)

    def refine():
        """Move each shift to the day that best separates the two levels around it (least absolute deviation).

        The two-window comparison only says a change lies near a day: the same difference shows for about
        a week on either side. The date may move back by up to a window from where the comparison fired
        and forward by half that, so a refined date always keeps half a window of data after it.
        """
        for _ in range(4):
            b = [0] + cuts + [n]
            moved = False
            for k in range(len(cuts)):
                lo = max(b[k] + s.min_segment, anchors[k] - w)
                hi = min(b[k + 2] - s.min_segment, anchors[k] + w // 2)
                if hi < lo:
                    continue
                best = min(range(lo, hi + 1), key=lambda t: _spread(x[b[k]:t]) + _spread(x[t:b[k + 2]]))
                if best != cuts[k]:
                    cuts[k] = b[k + 1] = best
                    moved = True
            if not moved:
                break

    prune()
    refine()
    prune()
    refine()
    return cuts


# ---------------------------------------------------------------- 3. baseline, trend and verdict

def theil_sen_mk(y):
    """Theil-Sen slope per step and the two-sided Mann-Kendall p-value. Needs eight values or more."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    if n < 8:
        return 0.0, 1.0
    i, j = np.triu_indices(n, 1)
    d = y[j] - y[i]
    score = float(np.sign(d).sum())
    z = (score - np.sign(score)) / math.sqrt(n * (n - 1) * (2 * n + 5) / 18) if score else 0.0
    return float(np.median(d / (j - i))), math.erfc(abs(z) / math.sqrt(2))


def size(lo, hi, s=Settings()):
    """How far `hi` sits above `lo`: 0 steady, 1 drifting, 2 worse. Both thresholds of a pair must be met."""
    if hi >= s.worse[0] * lo and hi - lo >= s.worse[1]:
        return 2
    if hi >= s.drifting[0] * lo and hi - lo >= s.drifting[1]:
        return 1
    return 0


def judge(days, x, s=Settings()):
    """Levels, shifts, trend and verdict for one daily series (`days` sorted, one value in `x` per day)."""
    if len(x) == 0:
        return Result(levels=[], shifts=[])
    cuts = find_shifts(x, s)
    b = [0] + cuts + [len(x)]
    levels = [Level(int(days[i]), int(days[j - 1]), j - i, float(np.median(x[i:j]))) for i, j in zip(b[:-1], b[1:])]
    shifts = [Shift(int(days[c]), levels[k].level, levels[k + 1].level) for k, c in enumerate(cuts)]
    r = Result(levels=levels, shifts=shifts)

    # Trend inside the current level, on weekly medians of the daily values.
    i, j = b[-2], b[-1]
    week = (days[i:j] - days[i]) // 7
    wy = np.array([float(np.median(x[i:j][week == k])) for k in np.unique(week) if (week == k).sum() >= 4])
    slope, r.p = theil_sen_mk(wy)
    r.weeks = len(wy)
    r.slope_month = slope * 4.345
    if r.weeks >= 8:
        # The size of the rise is measured from the data. A line fitted through a small step overstates it.
        r.start = float(np.median(wy[:4]))
        r.gain = float(np.median(wy[-4:])) - r.start
    elif r.weeks:
        r.start = float(np.median(wy))
    if r.weeks:
        r.fit0 = float(np.median(wy - slope * np.arange(r.weeks)))
        r.fit1 = r.fit0 + slope * (r.weeks - 1)

    # Baseline: the lowest level held for `sustained_days` or more.
    r.current = levels[-1].level
    held = [lv.level for lv in levels if lv.days >= s.sustained_days] or [lv.level for lv in levels]
    r.baseline = min(held)
    if len(x) < s.min_before + s.window:
        return r                                   # too little history to say anything: 'new'

    rank = size(r.baseline, r.current, s)
    r.why = 'shift' if rank else ''
    if r.p < s.trend_p and slope > 0:              # a significant rise inside the current level, on the same scale
        lo, hi = min(r.baseline, r.start), r.start + r.gain
        if size(lo, hi, s) > rank:
            rank, r.why, r.baseline, r.current = size(lo, hi, s), 'trend', lo, hi
    r.verdict = RANKS[rank]
    return r


def day_date(day):
    return datetime.date(1970, 1, 1) + datetime.timedelta(days=int(day))


def short_date(day):
    d = day_date(day)
    return '%d %s' % (d.day, d.strftime('%b %Y'))


@dataclass
class Verdict:
    """The latency verdict for a link: the worse of what the median and the 95th percentile show."""
    median: Result
    p95: Result

    @property
    def word(self):
        words = (self.median.verdict, self.p95.verdict)
        if 'new' in words:
            return 'new'
        return max(words, key=RANKS.index)

    @property
    def flagged(self):
        return self.word in ('drifting', 'worse')

    def reason(self):
        """One line saying what changed, for a flagged link."""
        a, b, parts = self.median, self.p95, []
        if not self.flagged:
            return ''
        rises = [e for e in a.shifts if e.after > e.before]
        if a.verdict != 'steady' and a.why == 'shift' and rises:
            parts.append('median %.1f -> %.1f ms on %s' % (a.baseline, a.current, short_date(rises[-1].day)))
        if a.verdict != 'steady' and a.why == 'trend':
            parts.append('median %.1f -> %.1f ms, %+.1f ms/month' % (a.baseline, a.current, a.slope_month))
        if b.verdict != 'steady' and b.why == 'trend':
            parts.append('p95 %.1f -> %.1f ms, %+.1f ms/month' % (b.baseline, b.current, b.slope_month))
        if not parts and b.verdict != 'steady':
            parts.append('p95 %.1f -> %.1f ms' % (b.baseline, b.current))
        if not parts:
            parts.append('median %.1f -> %.1f ms' % (a.baseline, a.current))
        return '; '.join(parts)


# ---------------------------------------------------------------- 5. outages

def merge(intervals, slack=0):
    """Join intervals that overlap or lie within `slack` seconds of each other."""
    out = []
    for a, b in sorted(intervals):
        if out and a - out[-1][1] <= slack:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def silences(x, end, s=Settings()):
    """Stretches where a link sent no readings for longer than `silence_seconds`.

    A link that has gone quiet, and has not been quiet long enough to count as removed, is silent up to `end`.
    """
    ts = x.ts
    out = [(int(ts[k]) + 60, int(ts[k + 1])) for k in np.where(np.diff(ts) > s.silence_seconds)[0]]
    last = int(ts[-1]) + 60
    if s.silence_seconds < end - last <= s.live_days * DAY:
        out.append((last, int(end)))
    return out


def collector_gaps(series, end, s=Settings()):
    """For each organization, the stretches where most of its links were quiet at the same moment.

    The collector asks Meraki one organization at a time, so a failed request loses that organization's
    readings and nobody else's. Such a stretch says nothing about the sites. It is found by walking
    through time and counting, at every moment, how many of the links that should be reporting are not.
    """
    gaps = {}
    for org in sorted({x.org for x in series}):
        change = {}                                  # time -> [change in links expected, change in links quiet]
        for x in series:
            if x.org != org:
                continue
            until = end if end - x.ts[-1] <= s.live_days * DAY else int(x.ts[-1])
            change.setdefault(int(x.ts[0]), [0, 0])[0] += 1
            change.setdefault(int(until), [0, 0])[0] -= 1
            for a, b in silences(x, end, s):
                change.setdefault(a, [0, 0])[1] += 1
                change.setdefault(b, [0, 0])[1] -= 1
        times = sorted(change)
        expected = quiet = 0
        found = []
        for t, after in zip(times, times[1:]):
            expected += change[t][0]
            quiet += change[t][1]
            if expected >= s.shared_links and quiet >= s.shared_share * expected:
                found.append((t, after))
        gaps[org] = merge(found)
    return gaps


def outages(x, end, org_gaps=(), s=Settings()):
    """Every stretch where a link was down, as (start, end) in seconds. Use `Link.outages` for the counted ones.

    Down means loss of `outage_loss` percent or more (empty readings inside such a run belong to it),
    or a silence that the rest of the organization did not share. Where a silence and a collector gap
    overlap, the link is down for the part of its silence that lies outside the gap, from the start of
    the first such stretch to the end of the last. Nothing is known about the minutes inside the gap.
    """
    high = x.loss >= s.outage_loss
    bad = high | np.isnan(x.loss)
    edge = np.diff(np.concatenate(([0], bad.astype(np.int8), [0])))
    found = [(int(x.ts[a]), int(x.ts[c - 1]) + 60)
             for a, c in zip(np.where(edge == 1)[0], np.where(edge == -1)[0]) if high[a:c].any()]
    for a, b in silences(x, end, s):
        outside, t = [], a
        for p, q in org_gaps:
            if q <= t or p >= b:
                continue
            if p > t:
                outside.append((t, p))
            t = max(t, q)
        if t < b:
            outside.append((t, b))
        outside = [(p, q) for p, q in outside if q - p > s.silence_seconds]
        if outside:
            found.append((outside[0][0], outside[-1][1]))
    return merge(found, slack=300)


# ---------------------------------------------------------------- the whole analysis

@dataclass
class Summary:
    """One link over one period: the row of the console report."""
    readings: int
    median: float
    p95: float
    bad_percent: float
    outages: int
    down_seconds: int
    availability: float

    @property
    def spread(self):
        return self.p95 - self.median


@dataclass
class Link:
    series: Series
    days: np.ndarray
    median: np.ndarray       # daily medians
    p95: np.ndarray          # daily 95th percentiles
    verdict: Verdict
    down: list               # every down stretch, however short
    live: bool               # still reporting at the end of the data
    settings: Settings = field(default_factory=Settings, repr=False)

    uplink: str = ''         # 'wan1', or 'wan1 to 8.8.4.4' where a device tests one uplink against several addresses

    org = property(lambda self: self.series.org)
    name = property(lambda self: self.series.name)
    port = property(lambda self: self.series.uplink)
    label = property(lambda self: '%s %s' % (self.series.name, self.uplink))

    def __post_init__(self):
        self.uplink = self.uplink or self.series.uplink

    @property
    def outages(self):
        """Outages long enough to count."""
        return [(a, b) for a, b in self.down if b - a >= self.settings.outage_seconds]

    def summary(self, start, end):
        x, s = self.series, self.settings
        m = (x.ts >= start) & (x.ts < end)
        lat, loss = x.latency[m], x.loss[m]
        seen = lat[~np.isnan(lat)]
        cut = [(max(a, start), min(b, end)) for a, b in self.outages if b > start and a < end]
        down = sum(b - a for a, b in cut)
        n = int(m.sum())
        return Summary(
            readings=n,
            median=float(np.median(seen)) if len(seen) else math.nan,
            p95=float(np.percentile(seen, 95)) if len(seen) else math.nan,
            bad_percent=100.0 * float(((loss >= s.bad_loss) | (lat > s.bad_latency)).sum()) / n if n else math.nan,
            outages=len(cut), down_seconds=int(down), availability=100.0 * (1 - down / float(end - start)))


@dataclass
class Analysis:
    links: list          # sorted by organization, site and uplink
    gaps: dict           # organization -> collector gaps
    start: int           # first reading
    end: int             # one minute after the last reading: the "now" of every report
    settings: Settings

    def find(self, name, uplink, org=None):
        for link in self.links:
            if link.name == name and uplink in (link.uplink, link.port) and org in (None, link.org):
                return link
        raise KeyError('%s %s' % (name, uplink))

    @property
    def flagged(self):
        """Flagged links that are still reporting, the worst first."""
        hit = [link for link in self.links if link.live and link.verdict.flagged]
        return sorted(hit, key=lambda link: (-RANKS.index(link.verdict.word), link.org, link.name, link.uplink))

    @property
    def sites(self):
        return len({(link.org, link.name) for link in self.links})

    def checks(self):
        """The data checks: how much there is and how complete it is."""
        s = self.settings
        n = sum(len(k.series.ts) for k in self.links)
        minutes = sum((int(k.series.ts[-1]) - int(k.series.ts[0])) // 60 + 1 for k in self.links)
        covered = sum(len(np.unique(k.series.ts // 60)) for k in self.links)
        empty = sum(int(np.isnan(k.series.latency).sum()) for k in self.links)
        gaps = merge([g for org in self.gaps.values() for g in org])
        return dict(
            readings=n, links=len(self.links), sites=self.sites, organizations=len({k.org for k in self.links}),
            coverage=100.0 * covered / minutes if minutes else math.nan,
            empty=100.0 * empty / n if n else math.nan,
            any_loss=100.0 * sum(int((k.series.loss > 0).sum()) for k in self.links) / n if n else math.nan,
            bad_loss=100.0 * sum(int((k.series.loss >= s.bad_loss).sum()) for k in self.links) / n if n else math.nan,
            collector_gaps=len(gaps), collector_gap_hours=sum(b - a for a, b in gaps) / 3600.0)


def join_replaced(series):
    """Give a replaced device its link's history back.

    A new device reports under a new serial. Where it has the same name in the same network, and the
    two devices never reported at the same time, their readings are one link.
    """
    groups = {}
    for x in series:
        groups.setdefault((x.org, x.network, x.name, x.uplink, x.ip), []).append(x)
    out = []
    for group in groups.values():
        group.sort(key=lambda x: int(x.ts[0]))
        if len(group) > 1 and all(a.ts[-1] < b.ts[0] for a, b in zip(group, group[1:])):
            last = group[-1]
            out.append(Series(last.org, last.network, last.serial, last.name, last.uplink, last.ip,
                              ts=np.concatenate([x.ts for x in group]),
                              latency=np.concatenate([x.latency for x in group]),
                              loss=np.concatenate([x.loss for x in group])))
        else:
            out += group
    return out


def analyse(series, settings=Settings(), end=None):
    """Run the whole method over every series from the store and return an `Analysis`."""
    s = settings
    series = [x for x in series if len(x.ts)]
    if not series:
        return Analysis([], {}, 0, 0, s)
    if end is None:
        end = max(int(x.ts[-1]) for x in series) + 60
    series = join_replaced(series)
    # A link that was removed after only a few days has too little history to be worth a row.
    series = [x for x in series if end - x.ts[-1] <= s.live_days * DAY or len(x.ts) >= s.min_readings]
    gaps = collector_gaps(series, end, s)
    links = []
    for x in sorted(series, key=lambda x: (x.org, x.name, x.uplink, x.ip, x.serial)):
        days, med, p95 = daily(x.ts, x.latency, s.day_readings)
        links.append(Link(series=x, days=days, median=med, p95=p95,
                          verdict=Verdict(judge(days, med, s), judge(days, p95, s)),
                          down=outages(x, end, gaps[x.org], s),
                          live=bool(end - x.ts[-1] <= s.live_days * DAY), settings=s))
    # Two rows must never carry the same name. Say what tells them apart: the test address, or the device.
    same = {}
    for k in links:
        same.setdefault((k.org, k.name, k.port), []).append(k)
    for group in same.values():
        if len(group) > 1:
            by_address = len({k.series.ip for k in group}) == len(group)
            for k in group:
                k.uplink = '%s to %s' % (k.port, k.series.ip) if by_address else '%s on %s' % (k.port, k.series.serial)
    return Analysis(links, gaps, min(int(x.ts[0]) for x in series), int(end), s)
