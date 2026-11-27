#!/usr/bin/env python3
"""Print the console report: the latency verdict for every link, and one period's numbers.

The exit status is 0 when no link is flagged, 1 when a link is Drifting, 2 when a link is Worse
and 3 when there is nothing to report on. That is the convention monitoring systems use.
"""
import argparse
import sys
import time

from wanstats import analysis, config, console


def whole_days(text):
    days = int(text)
    if days < 1:
        raise argparse.ArgumentTypeError('give a number of days, 1 or more')
    return days


def main(argv=None):
    parser = argparse.ArgumentParser(description='Print the WAN stats console report.')
    parser.add_argument('--days', type=whole_days, default=7, help='length of the period in the table (default: 7)')
    parser.add_argument('--history', type=whole_days, metavar='DAYS',
                        help='how far back the verdicts look (default: everything stored)')
    parser.add_argument('--db', metavar='URL', help='database to read (default: WAN_STATS_DB, or a SQLite file)')
    args = parser.parse_args(argv)

    try:
        store = config.open_db(args.db, create=False)
    except SystemExit as e:
        print(e)
        return 3
    try:
        series = store.load(since=time.time() - args.history * analysis.DAY if args.history else None)
    finally:
        store.close()
    text, status = console.render(analysis.analyse(series), days=args.days)
    print(text)
    return status


if __name__ == '__main__':
    sys.exit(main())
