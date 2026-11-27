#!/usr/bin/env python3
"""Check the method on the data in the store.

Backtest     what the method would have reported on earlier dates, using only the data it had then
Injection    plant steps and ramps of known size in steady links and see which ones are found
Sensitivity  the verdicts under looser and stricter thresholds

It needs about a year of history to say much.
"""
import argparse
import sys

import numpy as np

from wanstats import analysis, config, validation


def main(argv=None):
    parser = argparse.ArgumentParser(description='Check the method on the data in the store.')
    parser.add_argument('--db', metavar='URL', help='database to read (default: WAN_STATS_DB, or a SQLite file)')
    args = parser.parse_args(argv)
    store = config.open_db(args.db, create=False)
    try:
        a = analysis.analyse(store.load())
    finally:
        store.close()
    if not a.links:
        sys.exit('No readings yet. Run collect.py first.')

    history = validation.backtest(a)
    reference = validation.monthly_rule(a)
    print('BACKTEST: links flagged on earlier dates, using only the data up to each date')
    print('%-13s %-52s %s' % ('Data up to', 'This method (Worse in capitals)', 'Monthly rule'))
    for (day, flags), simple in zip(history, reference):
        mine = ', '.join(k.label.upper() if word == 'worse' else k.label for k, word in flags) or '-'
        print('%-13s %-52s %s' % (analysis.short_date(day), mine, ', '.join(k.label for k in simple) or '-'))
    print('flags raised: %d on %d links by this method, %d on %d links by the monthly rule' % (
        sum(len(f) for _, f in history), len({k.label for _, f in history for k, _ in f}),
        sum(map(len, reference)), len({k.label for f in reference for k in f})))

    links = validation.steady_links(a, history)
    print('\nINJECTION: changes planted in %d steady links' % len(links))
    if not links:
        print('No link is steady enough, with a year of history, to plant a change in.')
    else:
        print(', '.join(k.label for k in links))
        r = validation.inject(a, links)
        print('\n%-13s %5s  %11s  %10s  %7s' % ('Planted step', 'Runs', 'Shift found', 'Date exact', 'Flagged'))
        for delta, x in r['steps'].items():
            exact = '%d%%' % round(100 * np.mean(np.array(x['errors']) == 0)) if x['errors'] else '-'
            print('%-13s %5d  %11d  %10s  %7d' % ('%d ms' % delta, x['runs'], x['found'], exact, x['flagged']))
        seen = [d for d in r['delays'] if d]
        if seen:
            print('a 10 ms step is reported after a median of %d days of data (%d to %d)' % (
                np.median(seen), min(seen), max(seen)))
        print('\n%-13s %5s  %7s' % ('Planted ramp', 'Runs', 'Flagged'))
        for gain, x in r['ramps'].items():
            print('%-13s %5d  %7d' % ('%d ms' % gain, x['runs'], x['flagged']))

    print('\nSENSITIVITY: verdicts today under other thresholds (ratio, ms)')
    for worse, drifting, w, d in validation.sensitivity(a):
        print('worse %-11s drifting %-11s  Worse: %-22s Drifting: %s' % (
            worse, drifting, ', '.join(w) or '-', ', '.join(d) or '-'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
