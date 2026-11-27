#!/usr/bin/env python3
"""Make a database of invented readings, to try the reports without a Meraki network.

    python demo.py
    python report.py --db sqlite:///demo.sqlite

Seven links at six made-up sites over eight months. One link steps up and stays up, one creeps,
one gets faster, and a few have outages.
"""
import argparse
import os
import sys
import time

from wanstats import config, synthetic


def main(argv=None):
    parser = argparse.ArgumentParser(description='Make a database of invented readings.')
    parser.add_argument('--db', metavar='URL', default='sqlite:///demo.sqlite', help='where to write it')
    parser.add_argument('--days', type=int, default=240, help='how much history to invent (default: 240)')
    args = parser.parse_args(argv)

    if args.db.startswith('sqlite:///'):
        path = os.path.join(str(config.HERE), args.db[len('sqlite:///'):])
        if os.path.exists(path):
            sys.exit('%s is already there. Delete it first, or name another with --db.' % path)
    store = config.open_db(args.db)
    try:
        for x in synthetic.fleet(time.time(), days=args.days):
            rows = synthetic.rows(x)
            for i in range(0, len(rows), 100000):
                store.add(rows[i:i + 100000])
            print('%-8s %-5s %7d readings' % (x['name'], x['uplink'], len(rows)), flush=True)
    finally:
        store.close()
    print('\nNow try:  python report.py --db %s' % args.db)
    return 0


if __name__ == '__main__':
    sys.exit(main())
