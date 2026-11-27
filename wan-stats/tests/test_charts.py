import pytest

from conftest import series
from wanstats import analysis, charts

PNG = b'\x89PNG\r\n\x1a\n'


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_every_chart_draws(demo, theme):
    harbor, quarry = demo.find('Harbor', 'wan1'), demo.find('Quarry', 'wan1')
    drawn = [charts.fleet(demo, theme), charts.shifts(demo, theme), charts.boxes(demo, theme),
             charts.downtime(demo, theme), charts.site(demo, 'Ferry', theme=theme),
             charts.levels(demo, harbor, theme=theme), charts.trend(demo, quarry, theme=theme)]
    for chart in drawn:
        assert chart.png(dpi=50).startswith(PNG)


def test_a_chart_shows_itself_in_a_notebook_and_saves(demo, tmp_path):
    chart = charts.boxes(demo)
    data, meta = chart._repr_png_()
    assert data.startswith(PNG) and meta == {'width': 860}
    chart.save(tmp_path / 'boxes.png', dpi=40)
    assert (tmp_path / 'boxes.png').read_bytes().startswith(PNG)


def test_the_flagged_chart_matches_the_reason(demo):
    harbor, quarry = demo.find('Harbor', 'wan1'), demo.find('Quarry', 'wan1')
    assert 'level shift' in charts.flagged(demo, harbor).fig.texts[0].get_text()
    assert 'trend' in charts.flagged(demo, quarry).fig.texts[0].get_text()


def test_shift_clusters_need_two_links(demo):
    assert charts.shift_clusters(demo) == []


def test_nothing_to_draw_gives_none():
    a = analysis.analyse([series('Alpha', minutes=40 * 1440)])
    assert charts.shifts(a) is None and charts.downtime(a) is None and charts.site(a, 'Nowhere') is None
    assert charts.fleet(analysis.analyse([])) is None and charts.boxes(analysis.analyse([])) is None
    assert charts.fleet(a).png(dpi=40).startswith(PNG)


def test_drawing_leaves_matplotlib_settings_alone(demo):
    import matplotlib
    before = matplotlib.rcParams['font.size']
    charts.boxes(demo)
    assert matplotlib.rcParams['font.size'] == before
