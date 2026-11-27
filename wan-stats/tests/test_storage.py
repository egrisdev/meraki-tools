import datetime
import math
import os

import pytest

from wanstats.storage import MongoStore, PostgresStore, SQLiteStore, open_store

# PostgreSQL and MongoDB are tested when these name a scratch database, as the Build workflow does.
POSTGRES = os.getenv('WAN_STATS_TEST_POSTGRES')
MONGODB = os.getenv('WAN_STATS_TEST_MONGODB')
T = 1_800_000_000


def row(ts=T, serial='Q2AA-AAAA-AAAA', uplink='wan1', ip='8.8.8.8', latency=12.5, loss=0.0, name='Harbor',
        org='Org', network='N_1'):
    return {'organizationName': org, 'networkId': network, 'serial': serial, 'deviceName': name, 'uplink': uplink,
            'ip': ip, 'ts': ts, 'lossPercent': loss, 'latencyMs': latency}


@pytest.fixture(params=['sqlite'] + ['postgresql'] * bool(POSTGRES) + ['mongodb'] * bool(MONGODB))
def store(request, tmp_path):
    if request.param == 'sqlite':
        s = open_store('sqlite:///' + str(tmp_path / 'test.sqlite'))
    elif request.param == 'postgresql':
        s = open_store(POSTGRES)
        s._run('TRUNCATE readings, uplinks, collector_state RESTART IDENTITY')
        s.db.commit()
    else:
        s = open_store(MONGODB)
        s.data.delete_many({})
        s.state.delete_many({})
    yield s
    s.close()


def test_add_returns_how_many_readings_were_new(store):
    assert store.add([row(T), row(T + 60)]) == 2
    assert store.add([row(T + 60), row(T + 120)]) == 1
    assert store.add([row(T)]) == 0
    assert store.add([]) == 0


def test_load_returns_each_link_in_time_order(store):
    store.add([row(T + 120, latency=3.0), row(T, latency=1.0), row(T + 60, latency=2.0),
               row(T, uplink='wan2', latency=40.0, loss=1.7)])
    wan1, wan2 = sorted(store.load(), key=lambda x: x.uplink)
    assert (wan1.org, wan1.network, wan1.serial, wan1.name, wan1.uplink, wan1.ip) == (
        'Org', 'N_1', 'Q2AA-AAAA-AAAA', 'Harbor', 'wan1', '8.8.8.8')
    assert wan1.ts.tolist() == [T, T + 60, T + 120] and wan1.latency.tolist() == [1.0, 2.0, 3.0]
    assert wan1.ts.dtype.kind == 'i' and wan1.latency.dtype.kind == 'f'
    assert wan2.latency.tolist() == [40.0] and wan2.loss.tolist() == [1.7]


def test_an_empty_reading_comes_back_as_nan(store):
    store.add([row(T, latency=None, loss=None), row(T + 60)])
    x, = store.load()
    assert math.isnan(x.latency[0]) and math.isnan(x.loss[0]) and x.latency[1] == 12.5


def test_each_test_address_is_its_own_series(store):
    store.add([row(T, ip='8.8.8.8'), row(T, ip='1.1.1.1')])
    assert sorted(x.ip for x in store.load()) == ['1.1.1.1', '8.8.8.8']


def test_a_renamed_device_shows_its_new_name(store):
    store.add([row(T, name='Old name')])
    store.add([row(T + 60, name='New name')])
    x, = store.load()
    assert x.name == 'New name' and len(x.ts) == 2


def test_a_moved_device_shows_its_new_network_and_organization(store):
    store.add([row(T)])
    store.add([row(T + 60, network='N_2', org='Other')])
    x, = store.load()
    assert (x.network, x.org, len(x.ts)) == ('N_2', 'Other', 2)


def test_a_write_that_fails_leaves_nothing_behind_and_the_next_one_works(store):
    with pytest.raises(ValueError):
        store.add([row(T), row('not a time', serial='Q2CC-CCCC-CCCC')])
    assert store.load() == []
    assert store.add([row(T)]) == 1 and store.mark('org:1') is None


@pytest.mark.skipif(not POSTGRES, reason='needs WAN_STATS_TEST_POSTGRES')
def test_postgresql_recovers_from_a_statement_it_refused():
    s = open_store(POSTGRES)
    s._run('TRUNCATE readings, uplinks, collector_state RESTART IDENTITY')
    s.db.commit()
    with pytest.raises(Exception):
        s.add([row(T, latency='not a number')])
    assert s.add([row(T)]) == 1
    for k in range(1, 50):                                    # a link that is already known uses up no identity value
        s.add([row(T + 60 * k)])
    s.add([row(T, serial='Q2CC-CCCC-CCCC')])
    assert sorted(i for i, in s._run('SELECT id FROM uplinks')) == [2, 3]
    s.close()


def test_a_database_that_cannot_be_reached_does_not_echo_its_password():
    from wanstats import storage
    assert 'password' in storage.CANNOT_CONNECT and '%s' in storage.CANNOT_CONNECT
    pytest.importorskip('psycopg')
    with pytest.raises(ConnectionError) as e:
        open_store('postgresql://user:S3cr%et!x@127.0.0.1:1/nowhere')
    assert 'S3cr' not in str(e.value) and e.value.__cause__ is None and e.value.__suppress_context__


def test_load_since_leaves_out_older_readings(store):
    store.add([row(T + 60 * k) for k in range(10)])
    x, = store.load(since=T + 300)
    assert x.ts.tolist() == [T + 60 * k for k in range(5, 10)]
    assert store.load(since=T + 6000) == []


def test_marks_are_remembered(store):
    assert store.mark('org:1') is None
    store.set_mark('org:1', T)
    store.set_mark('org:1', T + 240)
    store.set_mark('pruned', 7)
    assert store.mark('org:1') == T + 240 and store.mark('pruned') == 7


def test_prune_deletes_older_readings(store):
    store.add([row(T + 60 * k) for k in range(10)])
    assert store.prune(T + 300) == 5
    assert store.load()[0].ts.tolist() == [T + 60 * k for k in range(5, 10)]


def test_nothing_stored_loads_nothing(store):
    assert store.load() == []


def test_the_store_survives_being_reopened(tmp_path):
    url = 'sqlite:///' + str(tmp_path / 'again.sqlite')
    first = open_store(url)
    first.add([row(T)])
    first.set_mark('org:1', T)
    first.close()
    second = open_store(url)
    assert second.add([row(T), row(T + 60)]) == 1 and second.mark('org:1') == T
    second.close()


def test_a_relative_sqlite_path_is_taken_from_the_base_folder(tmp_path):
    open_store('sqlite:///wan.sqlite', base=str(tmp_path)).close()
    assert (tmp_path / 'wan.sqlite').exists()


def test_a_report_does_not_create_a_missing_database(tmp_path):
    with pytest.raises(FileNotFoundError, match='Run collect.py first'):
        open_store('sqlite:///typo.sqlite', base=str(tmp_path), create=False)
    assert not (tmp_path / 'typo.sqlite').exists()
    open_store('sqlite:///:memory:', create=False).close()


def test_the_url_picks_the_database():
    assert isinstance(open_store('sqlite:///:memory:'), SQLiteStore)
    assert PostgresStore.mark_style == '%s' and MongoStore.key == ('networkId', 'serial', 'uplink', 'ip')
    with pytest.raises(ValueError, match='unknown database'):
        open_store('mysql://localhost/wan')
    with pytest.raises(ValueError, match='sqlite:///'):
        open_store('sqlite://wan.sqlite')


@pytest.mark.skipif(not MONGODB, reason='needs WAN_STATS_TEST_MONGODB')
def test_mongodb_reads_documents_as_the_collector_writes_them():
    """`ts` is a date, and a document with no uplink (a device that reported nothing) is not a reading."""
    s = open_store(MONGODB)
    s.data.delete_many({})
    when = datetime.datetime(2027, 1, 15, 8, 0, 0)
    doc = {'organizationName': 'Org', 'networkId': 'N_1', 'serial': 'Q2AA-AAAA-AAAA', 'deviceName': 'Harbor',
           'uplink': 'wan1', 'ip': '8.8.8.8', 'lossPercent': 0.0, 'latencyMs': 12.5}
    s.data.insert_many([dict(doc, ts=when), dict(doc, ts=when + datetime.timedelta(minutes=1), latencyMs=None),
                        dict(doc, ts=when, uplink=None, ip=None, lossPercent=None, latencyMs=None)])
    x, = s.load()
    assert x.ts.tolist() == [T, T + 60] and x.latency[0] == 12.5 and math.isnan(x.latency[1])
    assert s.add([row(T), row(T + 120)]) == 1                 # the same reading, written again, is skipped
    s.close()
