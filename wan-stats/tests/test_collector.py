from wanstats import collector
from wanstats.collector import LAG, SLICE, flatten, plan
from wanstats.storage import open_store

NOW = 1_800_000_000


def entry(serial='Q2AA-AAAA-AAAA', uplink='wan1', ip='8.8.8.8', minutes=(0, 1, 2, 3, 4), base=NOW - 420, loss=0.0):
    return {'networkId': 'N_1', 'serial': serial, 'uplink': uplink, 'ip': ip,
            'timeSeries': [{'ts': collector.iso(base + 60 * m), 'lossPercent': loss, 'latencyMs': 12.5}
                           for m in minutes]}


class Refused(Exception):
    """What the Meraki library raises when the API answers with an error status."""

    def __init__(self, status):
        super().__init__('%d from Meraki' % status)
        self.status = status


class Organizations:
    """Stands in for `meraki.DashboardAPI().organizations`. `now` is the clock, and requests can take time."""

    def __init__(self, orgs, broken=(), slow=0):
        self.orgs, self.broken, self.slow, self.calls, self.fail = orgs, set(broken), slow, [], None
        self.now = NOW

    def getOrganizations(self):
        return [{'id': i, 'name': n} for i, n in self.orgs]

    def getOrganizationDevices(self, org_id, total_pages=1):
        assert total_pages == 'all'
        self.now += self.slow                       # a long device list, or a wait for the rate limit
        return [{'serial': 'Q2AA-AAAA-AAAA', 'name': 'Harbor'}, {'serial': 'Q2BB-BBBB-BBBB', 'name': None}]

    def getOrganizationDevicesUplinksLossAndLatency(self, org_id, **kw):
        self.calls.append((org_id, kw))
        if org_id in self.broken:
            raise RuntimeError('404 Not Found')
        if 't0' in kw:
            t0, t1 = collector.parse_time(kw['t0']), collector.parse_time(kw['t1'])
            assert t1 - t0 <= SLICE and t1 <= self.now - LAG, 'Meraki would refuse this request'
            if self.fail and self.fail(t0):
                raise self.fail(t0)
            return [entry(base=t0, minutes=range((t1 - t0) // 60))]
        return [entry(base=self.now - LAG - SLICE)]


class Dashboard:
    def __init__(self, *args, **kw):
        self.organizations = Organizations(*args, **kw)


def run(dash, store, now=NOW, **kw):
    dash.organizations.now = now
    lines = []
    kw.setdefault('pause', lambda seconds: None)             # the tests do not wait between requests
    return collector.collect(dash, store, clock=lambda: dash.organizations.now, log=lines.append, **kw), lines


def memory():
    return open_store('sqlite:///:memory:')


def minutes(store, org=None):
    """The stored timestamps of the first link, as minutes after NOW - 420."""
    return [(t - (NOW - LAG - SLICE)) // 60 for t in store.load()[0].ts.tolist()]


# ---------------------------------------------------------------- transform

def test_flatten_makes_one_row_per_link_per_minute():
    rows = flatten('Org', [entry(minutes=(0, 1))], {'Q2AA-AAAA-AAAA': 'Harbor'})
    assert rows == [
        {'organizationName': 'Org', 'networkId': 'N_1', 'serial': 'Q2AA-AAAA-AAAA', 'deviceName': 'Harbor',
         'uplink': 'wan1', 'ip': '8.8.8.8', 'ts': NOW - 420 + 60 * m, 'lossPercent': 0.0, 'latencyMs': 12.5}
        for m in (0, 1)]


def test_flatten_reads_the_api_timestamp_as_utc():
    assert collector.parse_time('2027-01-15T08:00:00Z') == NOW
    assert collector.iso(NOW) == '2027-01-15T08:00:00Z'


def test_flatten_drops_an_entry_with_no_uplink():
    silent = {'networkId': 'N_1', 'serial': 'Q2AA-AAAA-AAAA', 'uplink': None, 'ip': None,
              'timeSeries': [{'ts': collector.iso(NOW), 'lossPercent': None, 'latencyMs': None}]}
    assert flatten('Org', [silent], {}) == []


def test_flatten_keeps_an_empty_reading_and_names_an_unnamed_device_by_its_serial():
    e = entry(serial='Q2BB-BBBB-BBBB', minutes=(0,))
    e['timeSeries'][0].update(lossPercent=None, latencyMs=None)
    row, = flatten('Org', [e], {'Q2BB-BBBB-BBBB': None})
    assert row['deviceName'] == 'Q2BB-BBBB-BBBB' and row['latencyMs'] is None and row['lossPercent'] is None


def test_flatten_survives_an_empty_answer():
    assert flatten('Org', None, {}) == [] and flatten('Org', [{'serial': 'S', 'uplink': 'wan1'}], {}) == []


# ---------------------------------------------------------------- catching up

def test_plan_asks_for_nothing_extra_when_runs_are_on_time():
    assert plan(None, NOW, 30) == ([], True)
    assert plan(NOW - 240 - LAG, NOW, 30) == ([], True)


def test_plan_fills_a_hole_with_five_minute_slices_that_reach_into_the_latest_request():
    mark = NOW - 3600
    slices, finished = plan(mark, NOW, 30)
    assert slices[0][0] == mark and slices[-1][1] == NOW - LAG - SLICE + 60 and finished
    assert all(b - a <= SLICE for a, b in slices)
    assert all(a[1] == b[0] for a, b in zip(slices, slices[1:]))


def test_plan_spreads_a_long_hole_over_several_runs():
    mark = NOW - 10 * 86400
    slices, finished = plan(mark, NOW, 5)
    assert len(slices) == 5 and slices[-1][1] == mark + 5 * SLICE and not finished


def test_plan_never_asks_for_more_than_meraki_keeps():
    slices, _ = plan(NOW - 100 * 86400, NOW, 3)
    assert slices[0][0] == NOW - 60 * 86400 + 3600


# ---------------------------------------------------------------- a whole run

def test_a_run_stores_the_readings_and_overlapping_runs_store_them_once():
    dash, store = Dashboard([('1', 'Org')]), memory()
    (new, read, failed), lines = run(dash, store)
    assert (new, read, failed) == (5, 1, 0) and lines == ['Org: 5 new readings']
    (new, _, _), _ = run(dash, store, now=NOW + 240)          # four minutes later: one minute overlaps
    assert new == 4
    x, = store.load()
    assert (x.org, x.name, x.uplink, len(x.ts)) == ('Org', 'Harbor', 'wan1', 9)
    assert dash.organizations.calls == [('1', {'timespan': 300})] * 2


def test_a_missed_run_is_caught_up():
    dash, store = Dashboard([('1', 'Org')]), memory()
    run(dash, store)
    (new, _, _), lines = run(dash, store, now=NOW + 1200)     # twenty minutes later
    extra = [(kw['t0'], kw['t1']) for _, kw in dash.organizations.calls if 't0' in kw]
    assert extra[0] == (collector.iso(NOW - 120), collector.iso(NOW + 180)) and len(extra) == 4
    assert new == 20 and lines == ['Org: 20 new readings, 4 earlier slices']
    assert minutes(store) == list(range(25))                  # no minute missing, none stored twice


def test_a_slow_request_does_not_lose_the_minutes_it_took():
    dash, store = Dashboard([('1', 'Org')], slow=150), memory()   # the device list takes two and a half minutes
    run(dash, store)
    run(dash, store, now=NOW + 240)
    run(dash, store, now=NOW + 480)
    got = minutes(store)
    assert got == list(range(got[0], got[-1] + 1))


def test_the_second_organization_is_planned_from_its_own_clock():
    dash, store = Dashboard([('1', 'One'), ('2', 'Two')], slow=900), memory()
    run(dash, store, backfill=3600, max_slices=10 ** 6)
    two = [kw for org, kw in dash.organizations.calls if org == '2']
    asked = NOW + 1800                                        # when the second organization's turn came
    assert two[0] == {'timespan': 300}
    assert two[1]['t0'] == collector.iso(asked - 3600) and two[-1]['t1'] == collector.iso(asked - LAG - SLICE + 60)
    assert store.mark('org:2') == asked - LAG


def test_catching_up_can_be_turned_off():
    dash, store = Dashboard([('1', 'Org')]), memory()
    run(dash, store)
    run(dash, store, now=NOW + 1200, max_slices=0)
    assert all('t0' not in kw for _, kw in dash.organizations.calls)


def test_backfill_asks_for_history_on_the_first_run():
    dash, store = Dashboard([('1', 'Org')]), memory()
    (new, _, _), _ = run(dash, store, backfill=3600, max_slices=10 ** 6)
    assert len([kw for _, kw in dash.organizations.calls if 't0' in kw]) == 11
    assert new == 58                                          # an hour back, less the two minutes Meraki holds back
    assert minutes(store) == list(range(-53, 5))


def test_history_is_fetched_at_no_more_than_five_requests_a_second():
    dash, store, waits = Dashboard([('1', 'Org')]), memory(), []
    run(dash, store, backfill=3600, max_slices=10 ** 6, pause=waits.append)
    assert len(waits) == 10 and all(0.15 < w <= collector.PACE for w in waits)      # between the 11 slices
    waits.clear()
    run(dash, store, now=NOW + 240, pause=waits.append)       # an ordinary run has nothing to pace
    assert waits == []


def test_one_organization_failing_does_not_stop_the_others():
    dash, store = Dashboard([('1', 'Broken'), ('2', 'Org')], broken={'1'}), memory()
    (new, read, failed), lines = run(dash, store)
    assert (new, read, failed) == (5, 1, 1)
    assert lines == ['Broken: failed: RuntimeError: 404 Not Found', 'Org: 5 new readings']


def test_an_interrupted_catch_up_keeps_what_it_stored_and_carries_on_next_run():
    dash, store = Dashboard([('1', 'Org')]), memory()
    run(dash, store)
    dash.organizations.fail = lambda t0: TimeoutError('no answer') if t0 >= NOW + 480 else None
    (new, read, failed), lines = run(dash, store, now=NOW + 1200)
    assert (new, read, failed) == (15, 1, 0)                  # the latest five minutes and the two slices that worked
    assert lines[0].startswith('Org: stopped filling the gap at ' + collector.iso(NOW + 480))
    assert store.mark('org:1') == NOW + 480                   # where the next run starts
    dash.organizations.fail = None
    run(dash, store, now=NOW + 1440)
    assert minutes(store) == list(range(29))


def test_a_slice_meraki_refuses_is_skipped():
    dash, store = Dashboard([('1', 'Org')]), memory()
    run(dash, store)
    dash.organizations.fail = lambda t0: Refused(400) if t0 == NOW + 180 else None
    (new, _, failed), lines = run(dash, store, now=NOW + 1200)
    assert (new, failed) == (15, 0) and lines == ['Org: 15 new readings, 3 earlier slices']
    assert store.mark('org:1') == NOW + 1200 - LAG


def test_when_meraki_refuses_every_slice_the_gap_is_given_up():
    dash, store = Dashboard([('1', 'Org')]), memory()
    run(dash, store)
    dash.organizations.fail = lambda t0: Refused(400)
    (new, read, failed), lines = run(dash, store, now=NOW + 86400)
    assert (new, read, failed) == (5, 1, 0) and 'Meraki refused it' in lines[0]
    assert len([kw for _, kw in dash.organizations.calls if 't0' in kw]) == 3
    assert store.mark('org:1') == NOW + 86400 - LAG


def test_a_rate_limit_is_not_a_refusal():
    dash, store = Dashboard([('1', 'Org')]), memory()
    run(dash, store)
    dash.organizations.fail = lambda t0: Refused(429)
    _, lines = run(dash, store, now=NOW + 1200)
    assert 'the next run carries on' in lines[0] and store.mark('org:1') == NOW - LAG


def test_only_the_named_organizations_are_read():
    dash, store = Dashboard([('1', 'One'), ('2', 'Two')]), memory()
    (_, read, _), lines = run(dash, store, org_ids=['2'])
    assert read == 1 and lines == ['Two: 5 new readings']


def test_old_readings_are_removed_once_a_day():
    dash, store = Dashboard([('1', 'Org')]), memory()
    run(dash, store, now=NOW - 40 * 86400)
    _, lines = run(dash, store, keep_days=30, max_slices=0)
    assert lines[-1] == 'removed 5 readings older than 30 days'
    assert len(store.load()[0].ts) == 5 and store.mark('pruned') == NOW
