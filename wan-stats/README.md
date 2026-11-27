# WAN stats

**Data science for Meraki WAN links. It keeps a history of loss and latency for every uplink, then uses statistics instead of fixed alarms to find the links that got worse.**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/fleet-year-dark.png">
  <img alt="A grid with one row per WAN link and one cell per week across a year, shaded by how far the week's median latency sits above the link's baseline. Most rows stay at baseline all year. Willow wan1 is at almost four times its baseline from March on and is marked Worse. Cypress wan2 is above its baseline from December, with a gap in the spring, and is marked Drifting. Several other rows are shaded for part of the year and then clear, including Willow wan2 for nine months." src="docs/img/fleet-year-light.png">
</picture>

*Figure 1. Weekly median latency of each link, relative to that link's own baseline. The baseline is taken from the whole year, so a row that is shaded at first and then clears is a link that got faster. The example data is an anonymized dataset: 12.1 million readings from 20 sites over one year.*

Every figure and table on this page is drawn by the [notebook](wan-stats.ipynb) from that dataset. The same report as a PDF: [example-report.pdf](docs/example-report.pdf).

## Summary

- **Data:** 12.1 million one-minute readings of loss and latency from 23 WAN links at 20 sites over one year.
- **Method:** robust daily summaries, a detector for level shifts, a per-link baseline, and a trend test.
- **Results:** 17 level shifts on 11 links, each with a date. One link is classed as Worse and one as Drifting.
- **Validation:** when steps of 6 ms or more were planted in steady links, the method found 600 of 600 and dated 99% of them to the exact day.

## Quick start

It needs Python 3.10 or newer. WAN stats only reads from Meraki, so a read-only API key is enough.

```sh
git clone https://github.com/egrisdev/meraki-tools.git
cd meraki-tools/wan-stats
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-notebook.txt
```

**Try it on invented data first.** No Meraki account is needed:

```sh
python demo.py                                  # writes demo.sqlite: seven links, eight months
python report.py --db sqlite:///demo.sqlite     # the console report
WAN_STATS_DB=sqlite:///demo.sqlite jupyter notebook wan-stats.ipynb
```

**Then point it at your own network:**

```sh
cp .env.example .env && chmod 600 .env          # put your API key in .env
python collect.py                               # one run, to check the key works
python collect.py --backfill 60d                # optional: start with the 60 days Meraki keeps
```

Add one line to your crontab (`crontab -e`) and the history builds from there:

```
*/4 * * * * cd /path/to/meraki-tools/wan-stats && .venv/bin/python collect.py >> collect.log 2>&1
```

The sections from [Collector](#collector) on cover each part.

## Problem

The Meraki Dashboard API returns uplink loss and latency in five-minute slices, and keeps 60 days of it. That answers "is the link bad right now?" but not the questions that come up later:

- Was this site always this slow, or did something change? If it changed, on what day?
- How many times did the link drop this month, and for how long?
- Which link is the worst?

A fixed alarm, such as "alert above 150 ms", answers none of them. WAN stats keeps the history and treats it as a data set: it checks the data, summarizes it robustly, looks for change, and then tests whether its own verdicts hold up.

## Data

| Item | In the example data |
|---|---|
| One reading | Loss (%) and latency (ms) for one uplink over one minute, measured by the Meraki device to a test address on the internet. |
| The example year | 12,083,112 readings from 23 links at 20 sites in two organizations. |
| One link | One uplink of one device, measured to one test address. A replaced device keeps its link's history when the new device has the same name in the same network. |
| Coverage | 99.5% of the minutes between each link's first and last reading have a reading. The rest are outages, collector gaps and short gaps. |
| Empty readings | 0.8% of readings arrive with no value. They are ignored, not counted as loss. |
| Collector gaps | 26 gaps, 12 hours in all, where at least 60% of an organization's links went quiet at the same moment. Such a gap is treated as the collector failing, not the sites, and is kept out of every outage count. |

## Pipeline

```mermaid
flowchart TB
    subgraph collector["Collector (ETL)"]
        direction LR
        extract["Extract<br>last 5 minutes,<br>every uplink"] --> transform["Transform<br>one row per link<br>per minute"] --> load["Load<br>skip rows<br>already stored"]
    end
    api["Meraki Dashboard API"] -->|"cron, every 4 minutes"| collector
    collector --> db[("Your database<br>SQLite, PostgreSQL<br>or MongoDB")]
    db --> stats["Statistics"]
    stats --> notebook["Jupyter notebook"]
    stats --> console["Console report"]
    notebook --> pdf["PDF or HTML report"]
```

WAN stats only reads from Meraki. It never changes a network.

| Part | What it does |
|---|---|
| Collector (ETL), `collect.py` | A small extract, transform, load job. It is an ordinary cron job: one crontab line runs it every four minutes (Task Scheduler does the same on Windows). **Extract:** asks Meraki for the last five minutes of loss and latency for every uplink in every organization the API key can see. **Transform:** flattens the answer into one row per link per minute, with the device name and a UTC timestamp. **Load:** writes the rows to the database and skips any it already has, so overlapping runs are safe. |
| Database | One reading per link per minute. SQLite by default, with nothing to set up. PostgreSQL or MongoDB with one setting. |
| Statistics, `wanstats/analysis.py` | One Python file that holds the method below. The notebook and the console report both call it, so their numbers always match. |
| Notebook, `wan-stats.ipynb` | One step per part of the analysis: data checks, flagged links, level shifts, the weekly summary, outages. Run it live, or export it as a PDF or HTML report with the code hidden. |
| Console report, `report.py` | The same verdicts as plain text, for a terminal or a scheduled job. It returns a non-zero exit status when a link is flagged. |

## Method

**1. Robust daily summary.** Each link's day (a UTC calendar day) is reduced to two numbers: its median latency, and its 95th percentile, called the slow end below because only one minute in twenty is slower. Latency is steady most of the time with occasional large spikes, and a few spikes drag an average a long way. In the example year, Magnolia wan1 had a 30-day period with a median of 15.6 ms and a 95th percentile of 16.2 ms. Its average was 48.8 ms, because 100 of the 42,837 readings in that period were longer than a second.

**2. Level-shift detection.** For every day, the 14 days after are compared with the 14 days before. A shift is recorded when the two medians differ by at least 3 ms, by at least 10%, and by at least four times the link's day-to-day noise. The noise is a robust estimate of the standard deviation of the daily values, taken from the median absolute change from one day to the next. The comparison places a shift only to within about a week, so the date is then moved to the day that best separates the two levels (least absolute deviation). Each shift gets a date, a size and a direction, and links that got faster are found as readily as links that got slower. This is a rule with fixed thresholds, not a significance test.

**3. Per-link baseline.** The baseline is the lowest level the link has held for 30 days or more. Links are never compared with each other or with one shared limit. A link that has always run at 75 ms is not a problem. A link that ran at 4 ms and now runs at 15 ms is.

**4. Trend test.** A slow rise has no single day to find. Inside the link's current level, a Theil-Sen slope is fitted to the weekly medians of the daily values and checked with a Mann-Kendall test. Both are standard choices when the data has outliers. A rise counts when p is below 0.001. Its size is then measured from the data, as the last four weeks against the first four, because a straight line fitted through a small step overstates it.

**5. Classification.** The rise above baseline is taken from whichever of the two tests found more, and judged on one scale.

```mermaid
flowchart LR
    rise["Rise above baseline:<br>the larger of the level shift<br>and the trend"] --> q1{{"At least 50%<br>and 10 ms?"}}
    q1 -->|yes| worse["Worse"]
    q1 -->|no| q2{{"At least 10%<br>and 3 ms?"}}
    q2 -->|yes| drifting["Drifting"]
    q2 -->|no| steady["Steady"]
```

The median and the slow end each go through steps 2 to 5, and a link takes the worse of its two verdicts. Both thresholds in a pair must be met, so a 5 ms link that moves to 7 ms is up 40% and is still left alone. The verdict describes latency only. Outages are counted separately and do not change it.

*Table 1. The settings behind every result on this page. All of them can be [changed](#settings).*

| Setting | Value |
|---|---|
| Days compared on each side of a possible shift | 14 (at least 7 before, at the start of the data) |
| Smallest shift recorded | 3 ms, 10%, and four times the day-to-day noise |
| Baseline | Lowest level held for 30 days or more |
| Trend test | Mann-Kendall p below 0.001 on weekly values |
| Drifting | Rise of at least 10% and 3 ms |
| Worse | Rise of at least 50% and 10 ms |
| Outage | Three minutes or more of at least 80% loss, or more than ten minutes with no readings from the link |
| Bad minute | Loss of 2% or more, or latency over 150 ms |

The 2% line for a bad minute comes from the data. The smallest loss a minute can show is 1.7%, which is one lost probe in sixty. A 1% line would count every minute with any loss at all, 1.74% of them. The 2% line counts 0.31%. Minutes inside an outage are bad minutes too, when the link was still reporting.

## Results

### Level shifts

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/level-shifts-dark.png">
  <img alt="Timeline of every level shift in median latency over the year, one row per link. Four links shift on 23 and 24 September, two on 11 October and two on 19 and 20 November. Later shifts are isolated: Chestnut slows in December and recovers, Willow wan2 slows for ten days in January, Willow wan1 slows in March, and Willow wan2 gets faster in June." src="docs/img/level-shifts-light.png">
</picture>

*Figure 2. Detected level shifts in median latency, by date and link. Table 2 has every value.*

Level shifts were found on 11 of the 23 links (Figure 2), 17 shifts in all, and 12 of them were improvements.

The dates carry information of their own. Four links at four sites, in both organizations, shifted within two days of each other on 23 and 24 September. Hemlock and Alder both got faster on 11 October, and Alder and Maple changed a day apart in November. Links at separate sites rarely move together by chance, so the likely cause is something they share, such as an ISP or the path to the test address. The shifts from December on each happened alone, which points at the link itself.

<details>
<summary>Table 2. All 17 detected level shifts in median latency</summary>

| Date | Link | Before | After | Change |
|---|---|---:|---:|---|
| 19 Sep 2025 | Aspen wan1 | 58.2 ms | 17.2 ms | Faster by 41.0 ms |
| 23 Sep 2025 | Locust wan1 | 37.3 ms | 14.1 ms | Faster by 23.2 ms |
| 24 Sep 2025 | Buckeye wan1 | 33.5 ms | 13.2 ms | Faster by 20.3 ms |
| 24 Sep 2025 | Hemlock wan1 | 79.0 ms | 91.1 ms | Slower by 12.1 ms |
| 24 Sep 2025 | Redwood wan1 | 37.9 ms | 15.5 ms | Faster by 22.4 ms |
| 2 Oct 2025 | Alder wan1 | 86.3 ms | 102.3 ms | Slower by 16.0 ms |
| 11 Oct 2025 | Alder wan1 | 102.3 ms | 79.2 ms | Faster by 23.0 ms |
| 11 Oct 2025 | Hemlock wan1 | 91.1 ms | 74.7 ms | Faster by 16.4 ms |
| 19 Nov 2025 | Alder wan1 | 79.2 ms | 17.5 ms | Faster by 61.8 ms |
| 20 Nov 2025 | Maple wan1 | 45.5 ms | 15.9 ms | Faster by 29.6 ms |
| 26 Nov 2025 | Larch wan1 | 50.9 ms | 7.0 ms | Faster by 43.9 ms |
| 1 Dec 2025 | Chestnut wan1 | 17.6 ms | 41.0 ms | Slower by 23.4 ms |
| 31 Dec 2025 | Chestnut wan1 | 41.0 ms | 17.8 ms | Faster by 23.2 ms |
| 23 Jan 2026 | Willow wan2 | 18.6 ms | 31.0 ms | Slower by 12.4 ms |
| 2 Feb 2026 | Willow wan2 | 31.0 ms | 18.5 ms | Faster by 12.5 ms |
| 11 Mar 2026 | Willow wan1 | 4.0 ms | 15.2 ms | Slower by 11.2 ms |
| 11 Jun 2026 | Willow wan2 | 18.5 ms | 5.6 ms | Faster by 12.9 ms |

</details>

### Sustained shift: Willow wan1

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/worse-willow-dark.png">
  <img alt="Daily median latency of Willow wan1 for a year. It holds near 4.0 ms until 11 March, steps to 15.2 ms on that day, and stays there through September." src="docs/img/worse-willow-light.png">
</picture>

*Figure 3. Willow wan1: daily median latency, with the sustained level on each side of the detected shift.*

Willow wan1 is the only link classed as Worse (Figure 3). Its median latency went from 4.0 ms to 15.2 ms on 11 March and has not moved since. A 150 ms alarm would never have fired: the link did not spend a single minute above 150 ms all year.

### Gradual rise: Cypress wan2

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/drifting-cypress-dark.png">
  <img alt="Daily 95th percentile latency of Cypress wan2 for a year, with a rising trend line. The daily values are higher over the winter, lower in the spring, and higher again from May." src="docs/img/drifting-cypress-light.png">
</picture>

*Figure 4. Cypress wan2: daily 95th-percentile latency, with a Theil-Sen trend line fitted to the weekly values.*

Cypress wan2 is the only link classed as Drifting (Figure 4). No single shift on it is large enough to record, but the trend test finds a rise in both series. Its median went from 30.9 ms in the first four weeks to 36.7 ms in the last four, and its slow end from 40.1 ms to 48.6 ms (Mann-Kendall p below 0.001 for both).

It is not a smooth drift. The daily values rose over the winter, fell back in the spring, and stepped up again in May. The example year has no link that drifted smoothly, so that case is covered by the injection test below.

### Outages

Figure 5 ranks the links by total downtime, and Table 3 lists the longest single outages. A gap shared by most of an organization's links is treated as the collector being down, and is not counted against any site.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/downtime-by-link-dark.png">
  <img alt="Bar chart of downtime over the year for the twelve links with the most. Pine wan1 leads with 150 hours across 21 outages. Spruce wan1 and Cypress wan1 follow with about 30 hours each." src="docs/img/downtime-by-link-light.png">
</picture>

*Figure 5. Total downtime by link over the year, for the twelve links with the most.*

*Table 3. The six longest outages of the year.*

| Site | Link | Started (UTC) | Lasted |
|---|---|---|---:|
| Pine | wan1 | 4 Oct 2025, 14:26 | 52 h 3 min |
| Pine | wan1 | 25 Oct 2025, 17:42 | 47 h 25 min |
| Spruce | wan1 | 4 Jul 2026, 17:51 | 26 h 27 min |
| Cypress | wan1 | 8 Sep 2026, 16:20 | 24 h 34 min |
| Pine | wan1 | 16 Oct 2025, 21:25 | 19 h 20 min |
| Cedar | wan1 | 13 Jun 2026, 00:10 | 16 h 31 min |

### Dual-uplink site: Cypress

Where a site has two uplinks, they are reported side by side (Figure 6). Here wan1 reports total loss for a day while wan2 stays up.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/two-links-outage-dark.png">
  <img alt="Seven days of hourly latency at Cypress. wan1 runs near 15 ms, then reports total loss for 24 hours 34 minutes. wan2 stays between 33 and 45 ms throughout." src="docs/img/two-links-outage-light.png">
</picture>

*Figure 6. Cypress wan1 and wan2: hourly median latency over the final seven days. The shaded band is the detected wan1 outage.*

### Latency distribution by link

Figure 7 shows one box per link for the whole year. The mark is the median. The box holds the middle half of all minutes, and the line runs from the 5th to the 95th percentile. The widest boxes and the longest lines belong to links that spent part of the year at a different level, such as Willow wan1 and Larch. A link's ordinary variation widens its box too, as at Pine.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/latency-boxes-dark.png">
  <img alt="Box plot of latency for 23 links over the year, sorted by median. Most links are narrow boxes between 5 and 18 ms. Willow wan1 and wan2 have wide boxes because each spent months at two different levels. Larch, Maple, Alder and Chestnut have long lines reaching 40 to 90 ms. Cypress wan2, Pine and Hemlock sit apart at about 34, 49 and 75 ms." src="docs/img/latency-boxes-light.png">
</picture>

*Figure 7. Latency distribution by link over the year: median, interquartile range, and 5th to 95th percentile.*

## Validation

A method that looks right on a finished year can still perform badly in use. Four checks were run on the example data. The first three are in `validate.py`, so they can be run on any other data: `python validate.py`.

### Injection test

Nobody labeled the example year, so there is no list of known incidents to score against. The injection test makes its own. It plants a change of known size and date in a link that is otherwise steady, runs the method, and checks what comes back.

The test used 8 links: those with no level shift, no flag at any point in the year, and less than 1.5 ms of rise or fall of their own. A step was added to both daily series from one day onward, at 15 dates and 9 sizes, 1,080 runs in all.

*Table 4. Planted steps and what the method reported.*

| Planted step | Runs | Shift found | Date exact | Flagged |
|---|---|---|---|---|
| 1 or 2 ms (below the minimum) | 240 | 0 | - | 11 |
| 3 ms (the minimum) | 120 | 50 | 96% | 68 |
| 4 ms | 120 | 109 | 98% | 110 |
| 6 to 20 ms | 600 | 600 | 99% | 600 |

- **Every step of 6 ms or more was found**, 99% of them on the exact day and all within 6 days.
- **At 3 ms, which is the minimum, about half were found.** That is what a threshold does.
- **Below the minimum, no shift was reported.** 11 of those 240 runs were still classed as Drifting, all on Fir wan1. Its slow end already rose by 1.4 ms over the year, and the planted 2 ms took the total over the 3 ms line.
- **A large step is reported after about a week.** A 10 ms step was found after a median of 7 days of data (6 to 9 days across 120 runs). The 14-day comparison responds once half of its window is past the change.

Ramps were planted the same way: a straight rise from a start date to the end of the data, at four start dates and five sizes, 160 runs in all.

*Table 5. Planted ramps and what the method reported.*

| Total rise | Runs | Flagged |
|---|---|---|
| 2 ms | 32 | 4 |
| 4 ms | 32 | 27 |
| 6 ms | 32 | 31 |
| 10 or 15 ms | 64 | 64 |

Every flagged ramp was found by the trend test, not as a level shift.

The test has limits of its own. A planted change adds the same amount to the median and to the slow end, which is how a pure increase in delay would look. On a live link, a change can alter the spread as well. The steady links are also the quiet ones.

### Walk-forward backtest

The method was run again at nine dates 30 days apart, from 13 January 2026 to 10 September 2026, each time using only the data that existed on that date. Table 6 lists the links it flagged.

A simple monthly rule was run the same way as a reference. It compares 30-day periods, on the median and on the slow end. A link is Worse when its latest period is 50% and 10 ms above its best period so far, and Drifting when the middle one of its last three periods is 10% and 3 ms above it.

*Table 6. Links flagged at each date. In the first column, links classed as Worse are in bold.*

| Data up to | This method | Monthly rule |
|---|---|---|
| 13 Jan 2026 | Cypress wan1, Cypress wan2 | Alder wan1, Chestnut wan1, Hemlock wan1, Larch wan1, Maple wan1 |
| 12 Feb 2026 | Cypress wan1, Cypress wan2, Willow wan2 | Chestnut wan1, Cypress wan2, Willow wan2 |
| 14 Mar 2026 | Cypress wan1 | Cypress wan2, Laurel wan1, Willow wan1 |
| 13 Apr 2026 | Cypress wan1, **Willow wan1** | Cypress wan2, Willow wan1 |
| 13 May 2026 | Cypress wan1, **Willow wan1** | Cypress wan2, Willow wan1 |
| 12 Jun 2026 | Cypress wan1, Cypress wan2, **Willow wan1** | Cypress wan2, Willow wan1 |
| 12 Jul 2026 | Cypress wan2, **Willow wan1** | Chestnut wan1, Cypress wan2, Larch wan1, Willow wan1, Willow wan2 |
| 11 Aug 2026 | Cypress wan2, **Willow wan1** | Aspen wan1, Cypress wan1, Cypress wan2, Willow wan1 |
| 10 Sep 2026 | Cypress wan2, **Willow wan1** | Cypress wan2, Willow wan1 |

| | This method | Monthly rule |
|---|---|---|
| Flags raised | 19 | 28 |
| Links ever flagged | 4 | 11 |

*Table 7. What the method's 19 flags were.*

| Link | Flags | What they were |
|---|---|---|
| Willow wan1 | 6 | The shift of 11 March, flagged at every date from 13 April on. |
| Cypress wan2 | 6 | A rise over the winter that fell back (flagged in January and February), then the rise that is still there (flagged from June on). |
| Cypress wan1 | 6 | A rise of 3 to 4 ms in its slow end, right at the threshold, flagged from January to June. It is not flagged at year end. |
| Willow wan2 | 1 | Flagged once, on 12 February, 10 days after a 10-day slowdown had ended, because the recovery could not yet be confirmed. |

- **Fewer flags is not proof of better.** Without labels, nobody can say which flags were right. What the tables show is that the method's flags fall on four links, and each can be traced to a change in that link's data.
- **The monthly rule flagged links that had just improved.** Alder, Hemlock, Larch and Maple were all flagged in January, after each of them had got faster. The 30-day period that contains an improvement still has more than 5% of its minutes at the old level, which keeps its 95th percentile high, and the rule looks back three periods.
- **The monthly rule is quicker on a large shift.** For the same reason in reverse, it flagged Willow wan1 on 14 March, 3 days after the shift. This method flagged it at the next date.
- **The thresholds were chosen on this same year**, so the backtest is not an out-of-sample test.

### Sensitivity to thresholds

Both pairs of thresholds were loosened and then tightened, nine combinations in all. Willow wan1 and Cypress wan2 were flagged in every one. The loosest setting for Drifting (5% and 2 ms) adds Cypress wan1. The strictest setting for Worse (100% and 15 ms) moves Willow wan1 down to Drifting, because its rise is 11 ms.

### Comparison with a fixed threshold

Over the year, a 150 ms alarm would have fired on 0 minutes for Willow wan1 and 2 minutes for Cypress wan2, the two flagged links. It would have fired on 2,860 minutes for Aspen wan1, 2,172 for Pine wan1 and 1,097 for Laurel wan1. Those minutes fell on 16, 27 and 7 days of the year, and all three links end the year classed as Steady. They are bursts, not a change of level.

## Limitations

- **Latency only.** The verdict looks at latency. Outages and loss are counted and reported, but a link that keeps dropping is not flagged for it.
- **Nobody labeled the year.** The injection test supplies changes of known size, but only simple ones. The backtest shows what would have been reported, not whether it was right.
- **The thresholds are judgment.** They were chosen to be useful on this data, and they are settings, not constants.
- **One test address.** Latency is measured from the site to one address on the internet, so it includes the path beyond the ISP. A shift shared by many links may have nothing to do with any of them.
- **One reading a minute.** Each reading is already an average over the minute. Anything shorter is invisible, and the spread between the median and the slow end is not packet jitter.
- **One to two weeks.** A large shift is reported about a week after it happens, and one near the minimum takes up to two. A slowdown shorter than about a week is not reported as a shift. Outages and bad minutes cover the short events.
- **Dates are estimates.** A shift date is exact for a clean step and less sure for a change that took several days. A change in the first week of data cannot be seen at all.
- **Trend tests assume independent readings.** Weekly latency values are not independent, so the p-values are optimistic. That is why a rise needs p below 0.001 and a minimum size.
- **No readings count as down.** The data cannot tell a WAN failure from a power cut or an unplugged device. A fault that hit most of an organization's sites at once would be taken for a collector gap. An organization with fewer than three links cannot be checked this way, so a collector failure there is counted as an outage.
- **One year, 20 sites.** The example shows what the method does. It is not a benchmark.

## Collector

`collect.py` is the cron job. Each run asks for the last five minutes, so runs four minutes apart overlap by a minute and a late run loses nothing. Readings that are already stored are skipped.

```sh
python collect.py                    # one run
python collect.py --backfill 14d     # also fetch history: up to the 60 days Meraki keeps
python collect.py --quiet            # print errors only
```

- **Missed runs are filled in.** If the collector was stopped for a while, the next runs ask Meraki for the minutes in between, 30 five-minute slices per run, until the hole is closed. Each slice is stored as it arrives, so a run that is interrupted loses nothing and the next one carries on. `--no-catch-up` turns this off.
- **Backfill gives a new install its history.** The verdicts need three weeks of data before they say anything. `--backfill 60d` fetches what Meraki still has. That is about 17,000 requests for each organization, so it takes a while. If it is stopped part-way, the scheduled runs finish the job.
- **Organizations are read one at a time.** If one fails, for example because its API access is off, the others are still collected. The run exits with a non-zero status only when none could be read.
- **Settings** come from the environment or from `.env` beside the scripts: `MERAKI_API_KEY`, and optionally `MERAKI_ORG_ID` (collect from these organizations only), `WAN_STATS_DB` and `WAN_STATS_KEEP_DAYS` (delete readings older than this; the default keeps everything). [`.env.example`](.env.example) lists them.

## Console report

`report.py` prints the verdicts and one period's numbers as text. This is the report for the last week of the example data:

```text
WAN stats, 7 days to 2026-09-10 16:23 UTC                               23 links, 2 flagged

WORSE     Willow wan1   median 4.0 -> 15.2 ms on 11 Mar 2026
DRIFTING  Cypress wan2  median 30.9 -> 36.7 ms, +0.5 ms/month; p95 40.1 -> 48.6 ms, +0.9 ms/month

Example Org A
Site     Link     Median      p95   Spread   Bad min   Outages     Down     Avail   Latency
Alder    wan1    17.7 ms     18.2      0.5      0.3%         0        -   100.00%   steady
Aspen    wan1    17.1 ms     18.0      0.9      0.4%         0        -   100.00%   steady
Cedar    wan1     7.6 ms      8.0      0.4      0.2%         0        -   100.00%   steady
Chestnut wan1    17.8 ms     18.3      0.5      0.2%         0        -   100.00%   steady
Cypress  wan1    14.6 ms     16.8      2.2     14.7%         1    24h34    85.38%   steady
Cypress  wan2    36.4 ms     48.5     12.1      0.1%         0        -   100.00%   DRIFTING
Hawthorn wan1     5.9 ms      5.9      0.0      0.2%         0        -   100.00%   steady
Hemlock  wan1    74.8 ms     83.9      9.1      0.3%         0        -   100.00%   steady
Larch    wan1     6.8 ms      7.1      0.3      0.1%         0        -   100.00%   steady
Laurel   wan1    16.9 ms     17.7      0.8      0.9%         0        -   100.00%   steady
Maple    wan1    15.8 ms     16.4      0.6      0.3%         0        -   100.00%   steady
Pine     wan1    49.1 ms     57.6      8.5      0.1%         1       6m    99.94%   steady
Spruce   wan1    16.5 ms     17.0      0.5      0.3%         0        -   100.00%   steady
Willow   wan1    15.5 ms     16.4      0.9      0.3%         0        -   100.00%   WORSE
Willow   wan2     5.7 ms      5.9      0.2      0.1%         0        -   100.00%   steady
Yew      wan1     5.6 ms      6.1      0.5      1.1%         0        -   100.00%   steady
Yew      wan2     4.8 ms      5.2      0.4      1.3%         1       3m    99.97%   steady

Example Org B
Site     Link     Median      p95   Spread   Bad min   Outages     Down     Avail   Latency
Buckeye  wan1    13.2 ms     14.2      1.0      0.4%         0        -   100.00%   steady
Dogwood  wan1     5.8 ms      5.9      0.1      0.1%         0        -   100.00%   steady
Fir      wan1    10.4 ms     11.3      0.9      0.1%         0        -   100.00%   steady
Locust   wan1    13.9 ms     14.7      0.8      0.2%         0        -   100.00%   steady
Magnolia wan1    15.2 ms     15.8      0.6      0.1%         0        -   100.00%   steady
Redwood  wan1    15.8 ms     16.6      0.8      0.2%         1       3m    99.97%   steady
```

| Column | Meaning |
|---|---|
| Median, p95 | The typical minute and the slow end, in milliseconds. |
| Spread | p95 minus the median. |
| Bad min | Share of minutes with 2% loss or more, or latency over 150 ms. |
| Outages, Down | Outages of three minutes or longer in the period, and their total length. |
| Avail | Share of the period the link was not in an outage. |
| Latency | The latency verdict: `steady`, `DRIFTING` or `WORSE`, or `new` for a link with less than three weeks of history. Flagged links are also listed at the top, with the size of the change. Outages do not affect it. |

```sh
python report.py                 # the last 7 days
python report.py --days 30       # a longer period in the table
python report.py --history 180   # let the verdicts look back 180 days, not at everything stored
```

The exit status follows the convention monitoring systems use, so the report can run as a scheduled check: 0 when no link is flagged, 1 when a link is Drifting, 2 when a link is Worse, and 3 when there is nothing to report on.

## Notebook and PDF report

[`wan-stats.ipynb`](wan-stats.ipynb) is the full report, one step per section: data checks, every link by week, flagged links, level shifts, the weekly summary, outages, sites with two uplinks, and the latency distribution. Each step is one line that calls the statistics, so the notebook is also the place to start your own analysis.

```sh
jupyter notebook wan-stats.ipynb                                           # run it live
jupyter nbconvert --to webpdf --no-input --execute wan-stats.ipynb         # wan-stats.pdf
jupyter nbconvert --to html --no-input --execute wan-stats.ipynb           # wan-stats.html
```

`--no-input` hides the code, which leaves a report anyone can read. The PDF export draws the page with a headless Chromium. The first time, add `--allow-chromium-download` and nbconvert fetches one.

## Storage

SQLite is the default. It is a single file with nothing to install or configure, so the collector works as soon as it has an API key. A year of readings from 20 sites is about 0.3 GB.

If you would rather keep the readings in a database server, PostgreSQL and MongoDB are also supported. Switching is one setting, and the reports work the same on all three:

```ini
# .env: use one of these
WAN_STATS_DB=sqlite:///wan-stats.sqlite                      # the default
# WAN_STATS_DB=postgresql://user:password@db-host/wan_stats
# WAN_STATS_DB=mongodb://user:password@db-host/wan_stats
```

| Database | Also install | What is stored |
|---|---|---|
| SQLite | nothing | Two tables: `uplinks` has one row per link, and `readings` has one narrow row per link per minute. A relative path is relative to the `wan-stats` folder. |
| PostgreSQL | `pip install "psycopg[binary]"` | The same two tables. The database must exist, and the tables are created on the first run. |
| MongoDB | `pip install pymongo` | One document per reading in the `uplink_data` collection, with the field names the Meraki API uses. A unique index keeps a reading from being stored twice. |

A password with special characters has to be percent-encoded in the URL.

## Settings

The thresholds in Table 1 are the defaults of one small class. To try other values, pass a changed copy in the notebook:

```python
from wanstats.analysis import Settings, analyse

a = analyse(store.load(), Settings(worse=(2.0, 15.0), drifting=(1.2, 5.0)))
```

`python validate.py` shows how the verdicts on your own data move under looser and stricter thresholds.

## Tests

```sh
pip install -r requirements-dev.txt
python -m pytest tests
```

The tests cover the statistics, the collector, the three databases, the console report, the charts and the notebook. The PostgreSQL and MongoDB tests run when `WAN_STATS_TEST_POSTGRES` and `WAN_STATS_TEST_MONGODB` name a scratch database, as the Build workflow does.

[Back to Meraki Tools](../README.md)
