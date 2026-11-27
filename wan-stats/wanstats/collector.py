"""The collector: a small extract, transform, load job that cron runs every few minutes.

Extract    ask Meraki for the last five minutes of loss and latency, one organization at a time
Transform  flatten the answer into one row per link per minute (`flatten`)
Load       hand the rows to the store, which skips the ones it already has

It only reads from Meraki.
"""
from __future__ import annotations

import datetime
import time

DAY = 86400
SLICE = 300          # the API returns at most five minutes per request
LAG = 120            # ... and nothing newer than two minutes ago
LOOKBACK = 60 * DAY  # ... and nothing older than 60 days
UTC = datetime.timezone.utc


def parse_time(text):
    """'2026-03-11T08:15:00Z' as whole seconds since 1970 UTC."""
    return int(datetime.datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp())


def iso(ts):
    return datetime.datetime.fromtimestamp(int(ts), UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def flatten(org_name, entries, names):
    """Transform: the API's nested answer as flat rows, one per link per minute.

    `names` maps a serial to its device name. An entry with no uplink carries no measurement
    (the device was not reporting), so it is dropped: the missing minutes are what marks the outage.
    """
    rows = []
    for e in entries or []:
        serial, uplink = e.get('serial'), e.get('uplink')
        if not serial or not uplink:
            continue
        for point in e.get('timeSeries') or []:
            if not point.get('ts'):
                continue
            rows.append({
                'organizationName': org_name, 'networkId': e.get('networkId') or '', 'serial': serial,
                'deviceName': names.get(serial) or serial, 'uplink': uplink, 'ip': e.get('ip') or '',
                'ts': parse_time(point['ts']),
                'lossPercent': point.get('lossPercent'), 'latencyMs': point.get('latencyMs')})
    return rows


def plan(mark, now, max_slices):
    """Which earlier five-minute slices to ask for, so a run that was missed leaves no hole.

    `now` is the time the request for the latest five minutes was made. That request returned the
    readings from `now - LAG - SLICE` to `now - LAG`. `mark` is where the last run got to for this
    organization. Returns (slices, finished). The slices are (start, end) pairs, oldest first. They
    run a minute into what the latest request covered, because the two clocks never agree exactly.
    `finished` is False when there were more than `max_slices`: the next run carries on.
    """
    latest = int(now) - LAG - SLICE
    if mark is None or mark >= latest:
        return [], True
    t = max(int(mark), int(now) - LOOKBACK + 3600)
    stop = latest + 60
    slices = []
    while t < stop and len(slices) < max_slices:
        slices.append((t, min(t + SLICE, stop)))
        t += SLICE
    return slices, t >= stop


def fill(fetch, store, key, slices, org_name, log):
    """Fetch the earlier slices one at a time, storing each and moving the mark as it goes.

    Returns (new readings, slices stored, outcome). A slice Meraki refuses is skipped, and after three
    in a row the gap is given up ('abandon'). Any other failure stops the work with the mark where it
    is, so the next run tries again ('stop'). Otherwise the outcome is 'done'.
    """
    new = stored = refused = 0
    for t0, t1 in slices:
        try:
            rows = fetch(t0, t1)
        except Exception as e:
            status = getattr(e, 'status', None)
            if isinstance(status, int) and 400 <= status < 500 and status != 429:
                refused += 1
                if refused == 3:                        # Meraki is refusing these requests as a kind, so stop asking
                    log('%s: could not fill the gap from %s, Meraki refused it: %s' % (org_name, iso(t0), e))
                    return new, stored, 'abandon'
                store.set_mark(key, t1)
                continue
            log('%s: stopped filling the gap at %s, the next run carries on: %s' % (org_name, iso(t0), e))
            return new, stored, 'stop'
        refused = 0
        new += store.add(rows)
        stored += 1
        store.set_mark(key, t1)
    return new, stored, 'done'


def collect(dashboard, store, org_ids=(), clock=time.time, max_slices=30, backfill=0, keep_days=0, log=print):
    """One run. Returns (new readings, organizations read, organizations that failed).

    `dashboard` is a `meraki.DashboardAPI`. `backfill` is how many seconds of history to ask for
    again, up to the 60 days Meraki keeps.
    """
    orgs = dashboard.organizations.getOrganizations()
    if org_ids:
        orgs = [o for o in orgs if str(o.get('id')) in set(org_ids)]
    total = read = failed = 0
    for org in orgs:
        org_id, org_name = str(org['id']), org.get('name') or str(org['id'])
        key = 'org:' + org_id
        try:
            devices = dashboard.organizations.getOrganizationDevices(org_id, total_pages='all')
            names = {d['serial']: d.get('name') for d in devices if d.get('serial')}
            request = dashboard.organizations.getOrganizationDevicesUplinksLossAndLatency

            # The clock is read here, not at the start of the run: an earlier organization may have taken minutes.
            now = int(clock())
            new = store.add(flatten(org_name, request(org_id, timespan=SLICE), names))
            mark = now - min(int(backfill), LOOKBACK) if backfill else store.mark(key)
            slices, finished = plan(mark, now, max_slices)
            more, stored, outcome = fill(
                lambda t0, t1: flatten(org_name, request(org_id, t0=iso(t0), t1=iso(t1)), names),
                store, key, slices, org_name, log)
            if outcome == 'abandon' or (outcome == 'done' and finished):
                store.set_mark(key, now - LAG)
            total += new + more
            read += 1
            log('%s: %d new readings%s' % (org_name, new + more, ', %d earlier slices' % stored if stored else ''))
        except Exception as e:                          # one organization failing must not stop the others
            failed += 1
            log('%s: failed: %s: %s' % (org_name, type(e).__name__, e))
    now = int(clock())
    if keep_days and now - (store.mark('pruned') or 0) >= DAY:
        gone = store.prune(now - keep_days * DAY)
        store.set_mark('pruned', now)
        if gone:
            log('removed %d readings older than %d days' % (gone, keep_days))
    return total, read, failed
