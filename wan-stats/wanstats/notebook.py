"""The tables the notebook shows. Each function returns Markdown, which Jupyter, a PDF export and GitHub all render."""
from __future__ import annotations

import datetime
import math
import re

from .analysis import DAY, short_date
from .console import WORDS, length, when


class Table(str):
    """Markdown text that a notebook renders as a table."""

    def _repr_markdown_(self):
        return str(self)


def plain(text):
    """A name that came from Meraki, as plain text.

    A device or an organization can be named anything. Its name must not turn into a link, a picture,
    a formula or a column break when the table is rendered, so HTML and Markdown marks are escaped.
    """
    text = str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return re.sub(r'([\\`*_\[\]~$!|])', r'\\\1', text)


def table(header, rows, right=()):
    """A Markdown table. Columns named in `right` are right-aligned, as numbers should be.

    Cells are used as they are, so a name from Meraki goes through `plain()` first.
    """
    lines = ['| ' + ' | '.join(header) + ' |',
             '|' + '|'.join('---:' if h in right else '---' for h in header) + '|']
    lines += ['| ' + ' | '.join(str(v) for v in row) + ' |' for row in rows]
    return Table('\n'.join(lines))


def lasted(seconds):
    m = int(round(seconds / 60))
    return '%d h %d min' % (m // 60, m % 60) if m >= 60 else '%d min' % m


def checks(a):
    """How much data there is and how complete it is."""
    c = a.checks()
    first, last = when(a.start), when(a.end)
    return table(['Check', 'Value'], [
        ['Readings', '{:,}'.format(c['readings'])],
        ['Links, sites, organizations', '%d, %d, %d' % (c['links'], c['sites'], c['organizations'])],
        ['First and last reading (UTC)', '%s to %s' % (first, last)],
        ['Coverage: minutes between a link\'s first and last reading that have a reading', '%.1f%%' % c['coverage']],
        ['Empty readings: no value arrived (ignored, not counted as loss)', '%.1f%%' % c['empty']],
        ['Readings with any loss', '%.2f%%' % c['any_loss']],
        ['Readings with %g%% loss or more' % a.settings.bad_loss, '%.2f%%' % c['bad_loss']],
        ['Collector gaps: most of an organization went quiet at once (kept out of every outage count)',
         '%d, %.0f hours in all' % (c['collector_gaps'], c['collector_gap_hours'])],
    ], right=['Value'])


def flagged(a):
    """One line per flagged link, the worst first."""
    if not a.flagged:
        return Table('No link is flagged. Every link is at, or close to, its own baseline.')
    lines = []
    for k in a.flagged:
        reason = k.verdict.reason().replace(' -> ', ' to ')
        lines.append('- **%s: %s.** %s%s' % (plain(k.label), k.verdict.word.capitalize(), reason[:1].upper(),
                                             reason[1:]))
    return Table('\n'.join(lines))


def summary(a, days=7):
    """The console report's tables: for each organization, one row per link for the last `days` days."""
    start = a.end - days * DAY
    live = [k for k in a.links if k.live]
    numbers = ['Median ms', 'p95 ms', 'Spread ms', 'Bad min', 'Outages', 'Down', 'Avail']

    def num(fmt, v):
        return '–' if math.isnan(v) else fmt % v

    parts = []
    for org in sorted({k.org for k in live}):
        rows = []
        for k in live:
            if k.org != org:
                continue
            s = k.summary(start, a.end)
            word = WORDS[k.verdict.word]
            rows.append([plain(k.name), plain(k.uplink), num('%.1f', s.median), num('%.1f', s.p95),
                         num('%.1f', s.spread), num('%.1f%%', s.bad_percent), s.outages,
                         length(s.down_seconds) if s.outages else '–', '%.2f%%' % s.availability,
                         '**%s**' % word if k.verdict.flagged else word])
        body = table(['Site', 'Link'] + numbers + ['Latency'], rows, right=numbers)
        parts.append('**%s**\n\n%s' % (plain(org), body))
    return Table('\n\n'.join(parts))


def shifts(a):
    """Every detected level shift in median latency, by date."""
    events = sorted((e.day, k.label, e) for k in a.links for e in k.verdict.median.shifts)
    if not events:
        return Table('No level shift was found.')
    rows = [[short_date(day), plain(label), '%.1f ms' % e.before, '%.1f ms' % e.after,
             '%s by %.1f ms' % ('Slower' if e.after > e.before else 'Faster', abs(e.after - e.before))]
            for day, label, e in events]
    return table(['Date', 'Link', 'Before', 'After', 'Change'], rows, right=['Before', 'After'])


def outages(a, top=6):
    """The longest outages."""
    found = sorted(((b - s, s, k) for k in a.links for s, b in k.outages), key=lambda o: (-o[0], o[1]))[:top]
    if not found:
        return Table('No outage was found.')
    rows = [[plain(k.name), plain(k.uplink), _started(s), lasted(span)] for span, s, k in found]
    return table(['Site', 'Link', 'Started (UTC)', 'Lasted'], rows, right=['Lasted'])


def _started(ts):
    d = datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc)
    return '%d %s' % (d.day, d.strftime('%b %Y, %H:%M'))


def dual_sites(a, days=7, at_least=1800):
    """Sites with two or more uplinks where one had an outage of `at_least` seconds in the last `days` days.

    Returns (organization, site) pairs, ready for `charts.site()`.
    """
    start = a.end - days * DAY
    sites = []
    for k in a.links:
        uplinks = {m.port for m in a.links if m.live and m.org == k.org and m.name == k.name}
        long = any(b > start and b - max(s, start) >= at_least for s, b in k.outages)
        if k.live and len(uplinks) > 1 and long and (k.org, k.name) not in sites:
            sites.append((k.org, k.name))
    return sites


def frame(link):
    """One link's readings as a pandas DataFrame, for your own analysis."""
    import pandas as pd
    x = link.series
    return pd.DataFrame({'time': pd.to_datetime(x.ts, unit='s', utc=True), 'latency_ms': x.latency,
                         'loss_percent': x.loss})
