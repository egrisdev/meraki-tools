import json
import os

from conftest import series
from wanstats import analysis, notebook

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_tables_are_markdown(demo):
    t = notebook.table(['Name', 'Value'], [['a', 1], ['b', 2]], right=['Value'])
    assert t == '| Name | Value |\n|---|---:|\n| a | 1 |\n| b | 2 |' and t._repr_markdown_() == t


def test_checks_table(demo):
    text = notebook.checks(demo)
    assert '| Readings | 1,511,230 |' in text and '| Links, sites, organizations | 7, 6, 1 |' in text
    assert '1, 1 hours in all' in text


def test_flagged_lines(demo):
    lines = notebook.flagged(demo).splitlines()
    assert lines[0].startswith('- **Harbor wan1: Worse.** Median 6.0 to 17.0 ms on ')
    assert lines[1].startswith('- **Quarry wan1: Drifting.** Median ')
    assert 'No link is flagged' in notebook.flagged(analysis.analyse([series()]))


def test_summary_has_a_row_per_live_link_and_marks_the_flagged_ones(demo):
    rows = notebook.summary(demo).splitlines()
    assert rows[0] == '**Example Org**' and len(rows) == 2 + 2 + 7
    harbor = next(r for r in rows if '| Harbor |' in r)
    assert harbor.endswith('| **WORSE** |') and '| 17.0 | 17.7 | 0.7 |' in harbor


def test_shift_and_outage_tables(demo):
    s = notebook.shifts(demo).splitlines()
    assert s[2].startswith('| 5 Sep 2024 | Orchard wan1 | 48.0 ms | 18.0 ms | Faster by 30.0 ms |')
    assert s[3].startswith('| 12 Oct 2024 | Harbor wan1 | 6.0 ms | 17.0 ms | Slower by 11.0 ms |')
    o = notebook.outages(demo, top=2).splitlines()
    assert len(o) == 4 and o[2].startswith('| Ferry | wan1 | 16 Dec 2024, 12:00 | 11 h 40 min |')
    calm = analysis.analyse([series()])
    assert notebook.shifts(calm) == 'No level shift was found.' and notebook.outages(calm) == 'No outage was found.'


def test_dual_sites_lists_a_two_uplink_site_with_a_recent_outage(demo):
    assert notebook.dual_sites(demo) == [('Example Org', 'Ferry')]
    assert notebook.dual_sites(demo, days=1) == [] and notebook.dual_sites(demo, at_least=86400) == []


def test_frame_is_a_dataframe_of_one_link(demo):
    df = notebook.frame(demo.find('Depot', 'wan1'))
    assert list(df.columns) == ['time', 'latency_ms', 'loss_percent'] and len(df) > 200000
    assert str(df['time'].dt.tz) == 'UTC'


def test_show_leaves_out_a_chart_with_nothing_to_draw(monkeypatch):
    import IPython.display
    shown = []
    monkeypatch.setattr(IPython.display, 'display', shown.append)
    notebook.show(None, notebook.Table('a table'), None)
    assert shown == ['a table']


def test_the_notebook_file_is_valid_and_calls_only_what_exists():
    with open(os.path.join(HERE, 'wan-stats.ipynb')) as f:
        nb = json.load(f)
    code = '\n'.join(''.join(c['source']) for c in nb['cells'] if c['cell_type'] == 'code')
    assert nb['nbformat'] == 4 and 'config.open_db(DB, create=False)' in code
    assert 'display(' not in code                             # every chart goes through notebook.show
    for name in ('checks', 'flagged', 'summary', 'shifts', 'outages', 'dual_sites', 'frame', 'show'):
        assert 'notebook.%s(' % name in code and hasattr(notebook, name)
    from wanstats import charts
    for name in ('fleet', 'flagged', 'shifts', 'downtime', 'site', 'boxes'):
        assert 'charts.%s(' % name in code and hasattr(charts, name)


def test_a_name_from_meraki_is_shown_as_plain_text():
    odd = series('<b>HQ</b> | $x^2$', minutes=40 * 1440)
    odd.latency[30 * 1440:] += 40
    a = analysis.analyse([odd])
    text = notebook.summary(a)
    assert '| &lt;b&gt;HQ&lt;/b&gt; \\| \\$x^2\\$ |' in text and text.startswith('**Org**')
    assert '<b>' not in notebook.flagged(a) and '<b>' not in notebook.shifts(a) + notebook.outages(a)
    from wanstats import charts
    for chart in (charts.fleet(a), charts.boxes(a), charts.shifts(a), charts.flagged(a, a.links[0])):
        assert chart.png(dpi=40).startswith(b'\x89PNG')


def test_a_name_cannot_smuggle_in_a_picture_or_a_link():
    assert notebook.plain('![x](https://example.com/p.png)') == '\\!\\[x\\](https://example.com/p.png)'
    assert notebook.plain('a_b *c* `d` ~e~') == 'a\\_b \\*c\\* \\`d\\` \\~e\\~'
    assert notebook.plain('HQ-1 (north), 2.5') == 'HQ-1 (north), 2.5'


def test_two_test_addresses_on_one_uplink_are_not_a_dual_site():
    one = series('Alpha', ip='8.8.8.8', minutes=3000)
    two = series('Alpha', ip='1.1.1.1', minutes=3000)
    for x in (one, two):
        x.loss[2000:2100] = 100.0
    a = analysis.analyse([one, two])
    assert notebook.dual_sites(a) == []
    assert '| Alpha | wan1 to 1.1.1.1 |' in notebook.outages(a)


def test_the_site_chart_keeps_to_one_organization_and_one_line_per_uplink():
    from wanstats import charts
    links = [series('Alpha', org='One', uplink='wan1'), series('Alpha', org='One', uplink='wan2', level=30.0),
             series('Alpha', org='Two', uplink='wan1', level=50.0)]
    links[0].loss[2000:2100] = 100.0
    a = analysis.analyse(links)
    assert notebook.dual_sites(a) == [('One', 'Alpha')]
    assert charts.site(a, 'Alpha', org='One').fig.texts[0].get_text().startswith('Alpha wan1 and wan2:')
    assert charts.site(a, 'Alpha', org='Two').fig.texts[0].get_text().startswith('Alpha wan1:')
