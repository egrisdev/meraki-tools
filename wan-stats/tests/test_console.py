import numpy as np

from conftest import MIDNIGHT, series
from wanstats import analysis, console


def test_the_demo_fleet_report(demo):
    text, status = console.render(demo, days=7)
    lines = text.splitlines()
    assert lines[0].startswith('WAN stats, 7 days to 2024-12-19 00:00 UTC') and lines[0].endswith('7 links, 2 flagged')
    assert lines[2].startswith('WORSE     Harbor wan1  median 6.0 -> 17.0 ms on ')
    assert lines[3].startswith('DRIFTING  Quarry wan1  median ')
    assert status == 2
    head = next(k for k in lines if k.startswith('Site'))
    harbor = next(k for k in lines if k.startswith('Harbor'))
    assert head.split() == ['Site', 'Link', 'Median', 'p95', 'Spread', 'Bad', 'min', 'Outages', 'Down', 'Avail',
                            'Latency']
    assert len(harbor) == len(head) - len('Latency') + len('WORSE')      # the columns line up
    assert harbor.split()[-1] == 'WORSE' and harbor.split()[2:4] == ['17.0', 'ms']
    ferry = next(k for k in lines if k.startswith('Ferry') and 'wan1' in k).split()
    assert ferry[-4:] == ['1', '11h40', '93.06%', 'steady']


def test_the_status_is_one_when_the_worst_link_is_drifting(demo):
    calm = analysis.Analysis([k for k in demo.links if k.name != 'Harbor'], demo.gaps, demo.start, demo.end,
                             demo.settings)
    text, status = console.render(calm)
    assert status == 1 and '6 links, 1 flagged' in text


def test_a_steady_fleet_has_status_zero_and_no_flag_lines():
    text, status = console.render(analysis.analyse([series('Alpha', minutes=40 * 1440)]))
    assert status == 0 and text.splitlines()[1] == '' and text.splitlines()[2] == 'Org'
    assert text.splitlines()[-1].split() == ['Alpha', 'wan1', '10.0', 'ms', '10.0', '0.0', '0.0%', '0', '-',
                                             '100.00%', 'steady']


def test_a_link_with_little_history_is_new_and_a_silent_one_shows_dashes():
    links = [series(name, minutes=12 * 1440) for name in ('Alpha', 'Bravo', 'Charlie', 'Delta')]
    quiet = links[0]
    keep = quiet.ts < MIDNIGHT + 2 * 86400                     # Alpha stopped reporting ten days ago
    quiet.ts, quiet.latency, quiet.loss = quiet.ts[keep], quiet.latency[keep], quiet.loss[keep]
    text, status = console.render(analysis.analyse(links))
    alpha = next(k for k in text.splitlines() if k.startswith('Alpha')).split()
    assert alpha == ['Alpha', 'wan1', '-', '-', '-', '-', '1', '168h00', '0.00%', 'new'] and status == 0


def test_a_removed_link_is_not_listed():
    old = series('Old', minutes=20 * 1440)
    new = series('New', minutes=60 * 1440)
    text, _ = console.render(analysis.analyse([old, new]))
    assert 'Old' not in text and '1 links, 0 flagged' in text


def test_two_test_addresses_are_told_apart():
    a = analysis.analyse([series('Alpha', ip='8.8.8.8'), series('Alpha', ip='1.1.1.1', level=30.0)])
    text, _ = console.render(a)
    assert 'wan1 to 1.1.1.1' in text and 'wan1 to 8.8.8.8' in text


def test_nothing_stored():
    assert console.render(analysis.analyse([])) == ('No readings yet. Run collect.py first.', 3)


def test_lengths_and_times():
    assert [console.length(s) for s in (170, 3540, 3600, 88440)] == ['3m', '59m', '1h00', '24h34']
    assert console.when(MIDNIGHT) == '2023-11-15 00:00'
    assert np.isnan(analysis.analyse([series()]).links[0].summary(0, 60).median)
