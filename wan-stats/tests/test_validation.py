import numpy as np

from conftest import MIDNIGHT, series
from wanstats import validation
from wanstats.analysis import DAY, Analysis, Link, Settings, Verdict, judge


def link(name, x, p95=None):
    """A link built from daily values alone, which is all the validation looks at."""
    days = MIDNIGHT // DAY + np.arange(len(x))
    p95 = x + 1.0 if p95 is None else p95
    return Link(series=series(name, minutes=10), days=days, median=x, p95=p95,
                verdict=Verdict(judge(days, x), judge(days, p95)), down=[], live=True)


def fleet(*links):
    return Analysis(list(links), {}, MIDNIGHT, MIDNIGHT + 380 * DAY, Settings())


def noisy(level, seed, n=380):
    return level + np.random.default_rng(seed).normal(0, 0.3, n)


def test_backtest_flags_a_step_only_after_it_happened():
    x = noisy(5, 1)
    x[250:] += 12
    a = fleet(link('Stepped', x), link('Calm', noisy(20, 2)))
    history = validation.backtest(a)
    assert [day for day, _ in history] == [MIDNIGHT // DAY + 379 - 30 * k for k in range(8, -1, -1)]
    said = [[(k.name, word) for k, word in flags] for _, flags in history]
    assert said[:4] == [[], [], [], []] and said[5:] == [[('Stepped', 'worse')]] * 4


def test_backtest_skips_a_link_that_did_not_exist_yet():
    late = link('Late', noisy(5, 1, n=50))
    late.days = late.days + 330
    assert all(flags == [] for _, flags in validation.backtest(fleet(late)))


def test_steady_links_leave_out_anything_that_moved():
    stepped = noisy(5, 1)
    stepped[250:] += 12
    a = fleet(link('Stepped', stepped), link('Calm', noisy(20, 2)), link('Short', noisy(20, 3, n=200)))
    assert [k.name for k in validation.steady_links(a, validation.backtest(a))] == ['Calm']


def test_injection_finds_large_steps_on_their_day_and_ignores_small_ones():
    a = fleet(link('Calm', noisy(20, 2)), link('Quiet', noisy(8, 3)))
    r = validation.inject(a, a.links, sizes=(2, 10), starts=(100, 200), rises=(1, 10), ramp_starts=(100,))
    big, small = r['steps'][10], r['steps'][2]
    assert (big['runs'], big['found'], big['flagged']) == (4, 4, 4) and big['errors'] == [0, 0, 0, 0]
    assert (small['runs'], small['found'], small['flagged']) == (4, 0, 0)
    assert r['ramps'][10]['flagged'] == 2 and r['ramps'][10]['why'] == {'trend': 2}
    assert r['ramps'][1]['flagged'] == 0
    assert all(5 <= d <= 14 for d in r['delays']) and r['links'] == ['Calm wan1', 'Quiet wan1']


def test_sensitivity_tries_every_pair_of_thresholds():
    x = noisy(10, 1)
    x[250:] += 6
    rows = validation.sensitivity(fleet(link('Stepped', x)))
    assert len(rows) == 9
    by = {(w, d): (worse, drifting) for w, d, worse, drifting in rows}
    assert by[((1.5, 10.0), (1.10, 3.0))] == ([], ['Stepped wan1'])
    assert by[((1.3, 5.0), (1.10, 3.0))] == (['Stepped wan1'], [])
    assert by[((2.0, 15.0), (1.20, 5.0))] == ([], ['Stepped wan1'])


def test_monthly_rule_flags_a_link_whose_latest_month_is_far_above_its_best():
    slow = series('Slow', minutes=300 * 1440)
    slow.latency[200 * 1440:] = 30.0
    calm = series('Calm', minutes=300 * 1440)
    from wanstats import analysis
    flagged = validation.monthly_rule(analysis.analyse([slow, calm]))
    assert [[k.name for k in month] for month in flagged][-3:] == [['Slow']] * 3
    assert flagged[0] == []
