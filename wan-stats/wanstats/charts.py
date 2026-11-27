"""Charts for the notebook, drawn with matplotlib.

Every function takes the `Analysis` from `analysis.analyse()` and returns a `Chart`. A chart shows
itself in a notebook, and `chart.save('name.png')` writes it to a file. Pass `theme='dark'` for a
dark background. A function returns None when there is nothing to draw.
"""
from __future__ import annotations

import datetime
import functools
import io

import matplotlib
import numpy as np
from matplotlib import dates as mdates
from matplotlib import font_manager, ticker
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import PathPatch, Rectangle
from matplotlib.path import Path

from .analysis import DAY, day_date

WIDTH = 8.6   # inches

THEMES = {
    'light': dict(surface='#ffffff', ink='#191919', ink2='#666666', grid='#e7e7e7', axis='#c2c2c2', data='#6e6e6e',
                  s1='#257de0', s2='#f47230', critical='#dc3c3d', warning='#fbb315', band='#f0f0f0', slower='#ec4647',
                  ramp=['#cce3fd', '#9dc8f7', '#6aabf1', '#257de0', '#1757ac', '#0e3d7e']),
    'dark': dict(surface='#0d1117', ink='#efefef', ink2='#9c9c9c', grid='#21262d', axis='#3d444d', data='#9198a1',
                 s1='#338ced', s2='#e76624', critical='#dc3c3d', warning='#fbb315', band='#1c2128', slower='#eb6263',
                 ramp=['#0e3d7e', '#1757ac', '#257de0', '#6aabf1', '#9dc8f7', '#cce3fd']),
}


class Chart:
    """A finished figure. It displays itself in a notebook and can be saved as a PNG."""

    def __init__(self, fig):
        self.fig = fig

    def png(self, dpi=200):
        out = io.BytesIO()
        self.fig.savefig(out, format='png', dpi=dpi, facecolor=self.fig.get_facecolor())
        return out.getvalue()

    def save(self, path, dpi=190):
        with open(path, 'wb') as f:
            f.write(self.png(dpi))

    def _repr_png_(self):
        return self.png(), {'width': int(self.fig.get_figwidth() * 100)}


@functools.lru_cache(maxsize=1)
def _font():
    have = {f.name for f in font_manager.fontManager.ttflist}
    return next((name for name in ('Noto Sans', 'Inter', 'Helvetica Neue', 'Arial') if name in have), 'DejaVu Sans')


def _chart(draw):
    """Draw with the chart font and sizes without changing matplotlib's settings for anyone else."""
    @functools.wraps(draw)
    def wrapped(*args, **kwargs):
        # A device name is drawn as it is: a '$' in one must not start a formula.
        with matplotlib.rc_context({'font.family': _font(), 'font.size': 8.5, 'axes.unicode_minus': False,
                                    'text.parse_math': False}):
            fig = draw(*args, **kwargs)
        return Chart(fig) if fig is not None else None
    return wrapped


def _dt(ts):
    return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).replace(tzinfo=None)


def _noon(day):
    return _dt(int(day) * DAY + DAY // 2)


def _figure(t, height, title, subtitle):
    fig = Figure(figsize=(WIDTH, height), dpi=100, facecolor=t['surface'])
    FigureCanvasAgg(fig)
    fig.text(0.035, 1 - 0.22 / height, title, fontsize=12.5, fontweight='bold', color=t['ink'], va='top')
    fig.text(0.035, 1 - 0.50 / height, subtitle, fontsize=9, color=t['ink2'], va='top')
    return fig


def _axes(fig, t, left, bottom, width, top, grid='y'):
    """Axes placed in inches from the figure's edges, so a taller figure keeps the same margins."""
    h = fig.get_figheight()
    ax = fig.add_axes([left, bottom / h, width, 1 - (bottom + top) / h])
    ax.set_facecolor(t['surface'])
    for side in ax.spines.values():
        side.set_visible(False)
    ax.tick_params(colors=t['ink2'], length=0, labelsize=8.5)
    ax.grid(False)
    if grid:
        ax.grid(axis=grid, color=t['grid'], linewidth=0.8)
    ax.set_axisbelow(True)
    return ax


def _strip(fig, left, from_top, width):
    """A thin, invisible axes for a legend, `from_top` inches below the top edge."""
    h = fig.get_figheight()
    ax = fig.add_axes([left, 1 - (from_top + 0.18) / h, width, 0.18 / h])
    ax.set_axis_off()
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 1)
    return ax


def _months(ax):
    """Month names along the bottom, with the year under the first label and under each January."""
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    first = [True]

    def label(v, _):
        d = mdates.num2date(v)
        text = d.strftime('%b') + (d.strftime('\n%Y') if first[0] or d.month == 1 else '')
        first[0] = False
        return text
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(label))


def _unit(ax, t, text='ms'):
    ax.text(-0.012, 1.035, text, transform=ax.transAxes, ha='right', va='bottom', color=t['ink2'], fontsize=8.5)


def _line_key(fig, t, items, from_top=0.70):
    key = _strip(fig, 0.07, from_top, 0.86)
    x = 0
    for label, color, width, alpha in items:
        key.plot([x, x + 4], [0.5, 0.5], color=color, linewidth=width, alpha=alpha, solid_capstyle='butt')
        key.text(x + 5.2, 0.5, label, va='center', color=t['ink2'], fontsize=8.3)
        x += 9 + len(label) * 0.98


def _span(a):
    return _dt(a.start - 3 * DAY), _dt(a.end + 2 * DAY)


def _long_date(day):
    d = day_date(day)
    return '%d %s' % (d.day, d.strftime('%B %Y'))


def _short_date(day):
    d = day_date(day)
    return '%d %s' % (d.day, d.strftime('%b %Y'))


def _drawable(a):
    return [k for k in a.links if len(k.days)]


# ---------------------------------------------------------------- every link, every week

def _weekly(link, origin, weeks):
    """Median latency of each whole week since `origin`, for weeks with enough readings."""
    x = link.series
    w = (x.ts - origin) // (7 * DAY)
    edges = np.searchsorted(w, np.arange(weeks + 1))
    out = {}
    for k, (i, j) in enumerate(zip(edges[:-1], edges[1:])):
        v = x.latency[i:j]
        v = v[~np.isnan(v)]
        if len(v) >= 2000:
            out[k] = float(np.median(v))
    return out


@_chart
def fleet(a, theme='light'):
    """One row per link and one cell per week, shaded by how far the week sits above the link's baseline."""
    t = THEMES[theme]
    rows = _drawable(a)
    if not rows:
        return None
    origin = a.start // DAY * DAY
    weeks = max(1, (a.end - origin) // (7 * DAY))
    steps = [1.10, 1.25, 1.5, 2.0, 3.0]
    labels = ['at baseline', '+10%', '+25%', '+50%', '2×', '3× or more']
    orgs = len({k.org for k in rows})
    units = len(rows) + 2.1 * orgs - 0.1
    fig = _figure(t, 0.2015 * units + 1.54, 'Weekly median latency relative to each link\'s baseline',
                  '%d sites, one row per link. The baseline is the lowest level a link has sustained for %d days. '
                  '%s cells are further above it.'
                  % (a.sites, a.settings.sustained_days, 'Brighter' if theme == 'dark' else 'Darker'))
    ax = _axes(fig, t, 0.185, 0.735, 0.555, 0.805, grid=None)
    side = ax.get_yaxis_transform()          # x across the axes (0 to 1), y in rows
    y, ticks, names, org_at, partial = 0.0, [], [], {}, False
    for k in rows:
        if k.org not in org_at:
            y += 1.1 if org_at else 0
            org_at[k.org] = y
            y += 1.0
        base = k.verdict.median.baseline or 0.001        # a baseline of exactly zero still needs a ratio
        cells = _weekly(k, origin, weeks)
        partial |= len(cells) < weeks
        for week, v in cells.items():
            shade = t['ramp'][int(np.searchsorted(steps, v / base, side='right'))]
            ax.add_patch(Rectangle((week + 0.06, y + 0.08), 0.88, 0.84, color=shade, linewidth=0))
        ticks.append(y + 0.5)
        names.append('%s  %s' % (k.name, k.uplink))
        # The numbers beside each row mean no reading depends on colour alone.
        word = k.verdict.word if k.live else 'ended'
        hot = word in ('drifting', 'worse')
        ink, weight = (t['ink'], 'bold') if hot else (t['ink2'], 'normal')
        kw = dict(transform=side, va='center', color=ink, fontweight=weight, fontsize=8.3)
        ax.text(1.107, y + 0.5, '%.1f' % base, ha='right', **kw)
        ax.text(1.191, y + 0.5, '%.1f' % k.verdict.median.current if k.live else '–', ha='right', **kw)
        if hot:
            mark = dict(marker='^', color=t['critical'], s=38) if word == 'worse' else \
                dict(marker='D', color=t['warning'], s=30)
            ax.scatter([1.229], [y + 0.5], transform=side, clip_on=False, zorder=5, linewidths=0, **mark)
            ax.text(1.250, y + 0.5, word.capitalize(), ha='left', **dict(kw, color=t['ink'], fontweight='bold'))
        y += 1.0
    head = dict(transform=side, va='center', color=t['ink2'], fontsize=8.3)
    for org, at in org_at.items():
        ax.text(0, at + 0.55, org, ha='left', **dict(head, color=t['ink'], fontweight='bold', fontsize=8.8))
    top = min(org_at.values())
    ax.text(1.107, top + 0.55, 'Baseline', ha='right', **head)
    ax.text(1.191, top + 0.55, 'Now', ha='right', **head)
    ax.text(1.191, top - 0.55, 'sustained level, ms', ha='right', **head)
    ax.set_xlim(-0.3, weeks)
    ax.set_ylim(y, -1.0)
    ax.set_yticks(ticks)
    ax.set_yticklabels(names, color=t['ink2'], fontsize=8.3)
    ax.tick_params(axis='y', pad=6)
    xt, xl = [], []
    month = _dt(a.start).replace(day=1, hour=0, minute=0, second=0)
    while True:
        month = (month + datetime.timedelta(days=32)).replace(day=1)
        week = (month.replace(tzinfo=datetime.timezone.utc).timestamp() - origin) / (7 * DAY)
        if week > weeks - 1:
            break
        if week >= 0:
            xl.append(month.strftime('%b') + (month.strftime('\n%Y') if month.month == 1 or not xt else ''))
            xt.append(week)
    ax.set_xticks(xt)
    ax.set_xticklabels(xl, color=t['ink2'], ha='left')
    h = fig.get_figheight()
    key = fig.add_axes([0.185, 0.126 / h, 0.555, 0.196 / h])
    key.set_axis_off()
    key.set_xlim(0, 60)
    key.set_ylim(0, 1)
    x = 0
    for shade, label in zip(t['ramp'], labels):          # six ordered steps of one hue
        key.add_patch(Rectangle((x, 0.15), 1.6, 0.7, color=shade, linewidth=0))
        key.text(x + 2.1, 0.5, label, va='center', color=t['ink2'], fontsize=8)
        x += 4.2 + len(label) * 0.62
    if partial:
        key.text(x + 1.0, 0.5, 'blank = no data', va='center', color=t['ink2'], fontsize=8)
    return fig


# ---------------------------------------------------------------- one link over time

@_chart
def levels(a, link, which='median', theme='light'):
    """A link's daily values with the sustained level on each side of every detected shift."""
    t = THEMES[theme]
    r = getattr(link.verdict, which)
    y = getattr(link, which)
    if not len(y):
        return None
    what = 'median' if which == 'median' else '95th-percentile'
    rises = [e for e in r.shifts if e.after > e.before]
    if rises:
        e = rises[-1]
        note = 'Sustained level before the shift: %.1f ms. After: %.1f ms. Shift dated %s.' % (
            e.before, e.after, _long_date(e.day))
    else:
        note = 'Baseline %.1f ms. Current level %.1f ms.' % (r.baseline, r.current)
    fig = _figure(t, 3.6, '%s: daily %s latency and detected level shift%s' % (
        link.label, what, '' if len(r.shifts) == 1 else 's'), note)
    ax = _axes(fig, t, 0.07, 0.54, 0.80, 0.954)
    band = dict(color=t['s1'], alpha=0.38, linewidth=6, solid_capstyle='butt', zorder=2)
    for lv in r.levels:      # the model is a wide, pale band, and the data is drawn on top so it stays visible
        ax.plot([_dt(lv.start * DAY), _dt((lv.end + 1) * DAY)], [lv.level] * 2, **band)
    for before, after in zip(r.levels, r.levels[1:]):
        x = _dt(after.start * DAY)
        ax.plot([x, x], [before.level, after.level], **band)
        if before.days >= 45:
            ax.text(x - datetime.timedelta(days=5), (before.level + after.level) / 2,
                    'shift dated\n' + _short_date(after.start), ha='right', va='center', color=t['ink'],
                    fontsize=8.5, fontweight='bold', linespacing=1.0)
    ax.plot([_noon(d) for d in link.days], y, color=t['data'], linewidth=1.1, solid_joinstyle='round', zorder=3)
    ax.set_ylim(0, max(max(lv.level for lv in r.levels) * 1.45, float(np.percentile(y, 97)) * 1.1))
    ax.set_xlim(*_span(a))
    right = _dt(a.end + 6 * DAY)
    label = dict(ha='left', va='center', fontsize=8.5, linespacing=1.0)
    ax.text(right, r.levels[-1].level, '%.1f ms\nnow' % r.levels[-1].level, color=t['ink'], fontweight='bold', **label)
    lowest = min(lv.level for lv in r.levels)
    if r.levels[-1].level - lowest > 0.12 * ax.get_ylim()[1]:
        ax.text(right, lowest, '%.1f ms\nbaseline' % lowest, color=t['ink2'], **label)
    _months(ax)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(5))
    _unit(ax, t)
    _line_key(fig, t, [('daily ' + what.replace('-', ' '), t['data'], 1.1, 1.0), ('sustained level', t['s1'], 6, 0.38)])
    return fig


@_chart
def trend(a, link, which='p95', theme='light'):
    """A link's daily values with the trend line fitted inside its current level."""
    t = THEMES[theme]
    r = getattr(link.verdict, which)
    y = getattr(link, which)
    if not len(y) or r.weeks < 2:
        return None
    what = 'median' if which == 'median' else '95th-percentile'
    top = float(np.percentile(y, 97)) * 1.25
    over = int((y > top).sum())
    note = 'Theil-Sen slope %+.1f ms per month, Mann-Kendall p %s.' % (
        r.slope_month, '< 0.001' if r.p < 0.001 else '= %.3f' % r.p)
    if r.weeks >= 8:
        note += ' First four weeks %.0f ms, last four %.0f ms.' % (r.start, r.start + r.gain)
    if over:
        note += ' %d daily value%s above the axis.' % (over, ' lies' if over == 1 else 's lie')
    fig = _figure(t, 3.6, '%s: daily %s latency and trend' % (link.label, what), note)
    ax = _axes(fig, t, 0.07, 0.54, 0.80, 0.954)
    ax.plot([_noon(d) for d in link.days], y, color=t['data'], linewidth=1.0, solid_joinstyle='round', zorder=2)
    first = r.levels[-1].start
    ax.plot([_dt((first + 3.5) * DAY), _dt((first + 7 * (r.weeks - 1) + 3.5) * DAY)], [r.fit0, r.fit1],
            color=t['s1'], linewidth=2.4, solid_capstyle='round', zorder=4)
    ax.set_ylim(0, top)
    ax.set_xlim(*_span(a))
    ax.text(_dt(a.end + 6 * DAY), r.fit1, '%.0f ms\non the\ntrend line' % r.fit1, ha='left', va='center',
            color=t['ink'], fontsize=8.5, fontweight='bold', linespacing=1.0)
    _months(ax)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(5))
    _unit(ax, t)
    _line_key(fig, t, [('daily ' + what.replace('-', ' '), t['data'], 1.0, 1.0),
                       ('trend line, fitted to the weekly values', t['s1'], 2.4, 1.0)])
    return fig


def flagged(a, link, theme='light'):
    """The chart that shows why a link was flagged: its level shift, or its trend."""
    v = link.verdict
    for which in ('median', 'p95'):
        r = getattr(v, which)
        if r.verdict in ('drifting', 'worse') and r.why == 'shift':
            return levels(a, link, which, theme)
    which = 'p95' if v.p95.verdict in ('drifting', 'worse') else 'median'
    return trend(a, link, which, theme)


# ---------------------------------------------------------------- every level shift, by date

def shift_clusters(a, gap=3):
    """Groups of shifts on two or more links, where each shift is within `gap` days of the next."""
    events = sorted((e.day, k.label) for k in a.links for e in k.verdict.median.shifts)
    groups = []
    for e in events:
        if groups and e[0] - groups[-1][-1][0] <= gap:
            groups[-1].append(e)
        else:
            groups.append([e])
    return [g for g in groups if len({label for _, label in g}) >= 2]


@_chart
def shifts(a, theme='light'):
    """Every detected level shift in median latency, one row per link."""
    t = THEMES[theme]
    rows = sorted([k for k in a.links if k.verdict.median.shifts], key=lambda k: k.verdict.median.shifts[0].day)
    if not rows:
        return None
    total = sum(len(k.verdict.median.shifts) for k in rows)
    fig = _figure(t, 0.28 * (len(rows) + 0.3) + 1.74, 'Detected level shifts in median latency, by date and link',
                  '%d shift%s on %d link%s. Where two shifts on one link are close together, only the later one '
                  'carries its values.' % (total, '' if total == 1 else 's', len(rows), '' if len(rows) == 1 else 's'))
    ax = _axes(fig, t, 0.15, 0.56, 0.80, 1.18)
    ax.set_ylim(len(rows) - 0.4, -0.7)
    ax.set_xlim(*_span(a))
    for g in shift_clusters(a):
        ax.axvspan(_dt((g[0][0] - 1) * DAY), _dt((g[-1][0] + 2) * DAY), color=t['band'], linewidth=0, zorder=0)
    for i, k in enumerate(rows):
        events = k.verdict.median.shifts
        for n, e in enumerate(events):
            up = e.after > e.before
            ax.scatter([_noon(e.day)], [i], s=62, marker='^' if up else 'v', color=t['slower'] if up else t['s1'],
                       edgecolors=t['surface'], linewidths=1.2, zorder=3)
            following = events[n + 1].day if n + 1 < len(events) else 10 ** 9
            if following - e.day > 45:        # the label sits just above the row line, so nothing hides behind it
                ax.text(_noon(e.day + 5), i - 0.13, '%.1f to %.1f ms' % (e.before, e.after), ha='left', va='bottom',
                        color=t['ink2'], fontsize=8.2)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(['%s  %s' % (k.name, k.uplink) for k in rows], fontsize=8.3)
    ax.tick_params(axis='y', pad=6)
    _months(ax)
    key = _strip(fig, 0.15, 0.90, 0.80)
    text = dict(va='center', color=t['ink2'], fontsize=8.3)
    key.scatter([1.2], [0.5], s=62, marker='^', color=t['slower'], linewidths=0)
    key.text(3.2, 0.5, 'got slower', **text)
    key.scatter([17.5], [0.5], s=62, marker='v', color=t['s1'], linewidths=0)
    key.text(19.5, 0.5, 'got faster', **text)
    key.add_patch(Rectangle((33, 0.12), 2.2, 0.76, color=t['band'], linewidth=0))
    key.text(36.3, 0.5, 'two or more links shifted within three days of each other', **text)
    return fig


# ---------------------------------------------------------------- distribution of every link

@_chart
def boxes(a, theme='light'):
    """One box per link: median, middle half, and 5th to 95th percentile of every reading."""
    t = THEMES[theme]
    light = t['ramp'][1]
    rows = []
    for k in a.links:
        v = k.series.latency
        v = v[~np.isnan(v)]
        if len(v):
            rows.append((np.percentile(v, [5, 25, 50, 75, 95]), k))
    if not rows:
        return None
    rows.sort(key=lambda r: r[0][2])
    days = max(1, round((a.end - a.start) / DAY))
    fig = _figure(t, 0.19 * (len(rows) + 0.2) + 1.59, 'Latency distribution by link, %d days' % days,
                  'Sorted by median. One reading per link per minute.')
    ax = _axes(fig, t, 0.15, 0.45, 0.80, 1.14, grid='x')
    ax.set_ylim(len(rows) - 0.4, -0.6)
    ax.set_xlim(0, max(q[4] for q, _ in rows) * 1.06)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(['%s  %s' % (k.name, k.uplink) for _, k in rows], fontsize=8.3)
    ax.tick_params(axis='y', pad=6)
    ax.xaxis.set_major_locator(ticker.MaxNLocator(6, steps=[1, 2, 5, 10]))
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: '%g ms' % v))

    def box(on, y, q, h=0.30):
        on.plot([q[0], q[4]], [y, y], color=light, linewidth=1.6, solid_capstyle='butt', zorder=2)
        for x in (q[0], q[4]):
            on.plot([x, x], [y - h * 0.55, y + h * 0.55], color=light, linewidth=1.6, solid_capstyle='butt', zorder=2)
        on.add_patch(Rectangle((q[1], y - h), q[3] - q[1], 2 * h, facecolor=light, edgecolor='none', zorder=3))
        on.plot([q[2], q[2]], [y - h * 1.35, y + h * 1.35], color=t['s1'], linewidth=2.4, solid_capstyle='round',
                zorder=4)
    for i, (q, _) in enumerate(rows):
        box(ax, i, q)
    h = fig.get_figheight()
    key = fig.add_axes([0.15, 1 - 1.05 / h, 0.80, 0.33 / h])     # how to read one box
    key.set_axis_off()
    key.set_xlim(0, 100)
    key.set_ylim(1, -1)
    box(key, 0, [1, 5, 8, 13, 18], h=0.42)
    key.text(20.5, 0, 'line: 5th to 95th percentile      box: the middle half of all minutes      mark: the median',
             va='center', color=t['ink2'], fontsize=8.3)
    return fig


# ---------------------------------------------------------------- downtime

def _bar(ax, y, x1, color, thick=0.13, radius=0.04):
    """A horizontal bar from zero: square at the baseline, rounded at the data end. Sizes are in inches."""
    fig = ax.figure
    (px0, py), (px1, _) = ax.transData.transform([(0, y), (x1, y)]) / fig.dpi
    h = thick / 2
    r = min(radius, max(px1 - px0, 0.001) / 2, h)
    k = 0.5523 * r
    points = [(px0, py - h), (px1 - r, py - h), (px1 - r + k, py - h), (px1, py - h + r - k), (px1, py - h + r),
              (px1, py + h - r), (px1, py + h - r + k), (px1 - r + k, py + h), (px1 - r, py + h), (px0, py + h),
              (px0, py - h)]
    codes = [Path.MOVETO, Path.LINETO, Path.CURVE4, Path.CURVE4, Path.CURVE4, Path.LINETO, Path.CURVE4, Path.CURVE4,
             Path.CURVE4, Path.LINETO, Path.CLOSEPOLY]
    fig.add_artist(PathPatch(Path(points, codes), transform=fig.dpi_scale_trans, facecolor=color, edgecolor='none',
                             zorder=3))


def _hours(v):
    return '%.0f h' % v if v >= 10 else '%.1f h' % v


@_chart
def downtime(a, theme='light', top=12):
    """Total downtime by link, for the links with the most."""
    t = THEMES[theme]
    rows = []
    for k in a.links:
        spans = [b - s for s, b in k.outages]
        if spans:
            rows.append((sum(spans) / 3600, len(spans), max(spans) / 3600, k))
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: -r[0])[:top]
    days = max(1, round((a.end - a.start) / DAY))
    fig = _figure(t, 0.2656 * (len(rows) + 0.2) + 1.26, 'Total downtime by link, %d days' % days,
                  'Outages of %d minutes or longer (loss of %d%% or more, or no readings). %s'
                  % (a.settings.outage_seconds // 60, a.settings.outage_loss,
                     'The %d links with the most downtime.' % len(rows) if len(rows) == top else ''))
    ax = _axes(fig, t, 0.185, 0.45, 0.74, 0.81, grid=None)
    ax.set_ylim(len(rows) - 0.4, -0.6)
    ax.set_xlim(0, rows[0][0] * 1.32)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(['%s  %s' % (r[3].name, r[3].uplink) for r in rows], fontsize=8.3)
    ax.tick_params(axis='y', pad=6)
    ax.xaxis.set_major_locator(ticker.MaxNLocator(7, steps=[1, 1.2, 2, 2.4, 3, 5, 6, 10]))
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: '%g h' % v))
    for i, (hours, count, longest, _) in enumerate(rows):
        _bar(ax, i, hours, t['s1'])
        ax.text(hours + rows[0][0] * 0.012, i, '%s in %d outage%s, longest %s' % (
            _hours(hours), count, '' if count == 1 else 's', _hours(longest)),
            va='center', color=t['ink2'], fontsize=8.2)
    ax.axvline(0, color=t['axis'], linewidth=0.9, zorder=4)
    return fig


# ---------------------------------------------------------------- the uplinks of one site, side by side

@_chart
def site(a, name, days=7, theme='light', org=None):
    """Hourly median latency of a site's uplinks over the last days, with the longest outage shaded.

    Draws the site's first two uplinks. Give `org` when two organizations have a site of the same name.
    """
    t = THEMES[theme]
    found = [k for k in a.links if k.name == name and k.live and org in (None, k.org)]
    links = []
    for k in found:                      # one organization, and one line per uplink
        if k.org == found[0].org and k.port not in [m.port for m in links]:
            links.append(k)
    links = links[:2]
    if not links:
        return None
    start = a.end - days * DAY
    lines, ends, peak = [], {}, 0.0
    for k, color in zip(links, (t['s1'], t['s2'])):
        x = k.series
        m = x.ts >= start
        ts, lat = x.ts[m], x.latency[m].copy()
        lat[x.loss[m] >= a.settings.outage_loss] = np.nan
        hour = (ts - start) // 3600
        edges = np.searchsorted(hour, np.arange(days * 24 + 1))
        xs, ys = [], []
        for h, (i, j) in enumerate(zip(edges[:-1], edges[1:])):
            v = lat[i:j]
            v = v[~np.isnan(v)]
            xs.append(_dt(start + h * 3600 + 1800))
            ys.append(float(np.median(v)) if len(v) >= 5 else np.nan)
        lines.append((k, color, xs, ys))
        seen = [(x_, y_) for x_, y_ in zip(xs, ys) if not np.isnan(y_)]
        if seen:
            ends[k.port] = (color, seen[-1])
            peak = max(peak, max(y_ for _, y_ in seen))
    if not ends:
        return None
    worst = max(((b - max(s, start), max(s, start), b, k) for k in links for s, b in k.outages if b > start),
                default=None, key=lambda o: o[0])
    title = '%s %s: hourly median latency, last %d days' % (name, ' and '.join(k.port for k in links), days)
    note = 'Times are UTC.' + (' Shaded band: the longest outage in the period.' if worst else '')
    fig = _figure(t, 3.6, title, note)
    ax = _axes(fig, t, 0.07, 0.47, 0.80, 0.97)
    for k, color, xs, ys in lines:
        ax.plot(xs, ys, color=color, linewidth=2, solid_joinstyle='round', solid_capstyle='round', zorder=3)
    top = max(20.0, peak * 1.35)
    ax.set_ylim(0, top)
    if worst:
        length, s, b, k = worst
        ax.axvspan(_dt(s), _dt(b), color=t['band'], zorder=1, linewidth=0)
        mins = int(length // 60)
        took = '%d h %d min' % (mins // 60, mins % 60) if mins >= 60 else '%d min' % mins
        middle = min(max((s + b) // 2, start + 0.07 * days * DAY), a.end - 0.07 * days * DAY)   # keep it on the chart
        ax.text(_dt(middle), top * 0.95, '%s down\n%s' % (k.port, took), ha='center', va='top', color=t['ink'],
                fontsize=8.5, fontweight='bold', linespacing=1.0)
    labels = sorted((y_, uplink) for uplink, (_, (_, y_)) in ends.items())
    at = [y_ for y_, _ in labels]
    for i in range(1, len(at)):                    # two lines that end close together still get separate labels
        at[i] = max(at[i], at[i - 1] + 0.075 * top)
    for uplink, (color, (x_, y_)) in ends.items():
        ax.scatter([x_], [y_], s=46, color=color, edgecolors=t['surface'], linewidths=1.6, zorder=4, clip_on=False)
    for (y_, uplink), label_y in zip(labels, at):
        ax.text(_dt(a.end + 0.12 * days / 7 * DAY), label_y, '%s  %.0f ms' % (uplink, y_), ha='left', va='center',
                color=t['ink'], fontsize=8.5, fontweight='bold')
    ax.set_xlim(_dt(start), _dt(a.end))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, days // 7)))
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(
        lambda v, _: '%s %d %s' % (mdates.num2date(v).strftime('%a'), mdates.num2date(v).day,
                                   mdates.num2date(v).strftime('%b'))))
    for label in ax.get_xticklabels():
        label.set_ha('left')
    ax.yaxis.set_major_locator(ticker.MaxNLocator(4))
    _unit(ax, t)
    key = _strip(fig, 0.07, 0.74, 0.6)
    x = 0
    for k, color, _, _ in lines:
        key.plot([x, x + 4], [0.5, 0.5], color=color, linewidth=2)
        key.text(x + 5.2, 0.5, k.port, va='center', color=t['ink2'], fontsize=8.3)
        x += 14
    return fig
