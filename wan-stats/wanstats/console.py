"""The console report: the verdicts and one period's numbers as plain text."""
from __future__ import annotations

import datetime
import math

from .analysis import DAY

ROW = '%-{site}s %-{link}s  %9s  %7s  %7s  %8s  %8s  %7s  %8s   %s'   # one format for the header and every row
WORDS = {'steady': 'steady', 'drifting': 'DRIFTING', 'worse': 'WORSE', 'new': 'new'}
EXIT = {'steady': 0, 'new': 0, 'drifting': 1, 'worse': 2}


def when(ts):
    return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).strftime('%Y-%m-%d %H:%M')


def length(seconds):
    m = int(round(seconds / 60))
    return '%dh%02d' % (m // 60, m % 60) if m >= 60 else '%dm' % m


def render(a, days=7):
    """Returns (text, exit status). The status is 0 when nothing is flagged, 1 for Drifting, 2 for Worse."""
    if not a.links:
        return 'No readings yet. Run collect.py first.', 3
    start = a.end - days * DAY
    live = [k for k in a.links if k.live]
    row = ROW.format(site=max([4] + [len(k.name) for k in live]),
                     link=max([4] + [len(k.uplink) for k in live]))
    head = row % ('Site', 'Link', 'Median', 'p95', 'Spread', 'Bad min', 'Outages', 'Down', 'Avail', 'Latency')

    def num(fmt, v):
        return '-' if math.isnan(v) else fmt % v

    lines, org = [], None
    for k in live:
        s = k.summary(start, a.end)
        if k.org != org:
            org = k.org
            lines += ['', org, head]
        lines.append(row % (
            k.name, k.uplink, num('%.1f ms', s.median), num('%.1f', s.p95), num('%.1f', s.spread),
            num('%.1f%%', s.bad_percent), s.outages, length(s.down_seconds) if s.outages else '-',
            '%.2f%%' % s.availability, WORDS[k.verdict.word]))

    flagged = a.flagged
    width = max([0] + [len(k.label) for k in flagged])
    top = ['%-9s %-*s  %s' % (WORDS[k.verdict.word], width, k.label, k.verdict.reason()) for k in flagged]
    title = 'WAN stats, %d days to %s UTC' % (days, when(a.end))
    count = '%d links, %d flagged' % (len(live), len(flagged))
    title += ' ' * max(2, len(head) - len(title) - len(count)) + count
    text = '\n'.join([title] + ([''] + top if top else []) + lines)
    return text, max([0] + [EXIT[k.verdict.word] for k in flagged])
