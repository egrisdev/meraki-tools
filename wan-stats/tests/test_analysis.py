import math

import numpy as np

from conftest import MIDNIGHT, series
from wanstats import analysis as A
from wanstats.analysis import DAY, Settings


def noisy(n, level, sd=0.3, seed=1):
    return level + np.random.default_rng(seed).normal(0, sd, n)


# ---------------------------------------------------------------- daily summary

def test_daily_gives_one_median_and_p95_per_utc_day():
    x = series(minutes=3 * 1440)
    x.latency[:1440] = np.arange(1440) / 10.0          # day one: 0.0 to 143.9
    days, med, p95 = A.daily(x.ts, x.latency)
    assert list(days) == [MIDNIGHT // DAY + k for k in range(3)]
    assert med[0] == np.median(np.arange(1440) / 10.0) and med[1] == 10.0
    assert math.isclose(p95[0], np.percentile(np.arange(1440) / 10.0, 95))


def test_daily_leaves_out_a_day_with_too_few_readings_and_ignores_empty_ones():
    x = series(minutes=3 * 1440)
    x.latency[1440:1440 + 1200] = np.nan                # day two keeps only 240 readings
    x.latency[5] = np.nan
    days, med, _ = A.daily(x.ts, x.latency)
    assert list(days) == [MIDNIGHT // DAY, MIDNIGHT // DAY + 2]
    assert med[0] == 10.0


# ---------------------------------------------------------------- level shifts

def test_a_step_is_found_on_the_day_it_happened():
    x = noisy(200, 10)
    x[120:] += 8
    assert A.find_shifts(x) == [120]


def test_an_improvement_is_found_as_readily_as_a_slowdown():
    x = noisy(200, 30)
    x[70:] -= 12
    assert A.find_shifts(x) == [70]


def test_a_step_below_three_ms_is_not_a_shift():
    x = noisy(200, 10)
    x[120:] += 2
    assert A.find_shifts(x) == []


def test_a_step_below_ten_percent_is_not_a_shift():
    x = noisy(200, 80)
    x[120:] += 5
    assert A.find_shifts(x) == []


def test_a_noisy_link_needs_a_bigger_step():
    x = noisy(200, 40, sd=3.0)
    x[120:] += 5                                         # 5 ms is over 3 ms and 10%, but under four times the noise
    assert A.find_shifts(x) == []


def test_a_spike_of_a_few_days_is_not_a_shift():
    x = noisy(200, 10)
    x[100:104] += 40
    assert A.find_shifts(x) == []


def test_two_shifts_are_both_dated():
    x = noisy(300, 10)
    x[90:] += 9
    x[200:] -= 9
    assert A.find_shifts(x) == [90, 200]


def test_too_little_history_finds_nothing():
    assert A.find_shifts(noisy(20, 10)) == []


# ---------------------------------------------------------------- trend test

def test_theil_sen_slope_and_mann_kendall_p():
    slope, p = A.theil_sen_mk(0.5 * np.arange(20))
    assert math.isclose(slope, 0.5) and p < 1e-6
    slope, p = A.theil_sen_mk(noisy(30, 10))
    assert abs(slope) < 0.05 and p > 0.001


def test_trend_needs_eight_values():
    assert A.theil_sen_mk([1, 2, 3, 4, 5, 6, 7]) == (0.0, 1.0)


# ---------------------------------------------------------------- verdicts

def judged(x):
    return A.judge(np.arange(len(x)), np.asarray(x, dtype=float))


def test_a_large_lasting_step_is_worse():
    x = noisy(240, 4)
    x[150:] += 11
    r = judged(x)
    assert (r.verdict, r.why) == ('worse', 'shift')
    assert round(r.baseline) == 4 and round(r.current) == 15
    assert [(e.day, round(e.before), round(e.after)) for e in r.shifts] == [(150, 4, 15)]


def test_a_modest_step_is_drifting():
    x = noisy(240, 10)
    x[150:] += 4
    assert judged(x).verdict == 'drifting'


def test_both_thresholds_of_a_pair_must_be_met():
    x = noisy(240, 5)
    x[150:] += 4                                         # up 80%, but only 4 ms: Drifting, not Worse
    assert judged(x).verdict == 'drifting'
    assert A.size(5.0, 7.0) == 0                         # up 40%, but only 2 ms
    assert A.size(100.0, 109.0) == 0                     # up 9 ms, but only 9%
    assert A.size(4.0, 15.2) == 2


def test_a_link_that_got_faster_is_steady():
    x = noisy(240, 40)
    x[100:] -= 25
    r = judged(x)
    assert r.verdict == 'steady' and len(r.shifts) == 1


def test_the_baseline_is_the_lowest_level_held_for_thirty_days():
    x = noisy(260, 20)
    x[100:140] -= 12                                     # forty days at 8 ms: that is the baseline
    r = judged(x)
    assert round(r.baseline) == 8 and r.verdict == 'worse'
    y = noisy(260, 20)
    y[100:120] -= 12                                     # twenty days is a dip, not a baseline
    assert judged(y).verdict == 'steady'


def test_a_slow_rise_is_found_by_the_trend_test():
    x = noisy(300, 20)
    x += np.clip((np.arange(300) - 40) / 260.0, 0, 1) * 8
    r = judged(x)
    assert (r.verdict, r.why) == ('drifting', 'trend') and r.shifts == []
    assert 6 < r.gain < 9 and r.p < 0.001


def test_a_steady_link_is_steady():
    r = judged(noisy(300, 20))
    assert (r.verdict, r.why, r.shifts) == ('steady', '', [])


def test_a_link_with_little_history_is_new():
    assert judged(noisy(15, 20)).verdict == 'new'
    assert judged([]).verdict == 'new'


def test_a_link_takes_the_worse_of_its_two_verdicts():
    steady, worse = judged(noisy(240, 10)), None
    x = noisy(240, 10)
    x[150:] += 20
    worse = judged(x)
    v = A.Verdict(median=steady, p95=worse)
    assert v.word == 'worse' and v.flagged
    assert v.reason().startswith('p95 10.0 -> 30.0 ms')
    assert A.Verdict(median=steady, p95=steady).reason() == ''
    assert A.Verdict(median=judged([]), p95=judged([])).word == 'new'


def test_the_reason_names_the_day_of_the_last_rise():
    x = noisy(240, 4)
    x[150:] += 11
    r = A.judge(MIDNIGHT // DAY + np.arange(240), x)
    assert A.Verdict(r, judged(noisy(240, 5))).reason() == 'median 4.0 -> 15.0 ms on 13 Apr 2024'


# ---------------------------------------------------------------- outages

def down(x, start_minute, minutes):
    x.loss[start_minute:start_minute + minutes] = 100.0
    x.latency[start_minute:start_minute + minutes] = np.nan


def silent(x, start_minute, minutes):
    keep = np.ones(len(x.ts), dtype=bool)
    keep[start_minute:start_minute + minutes] = False
    x.ts, x.latency, x.loss = x.ts[keep], x.latency[keep], x.loss[keep]


def test_total_loss_is_an_outage():
    x = series()
    down(x, 1000, 10)
    a = A.analyse([x])
    assert a.links[0].outages == [(MIDNIGHT + 60000, MIDNIGHT + 60600)]


def test_an_outage_shorter_than_three_minutes_is_not_counted():
    x = series()
    down(x, 1000, 2)
    link = A.analyse([x]).links[0]
    assert link.outages == [] and len(link.down) == 1


def test_empty_readings_inside_an_outage_belong_to_it_and_alone_they_are_nothing():
    x = series()
    down(x, 1000, 10)
    x.loss[1003:1006] = np.nan                           # the link reported nothing useful for three minutes
    x.loss[2000:2010] = np.nan                           # empty readings with no loss around them
    x.latency[2000:2010] = np.nan
    assert A.analyse([x]).links[0].outages == [(MIDNIGHT + 60000, MIDNIGHT + 60600)]


def test_outages_a_few_minutes_apart_are_one():
    x = series()
    down(x, 1000, 10)
    down(x, 1013, 10)
    assert len(A.analyse([x]).links[0].outages) == 1


def test_a_link_that_goes_silent_is_down():
    links = [series(name) for name in ('Alpha', 'Bravo', 'Charlie', 'Delta')]
    silent(links[0], 1000, 30)
    a = A.analyse(links)
    assert a.find('Alpha', 'wan1').outages == [(MIDNIGHT + 60000, MIDNIGHT + 61800)]
    assert a.find('Bravo', 'wan1').outages == [] and a.gaps == {'Org': []}


def test_a_silence_most_of_the_organization_shares_is_the_collector():
    links = [series(name) for name in ('Alpha', 'Bravo', 'Charlie', 'Delta')]
    for x in links[:3]:
        silent(x, 1000, 30)
    a = A.analyse(links)
    assert a.gaps == {'Org': [(MIDNIGHT + 60000, MIDNIGHT + 61800)]}
    assert all(k.outages == [] for k in a.links)
    assert a.checks()['collector_gaps'] == 1


def test_an_outage_is_not_hidden_by_a_collector_gap_inside_it():
    links = [series(name, minutes=6000) for name in ('Alpha', 'Bravo', 'Charlie', 'Delta')]
    silent(links[0], 1000, 2880)                         # Alpha is down for two days ...
    for x in links[1:]:
        silent(x, 2425 - 2880 * (x is links[0]), 30)     # ... and the collector misses half an hour in the middle
    a = A.analyse(links)
    assert a.gaps == {'Org': [(MIDNIGHT + 2425 * 60, MIDNIGHT + 2455 * 60)]}
    assert a.find('Alpha', 'wan1').outages == [(MIDNIGHT + 60000, MIDNIGHT + 3880 * 60)]
    assert all(k.outages == [] for k in a.links if k.name != 'Alpha')


def test_a_link_still_down_after_a_collector_gap_is_down_for_the_rest():
    links = [series(name) for name in ('Alpha', 'Bravo', 'Charlie', 'Delta')]
    for x in links:
        silent(x, 1000, 70 if x is links[0] else 30)     # all four go quiet, and Alpha stays quiet 40 minutes longer
    a = A.analyse(links)
    assert a.gaps == {'Org': [(MIDNIGHT + 60000, MIDNIGHT + 61800)]}
    assert a.find('Alpha', 'wan1').outages == [(MIDNIGHT + 61800, MIDNIGHT + 64200)]


def test_links_out_of_step_by_a_minute_still_share_a_collector_gap():
    links = [series(name, start=MIDNIGHT + 7 * n) for n, name in enumerate(('Alpha', 'Bravo', 'Charlie', 'Delta'))]
    for n, x in enumerate(links):
        silent(x, 1000 + n % 2, 30)                      # each link loses the same half hour, give or take a minute
    a = A.analyse(links)
    assert len(a.gaps['Org']) == 1 and all(k.outages == [] for k in a.links)


def test_another_organization_does_not_hide_a_collector_gap():
    ours = [series(name) for name in ('Alpha', 'Bravo', 'Charlie')]
    theirs = [series(name, org='Other') for name in ('Xray', 'Yankee', 'Zulu', 'Whiskey', 'Victor')]
    for x in ours:
        silent(x, 1000, 30)
    a = A.analyse(ours + theirs)
    assert len(a.gaps['Org']) == 1 and a.gaps['Other'] == []
    assert all(k.outages == [] for k in a.links)


def test_a_link_that_stopped_reporting_is_down_until_now():
    links = [series(name) for name in ('Alpha', 'Bravo', 'Charlie', 'Delta')]
    silent(links[0], 2880, 120)                          # Alpha's last two hours are missing
    a = A.analyse(links)
    alpha = a.find('Alpha', 'wan1')
    assert alpha.live and alpha.outages == [(MIDNIGHT + 2880 * 60, a.end)]


def test_a_link_silent_for_a_month_was_removed():
    old = series('Old', minutes=20 * 1440)
    new = series('New', minutes=60 * 1440)
    a = A.analyse([old, new])
    assert not a.find('Old', 'wan1').live and a.find('Old', 'wan1').outages == []
    assert a.find('New', 'wan1').live


def test_a_short_leftover_series_is_dropped():
    old = series('Old', minutes=500)
    new = series('New', minutes=60 * 1440)
    assert [k.name for k in A.analyse([old, new]).links] == ['New']


def test_a_replaced_device_keeps_its_history():
    first = series('Alpha', minutes=5000, serial='S-1')
    second = series('Alpha', minutes=5000, start=MIDNIGHT + 5010 * 60, serial='S-2')
    a = A.analyse([second, first])
    assert len(a.links) == 1 and len(a.links[0].series.ts) == 10000 and a.links[0].series.serial == 'S-2'
    assert np.all(np.diff(a.links[0].series.ts) > 0)


def test_two_devices_reporting_at_once_stay_separate_and_are_told_apart():
    a = A.analyse([series('Alpha', serial='S-1'), series('Alpha', serial='S-2')])
    assert [k.label for k in a.links] == ['Alpha wan1 on S-1', 'Alpha wan1 on S-2']
    assert a.find('Alpha', 'wan1').port == 'wan1'


def test_two_test_addresses_on_one_uplink_are_told_apart():
    a = A.analyse([series('Alpha', ip='8.8.8.8'), series('Alpha', ip='1.1.1.1'), series('Bravo')])
    assert [k.label for k in a.links] == ['Alpha wan1 to 1.1.1.1', 'Alpha wan1 to 8.8.8.8', 'Bravo wan1']


# ---------------------------------------------------------------- summary and checks

def test_summary_of_a_period():
    x = series(minutes=2 * 1440)
    x.latency[1440:] = 20.0
    x.latency[1500:1510] = 400.0                         # ten slow minutes
    x.loss[1600:1620] = 5.0                              # twenty lossy minutes
    down(x, 2000, 144)                                   # and 144 minutes down
    link = A.analyse([x]).links[0]
    s = link.summary(MIDNIGHT + DAY, MIDNIGHT + 2 * DAY)
    assert s.readings == 1440 and s.median == 20.0 and s.outages == 1 and s.down_seconds == 144 * 60
    assert math.isclose(s.bad_percent, 100 * (10 + 20 + 144) / 1440)
    assert math.isclose(s.availability, 90.0)
    assert link.summary(MIDNIGHT, MIDNIGHT + DAY).outages == 0


def test_summary_of_a_period_with_no_readings():
    s = A.analyse([series()]).links[0].summary(MIDNIGHT - DAY, MIDNIGHT)
    assert s.readings == 0 and math.isnan(s.median) and math.isnan(s.bad_percent)


def test_data_checks():
    x = series(minutes=1000)
    x.latency[:10] = np.nan
    x.loss[:10] = np.nan
    x.loss[100:150] = 1.7
    x.loss[150:170] = 3.3
    silent(x, 500, 100)
    c = A.analyse([x]).checks()
    assert (c['readings'], c['links'], c['sites'], c['organizations']) == (900, 1, 1, 1)
    assert math.isclose(c['coverage'], 90.0) and math.isclose(c['empty'], 100 * 10 / 900)
    assert math.isclose(c['any_loss'], 100 * 70 / 900) and math.isclose(c['bad_loss'], 100 * 20 / 900)


def test_settings_change_the_verdict():
    x = noisy(240, 10)
    x[150:] += 4
    days = np.arange(240)
    assert A.judge(days, x, Settings(drifting=(1.5, 5.0))).verdict == 'steady'


def test_nothing_stored_is_an_empty_analysis():
    a = A.analyse([])
    assert a.links == [] and a.flagged == []
