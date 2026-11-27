"""Where the readings are kept: SQLite (the default), PostgreSQL or MongoDB.

`open_store(url)` returns a store for any of the three. Each has the same methods:

    add(rows)        write readings, skip the ones already stored, return how many were new
    load(since)      every series, as `analysis.Series`, with readings in time order
    mark(name)       a number the collector saved earlier (or None)
    set_mark(...)    save one
    prune(before)    delete readings older than a time
    close()

A row is a dict with the fields the Meraki API uses:
organizationName, networkId, serial, deviceName, uplink, ip, ts, lossPercent, latencyMs.
`ts` is whole seconds since 1970 UTC.
"""
from __future__ import annotations

import contextlib
import datetime
import os
import sqlite3

import numpy as np

from .analysis import Series

UTC = datetime.timezone.utc
EPOCH = datetime.datetime(1970, 1, 1)
CANNOT_CONNECT = ('could not connect to %s (%s). Check the host, the database name, the user and the password in '
                  'WAN_STATS_DB. Special characters in a password must be percent-encoded.')
SECOND = datetime.timedelta(seconds=1)


def open_store(url, base='.', create=True):
    """Open the database a URL names. A relative SQLite path is taken from `base`.

    With `create` off, a SQLite file that is not there is an error instead of a new, empty database.
    """
    scheme = url.split(':', 1)[0].lower()
    if scheme == 'sqlite':
        if not url.startswith('sqlite:///'):
            raise ValueError('a SQLite URL looks like sqlite:///wan-stats.sqlite (or sqlite:////absolute/path)')
        path = url[len('sqlite:///'):]
        path = path if path == ':memory:' else os.path.join(base, path)
        if not create and path != ':memory:' and not os.path.exists(path):
            raise FileNotFoundError('there is no database at %s yet. Run collect.py first.' % path)
        return SQLiteStore(path)
    if scheme in ('postgresql', 'postgres'):
        return PostgresStore(url)
    if scheme in ('mongodb', 'mongodb+srv'):
        return MongoStore(url)
    raise ValueError('unknown database %r: use sqlite:///, postgresql:// or mongodb://' % scheme)


def _series(org, network, serial, name, uplink, ip, rows):
    """Rows of (ts, latency, loss), in time order, as a Series. None becomes nan."""
    a = np.array(rows, dtype=float).reshape(-1, 3)
    return Series(org=org or '', network=network or '', serial=serial, name=name or serial, uplink=uplink,
                  ip=ip or '', ts=a[:, 0].astype(np.int64), latency=a[:, 1].copy(), loss=a[:, 2].copy())


class _SQLStore:
    """SQLite and PostgreSQL share everything except how they connect and three words of SQL.

    Two tables: `uplinks` has one row per link (device, uplink, test address) with its current names,
    and `readings` has one narrow row per link per minute.
    """
    mark_style = '?'
    schema = ()

    def _run(self, sql, args=()):
        cur = self.db.cursor()
        cur.execute(sql.replace('?', self.mark_style), args)
        return cur

    def _many(self, sql, rows):
        cur = self.db.cursor()
        cur.executemany(sql.replace('?', self.mark_style), rows)
        return cur

    @contextlib.contextmanager
    def _transaction(self):
        """Commit what the block did, or undo all of it.

        The undo matters on PostgreSQL: after a failed statement it refuses every later one until
        the transaction is rolled back, so one bad run would otherwise stop the whole collector.
        """
        try:
            yield
        except BaseException:
            self.db.rollback()
            raise
        self.db.commit()

    def _create(self):
        with self._transaction():
            for statement in self.schema:
                self._run(statement)

    def _uplinks(self):
        return {(s, u, i): (n, o, w, d) for n, s, u, i, o, w, d in self._run(
            'SELECT id, serial, uplink, ip, organization, network_id, device_name FROM uplinks')}

    def add(self, rows):
        if not rows:
            return 0
        names = {(r['serial'], r['uplink'], r['ip']): (r['organizationName'], r['networkId'], r['deviceName'])
                 for r in rows}
        with self._transaction():
            have = self._uplinks()
            fresh = [k + v for k, v in names.items() if k not in have]
            if fresh:                                   # a link seen for the first time
                self._many('INSERT INTO uplinks (serial, uplink, ip, organization, network_id, device_name) '
                           'VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (serial, uplink, ip) DO NOTHING', fresh)
                have = self._uplinks()
            renamed = [v + (have[k][0],) for k, v in names.items() if have[k][1:] != v]
            if renamed:                                 # a device, network or organization was renamed or moved
                self._many('UPDATE uplinks SET organization = ?, network_id = ?, device_name = ? WHERE id = ?', renamed)
            return self._many(
                'INSERT INTO readings (uplink_id, ts, loss_percent, latency_ms) VALUES (?, ?, ?, ?) '
                'ON CONFLICT DO NOTHING',
                [(have[(r['serial'], r['uplink'], r['ip'])][0], int(r['ts']), r['lossPercent'], r['latencyMs'])
                 for r in rows]).rowcount

    def load(self, since=None):
        out = []
        with self._transaction():
            links = self._run('SELECT id, organization, network_id, serial, device_name, uplink, ip '
                              'FROM uplinks ORDER BY id').fetchall()
            for uplink_id, org, network, serial, name, uplink, ip in links:
                rows = self._run('SELECT ts, latency_ms, loss_percent FROM readings WHERE uplink_id = ? AND ts >= ? '
                                 'ORDER BY ts', (uplink_id, int(since or 0))).fetchall()
                if rows:
                    out.append(_series(org, network, serial, name, uplink, ip, rows))
        return out

    def mark(self, name):
        with self._transaction():
            row = self._run('SELECT value FROM collector_state WHERE name = ?', (name,)).fetchone()
        return row[0] if row else None

    def set_mark(self, name, value):
        with self._transaction():
            self._run('INSERT INTO collector_state (name, value) VALUES (?, ?) '
                      'ON CONFLICT (name) DO UPDATE SET value = excluded.value', (name, int(value)))

    def prune(self, before):
        with self._transaction():
            return self._run('DELETE FROM readings WHERE ts < ?', (int(before),)).rowcount

    def close(self):
        self.db.close()


class SQLiteStore(_SQLStore):
    schema = (
        'CREATE TABLE IF NOT EXISTS uplinks ('
        ' id INTEGER PRIMARY KEY, serial TEXT NOT NULL, uplink TEXT NOT NULL, ip TEXT NOT NULL,'
        ' organization TEXT, network_id TEXT, device_name TEXT, UNIQUE (serial, uplink, ip))',
        'CREATE TABLE IF NOT EXISTS readings ('
        ' uplink_id INTEGER NOT NULL REFERENCES uplinks (id), ts INTEGER NOT NULL,'
        ' loss_percent REAL, latency_ms REAL, PRIMARY KEY (uplink_id, ts)) WITHOUT ROWID',
        'CREATE TABLE IF NOT EXISTS collector_state (name TEXT PRIMARY KEY, value INTEGER NOT NULL)',
    )

    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=60)
        self.db.execute('PRAGMA journal_mode = WAL')     # a report can read while the collector writes
        self._create()


class PostgresStore(_SQLStore):
    mark_style = '%s'
    schema = (
        'CREATE TABLE IF NOT EXISTS uplinks ('
        ' id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, serial TEXT NOT NULL, uplink TEXT NOT NULL,'
        ' ip TEXT NOT NULL, organization TEXT, network_id TEXT, device_name TEXT, UNIQUE (serial, uplink, ip))',
        'CREATE TABLE IF NOT EXISTS readings ('
        ' uplink_id INTEGER NOT NULL REFERENCES uplinks (id), ts BIGINT NOT NULL,'
        ' loss_percent DOUBLE PRECISION, latency_ms DOUBLE PRECISION, PRIMARY KEY (uplink_id, ts))',
        'CREATE TABLE IF NOT EXISTS collector_state (name TEXT PRIMARY KEY, value BIGINT NOT NULL)',
    )

    def __init__(self, url):
        try:
            import psycopg
        except ImportError:
            raise SystemExit('PostgreSQL needs one more package: pip install "psycopg[binary]"')
        try:
            self.db = psycopg.connect(url)
        except psycopg.Error as e:
            # The driver's own message can quote part of the URL, password included. Say what to check instead.
            raise ConnectionError(CANNOT_CONNECT % ('PostgreSQL', type(e).__name__)) from None
        self._create()


class MongoStore:
    """One document per reading in the `uplink_data` collection, with the API's field names.

    `ts` is stored as a date. The unique index is what lets overlapping collector runs skip
    readings that are already there.
    """
    key = ('networkId', 'serial', 'uplink', 'ip')

    def __init__(self, url):
        try:
            import pymongo
        except ImportError:
            raise SystemExit('MongoDB needs one more package: pip install pymongo')
        self.pymongo = pymongo
        try:
            self.client = pymongo.MongoClient(url, serverSelectionTimeoutMS=15000)
            db = self.client.get_default_database(default='wan_stats')
            self.data, self.state = db['uplink_data'], db['collector_state']
            self.data.create_index([(k, 1) for k in self.key + ('ts',)], unique=True)
        except pymongo.errors.PyMongoError as e:
            raise ConnectionError(CANNOT_CONNECT % ('MongoDB', type(e).__name__)) from None

    @staticmethod
    def _date(ts):
        return datetime.datetime.fromtimestamp(int(ts), UTC).replace(tzinfo=None)

    def add(self, rows):
        if not rows:
            return 0
        docs = [dict(r, ts=self._date(r['ts'])) for r in rows]
        try:
            return len(self.data.insert_many(docs, ordered=False).inserted_ids)
        except self.pymongo.errors.BulkWriteError as e:
            if any(w.get('code') != 11000 for w in e.details.get('writeErrors', [])):   # 11000: already stored
                raise
            return e.details.get('nInserted', 0)

    def load(self, since=None):
        match = {'uplink': {'$ne': None}}
        if since:
            match['ts'] = {'$gte': self._date(since)}
        groups = self.data.aggregate(
            [{'$match': match}, {'$group': {'_id': {k: '$' + k for k in self.key}}}], allowDiskUse=True)
        links = {}
        for g in groups:                       # a device moved to another network is still the same link
            k = g['_id']
            links.setdefault((k.get('serial'), k.get('uplink'), k.get('ip')), []).append(k.get('networkId'))
        out = []
        for (serial, uplink, ip), networks in sorted(links.items(), key=lambda kv: [str(v) for v in kv[0]]):
            rows = []
            for network in networks:
                where = dict(match, networkId=network, serial=serial, uplink=uplink, ip=ip)
                found = self.data.find(where, {'_id': 0, 'ts': 1, 'latencyMs': 1, 'lossPercent': 1}).sort('ts', 1)
                rows += [((d['ts'] - EPOCH) // SECOND, d.get('latencyMs'), d.get('lossPercent')) for d in found]
            if len(networks) > 1:
                rows.sort(key=lambda r: r[0])
            last = self.data.find_one({'serial': serial, 'uplink': uplink, 'ip': ip, 'networkId': networks[-1]},
                                      sort=[('ts', -1)])
            out.append(_series(last.get('organizationName'), last.get('networkId'), serial,
                               last.get('deviceName'), uplink, ip, rows))
        return out

    def mark(self, name):
        doc = self.state.find_one({'_id': name})
        return doc['value'] if doc else None

    def set_mark(self, name, value):
        self.state.update_one({'_id': name}, {'$set': {'value': int(value)}}, upsert=True)

    def prune(self, before):
        return self.data.delete_many({'ts': {'$lt': self._date(before)}}).deleted_count

    def close(self):
        self.client.close()
