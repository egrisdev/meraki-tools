"""The four scripts, run the way a new user would: make the demo data, then report on it."""
import pytest

import collect
import demo
import report
import validate


@pytest.fixture(scope='module')
def url(tmp_path_factory):
    path = tmp_path_factory.mktemp('demo') / 'demo.sqlite'
    assert demo.main(['--db', 'sqlite:///%s' % path, '--days', '45']) == 0
    return 'sqlite:///%s' % path


def test_demo_refuses_to_overwrite(url):
    with pytest.raises(SystemExit, match='already there'):
        demo.main(['--db', url])


def test_report_prints_the_table_and_returns_the_status(url, capsys):
    assert report.main(['--db', url]) == 2
    out = capsys.readouterr().out
    assert out.startswith('WAN stats, 7 days to ') and 'WORSE     Harbor wan1' in out and '7 links, ' in out
    assert report.main(['--db', url, '--days', '30', '--history', '40']) in (0, 1, 2)
    assert 'WAN stats, 30 days to ' in capsys.readouterr().out


def test_report_on_a_missing_database_says_so(tmp_path, capsys):
    assert report.main(['--db', 'sqlite:///%s' % (tmp_path / 'typo.sqlite')]) == 3
    assert 'Run collect.py first' in capsys.readouterr().out and not (tmp_path / 'typo.sqlite').exists()


def test_validate_runs_on_a_short_history(url, capsys):
    assert validate.main(['--db', url]) == 0
    out = capsys.readouterr().out
    assert 'BACKTEST' in out and 'No link is steady enough' in out and 'SENSITIVITY' in out


def test_collect_needs_a_key(monkeypatch):
    monkeypatch.setenv('MERAKI_API_KEY', '')
    with pytest.raises(SystemExit, match='MERAKI_API_KEY is not set'):
        collect.main([])


def test_collect_lengths():
    assert [collect.span(x) for x in ('90m', '6h', '14d')] == [5400, 21600, 1209600]
    with pytest.raises(Exception):
        collect.span('soon')
