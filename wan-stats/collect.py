#!/usr/bin/env python3
"""Collect uplink loss and latency from Meraki. Run it from cron every four minutes.

    */4 * * * * /path/to/python /path/to/wan-stats/collect.py >> /path/to/collect.log 2>&1

Each run asks for the last five minutes, so runs overlap by a minute and nothing is lost when one
is late. Readings that are already stored are skipped. If runs were missed, the next one asks
Meraki for the minutes in between.
"""
import argparse
import re
import sys
import time

from wanstats import collector, config

UNITS = {'m': 60, 'h': 3600, 'd': 86400}


def span(text):
    """'90m', '6h' or '14d' as seconds."""
    found = re.fullmatch(r'(\d+)([mhd])', text.strip().lower())
    if not found:
        raise argparse.ArgumentTypeError('give a length such as 90m, 6h or 14d')
    return int(found.group(1)) * UNITS[found.group(2)]


def main(argv=None):
    parser = argparse.ArgumentParser(description='Collect uplink loss and latency from Meraki into the database.')
    parser.add_argument('--db', metavar='URL', help='database to write to (default: WAN_STATS_DB, or a SQLite file)')
    parser.add_argument('--backfill', type=span, metavar='LENGTH',
                        help='also fetch history, for example 6h or 14d. Meraki keeps 60 days')
    parser.add_argument('--no-catch-up', action='store_true', help='do not fetch the minutes missed by earlier runs')
    parser.add_argument('-q', '--quiet', action='store_true', help='print errors only')
    args = parser.parse_args(argv)

    cfg = config.load()
    if not cfg.api_key:
        sys.exit('MERAKI_API_KEY is not set. Put it in the .env file beside this script, or in the environment.')
    import meraki
    dashboard = meraki.DashboardAPI(api_key=cfg.api_key, output_log=False, print_console=False,
                                    suppress_logging=True, caller='WanStats/1.0 MerakiTools')
    stamp = time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())

    def log(line):
        if not args.quiet or 'failed' in line or 'could not' in line:
            print(stamp, line, flush=True)

    store = config.open_db(args.db)
    try:
        slices = 0 if args.no_catch_up else 10 ** 6 if args.backfill else 30
        new, read, failed = collector.collect(dashboard, store, org_ids=cfg.org_ids, max_slices=slices,
                                              backfill=args.backfill or 0, keep_days=cfg.keep_days, log=log)
    except (meraki.APIError, OSError) as e:        # a wrong key, or no way to reach Meraki
        log('failed: could not list the organizations: %s' % e)
        return 1
    finally:
        store.close()
    if not read and not failed:
        log('failed: the API key can see no organization' + (' with the ID in MERAKI_ORG_ID' if cfg.org_ids else ''))
    return 0 if read else 1


if __name__ == '__main__':
    sys.exit(main())
