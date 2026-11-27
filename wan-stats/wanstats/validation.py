"""Checks on the method itself: does it find changes of a known size, and what would it have said earlier?

Nobody labels a year of WAN readings, so there is no list of real incidents to score against.
These checks make their own evidence from the data in the store.
"""
from __future__ import annotations

import collections
import dataclasses

import numpy as np

from .analysis import RANKS, judge


def verdict(days, median, p95, s):
    a, b = judge(days, median, s), judge(days, p95, s)
    words = [w for w in (a.verdict, b.verdict) if w != 'new']
    return (max(words, key=RANKS.index) if words else 'new'), a, b


def backtest(a, replays=9, every=30):
    """Run the method as if on earlier dates, each time with only the data that existed then.

    Returns [(last day used, [(link, verdict)])], oldest first.
    """
    s = a.settings
    last = (a.end - 1) // 86400
    out = []
    for back in range(replays - 1, -1, -1):
        end = last - back * every
        flags = []
        for k in a.links:
            m = k.days <= end
            if m.sum() < 60 or k.days[m][-1] < end - 7:        # too new, or already gone, on that date
                continue
            word, _, _ = verdict(k.days[m], k.median[m], k.p95[m], s)
            if word in ('drifting', 'worse'):
                flags.append((k, word))
        out.append((int(end), flags))
    return out


def monthly_rule(a, replays=9):
    """A simpler rule, replayed on the same dates, as a reference.

    It compares 30-day periods, on the median and on the 95th percentile. Worse: the latest period is
    50% and 10 ms above the best period so far. Drifting: the middle one of the last three periods is
    10% and 3 ms above it.
    """
    month = 30 * 86400

    def periods(k):
        x = k.series
        b = (a.end - 1 - x.ts) // month                        # period 0 is the latest
        med, p95 = {}, {}
        for n in range(replays + 12):
            v = x.latency[b == n]
            v = v[~np.isnan(v)]
            if len(v) >= 20000:
                med[n], p95[n] = float(np.median(v)), float(np.percentile(v, 95))
        return med, p95

    def rule(values, upto):
        seen = {n: v for n, v in values.items() if n >= upto}
        if upto not in seen or len(seen) < 4:
            return False
        best, latest = min(seen.values()), seen[upto]
        middle = float(np.median([seen[n] for n in (upto, upto + 1, upto + 2) if n in seen]))
        return (latest >= 1.5 * best and latest - best >= 10) or (middle >= 1.1 * best and middle - best >= 3)

    table = [(k, periods(k)) for k in a.links]
    return [[k for k, (med, p95) in table if rule(med, back) or rule(p95, back)] for back in range(replays - 1, -1, -1)]


def steady_links(a, history):
    """Links fit to plant a change in: no shift, never flagged, and no rise or fall of their own."""
    flagged = {id(k) for _, flags in history for k, _ in flags}
    out = []
    for k in a.links:
        m, p = k.verdict.median, k.verdict.p95
        if (not m.shifts and not p.shifts and m.verdict == p.verdict == 'steady' and id(k) not in flagged
                and len(k.days) >= 350 and abs(m.gain) < 1.5 and abs(p.gain) < 1.5):
            out.append(k)
    return out


def inject(a, links, sizes=(1, 2, 3, 4, 6, 8, 10, 12, 20), starts=tuple(range(40, 330, 20)),
           rises=(2, 4, 6, 10, 15), ramp_starts=(45, 105, 165, 225)):
    """Plant steps and ramps of known size and date in steady links, and record what the method reports."""
    s = a.settings
    steps = {d: dict(runs=0, found=0, errors=[], flagged=0, by_link=collections.Counter()) for d in sizes}
    for k in links:
        for delta in sizes:
            for start in starts:
                m = k.days >= k.days[0] + start
                word, med, _ = verdict(k.days, k.median + delta * m, k.p95 + delta * m, s)
                true = int(k.days[m][0])
                hits = [e.day - true for e in med.shifts if e.after > e.before and abs(e.day - true) <= 14]
                r = steps[delta]
                r['runs'] += 1
                r['flagged'] += word != 'steady'
                if word != 'steady':
                    r['by_link'][k.label] += 1
                if hits:
                    r['found'] += 1
                    r['errors'].append(min(hits, key=abs))
    # How many days of data after a 10 ms step before it is reported.
    delays = []
    for k in links:
        for start in starts:
            m = k.days >= k.days[0] + start
            true, x = int(k.days[m][0]), k.median + 10.0 * m
            for n in range(5, 31):
                cut = k.days <= true + n - 1
                found = judge(k.days[cut], x[cut], s).shifts
                if any(e.after > e.before and abs(e.day - true) <= 14 for e in found):
                    delays.append(n)
                    break
            else:
                delays.append(None)
    ramps = {g: dict(runs=0, flagged=0, why=collections.Counter()) for g in rises}
    for k in links:
        for gain in rises:
            for start in ramp_starts:
                first = k.days[0] + start
                add = np.clip((k.days - first) / float(k.days[-1] - first), 0, 1) * gain
                word, med, p95 = verdict(k.days, k.median + add, k.p95 + add, s)
                r = ramps[gain]
                r['runs'] += 1
                if word != 'steady':
                    r['flagged'] += 1
                    r['why'][med.why or p95.why] += 1
    return dict(links=[k.label for k in links], steps=steps, ramps=ramps, delays=delays,
                dates=len(starts), ramp_dates=len(ramp_starts))


def sensitivity(a, worse=((1.3, 5.0), (1.5, 10.0), (2.0, 15.0)), drifting=((1.05, 2.0), (1.10, 3.0), (1.20, 5.0))):
    """The verdicts on today's data under looser and stricter thresholds.

    The smallest recorded shift moves with the Drifting pair.
    Returns [(worse, drifting, [Worse links], [Drifting links])].
    """
    out = []
    for w in worse:
        for d in drifting:
            s = dataclasses.replace(a.settings, worse=w, drifting=d, shift_ratio=d[0] - 1, shift_ms=d[1])
            found = {'worse': [], 'drifting': []}
            for k in a.links:
                if k.live:
                    word, _, _ = verdict(k.days, k.median, k.p95, s)
                    if word in found:
                        found[word].append(k.label)
            out.append((w, d, found['worse'], found['drifting']))
    return out
