"""
Focused NIFTY 50 3 PM alpha candidate study.

This intentionally removes the broad parameter sweep from the old v2 study.
The only candidate kept here is:

    prior day 3 PM bullish candle -> 3:15 bearish candle,
    followed by a next-day gap-up.

The prior 3 PM close touch/no-touch split is retained because it explains the
trade behavior rather than adding a new fitted filter.
"""

from pathlib import Path
from typing import Any
import html

import numpy as np
import pandas as pd

from market_data.data_loader import IS_LABEL, load_nifty_15min


OUT_DIR = Path("reports") / "research" / "nifty_3pm_v2"
REPORT_DIR = OUT_DIR / "summaries"
CHART_DIR = REPORT_DIR / "charts"

DETAIL_OUT = OUT_DIR / "v2_daily_observations.csv"
EDGE_TABLES_OUT = OUT_DIR / "v2_edge_tables.csv"
CANDIDATE_OUT = OUT_DIR / "v2_candidate_edges.csv"

MAIN_SETUP = "bullish_bearish"


def direction(value: float, up: str, down: str, flat: str) -> str:
    if pd.isna(value):
        return "na"
    if value > 0:
        return up
    if value < 0:
        return down
    return flat


def candle_color(row: pd.Series) -> str:
    return direction(row["close"] - row["open"], "bullish", "bearish", "doji")


def candle_features(df: pd.DataFrame, clock: str, prefix: str) -> pd.DataFrame:
    frame = df[df["datetime"].dt.strftime("%H:%M:%S") == clock].copy()
    frame[f"{prefix}_color"] = frame.apply(candle_color, axis=1)
    frame[f"{prefix}_body_pts"] = frame["close"] - frame["open"]
    frame[f"{prefix}_body_pct"] = frame[f"{prefix}_body_pts"] / frame["open"] * 100
    frame[f"{prefix}_range_pts"] = frame["high"] - frame["low"]
    frame[f"{prefix}_close_location"] = (
        (frame["close"] - frame["low"])
        / frame[f"{prefix}_range_pts"].replace(0, pd.NA)
    )
    return (
        frame.set_index("date")[
            [
                "open",
                "high",
                "low",
                "close",
                f"{prefix}_color",
                f"{prefix}_body_pts",
                f"{prefix}_body_pct",
                f"{prefix}_range_pts",
                f"{prefix}_close_location",
            ]
        ]
        .rename(
            columns={
                "open": f"{prefix}_open",
                "high": f"{prefix}_high",
                "low": f"{prefix}_low",
                "close": f"{prefix}_close",
            }
        )
    )


def touch_stats(next_bars: pd.DataFrame, level: float) -> dict[str, Any]:
    hit = next_bars[(next_bars["high"] >= level) & (next_bars["low"] <= level)]
    if hit.empty:
        return {
            "touched": False,
            "first_touch_time": pd.NA,
            "first_touch_bar": pd.NA,
            "touch_close": pd.NA,
            "touch_close_vs_level_pct": pd.NA,
            "post_touch_high_pct": pd.NA,
            "post_touch_low_pct": pd.NA,
        }

    pos = next_bars.index.get_loc(hit.index[0])
    first_touch = hit.iloc[0]
    after_touch = next_bars.iloc[pos:]
    return {
        "touched": True,
        "first_touch_time": first_touch["datetime"],
        "first_touch_bar": pos + 1,
        "touch_close": first_touch["close"],
        "touch_close_vs_level_pct": (first_touch["close"] - level) / level * 100,
        "post_touch_high_pct": (after_touch["high"].max() - level) / level * 100,
        "post_touch_low_pct": (after_touch["low"].min() - level) / level * 100,
    }


def touch_period_label(bar_number: Any) -> str:
    if pd.isna(bar_number):
        return "not_touched"
    number = int(bar_number)
    if number <= 5:
        return "opening_5"
    if number <= 12:
        return "midday"
    if number <= 20:
        return "afternoon"
    return "closing_hour"


def summarize_slice(rows: pd.DataFrame, label: str) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {
            "edge": label,
            "n": 0,
            "gap_up_rate": np.nan,
            "next_up_rate": np.nan,
            "avg_gap_pct": np.nan,
            "avg_oc_pct": np.nan,
            "median_oc_pct": np.nan,
            "avg_total_pct": np.nan,
            "touch_rate": np.nan,
            "close_above_level_rate": np.nan,
        }

    return {
        "edge": label,
        "n": n,
        "gap_up_rate": rows["gap_dir"].eq("gap_up").mean() * 100,
        "next_up_rate": rows["next_intraday_dir"].eq("up_day").mean() * 100,
        "avg_gap_pct": rows["gap_pct"].mean(),
        "avg_oc_pct": rows["next_intraday_pct"].mean(),
        "median_oc_pct": rows["next_intraday_pct"].median(),
        "avg_total_pct": rows["next_total_pct"].mean(),
        "touch_rate": rows["three_close_touched_next_day"].mean() * 100,
        "close_above_level_rate": rows["next_close_vs_three_close_dir"].eq("above").mean() * 100,
    }


def grouped_summary(obs: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    grouped = obs.groupby(group_cols, dropna=False)
    out = grouped.agg(
        n=("gap_pct", "size"),
        gap_up_rate=("gap_dir", lambda s: (s == "gap_up").mean() * 100),
        next_up_rate=("next_intraday_dir", lambda s: (s == "up_day").mean() * 100),
        avg_gap_pct=("gap_pct", "mean"),
        avg_oc_pct=("next_intraday_pct", "mean"),
        median_oc_pct=("next_intraday_pct", "median"),
        avg_total_pct=("next_total_pct", "mean"),
        touch_rate=("three_close_touched_next_day", "mean"),
        close_above_level_rate=(
            "next_close_vs_three_close_dir",
            lambda s: (s == "above").mean() * 100,
        ),
    )
    out["touch_rate"] *= 100
    return out.reset_index().round(4)


def period_bucket(date_value: Any) -> str:
    year = pd.Timestamp(date_value).year
    if year <= 2018:
        return "2015-2018"
    if year <= 2022:
        return "2019-2022"
    return "2023+"


def pretty_label(value: object) -> str:
    labels = {
        "bullish_bearish": "bullish then bearish",
        "bullish_bullish": "bullish then bullish",
        "bearish_bearish": "bearish then bearish",
        "bearish_bullish": "bearish then bullish",
        "gap_up": "gap up",
        "gap_down": "gap down",
        "opening_5": "opening 5 bars",
        "not_touched": "not touched",
    }
    if pd.isna(value):
        return ""
    text = str(value)
    return labels.get(text, text.replace("_", " "))


def fmt(value: object, suffix: str = "", decimals: int = 2) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, np.integer)):
        return f"{value}{suffix}"
    if isinstance(value, (float, np.floating)):
        return f"{value:.{decimals}f}{suffix}"
    return pretty_label(value)


def markdown_table(frame: pd.DataFrame, cols: list[tuple[str, str, str, int]]) -> str:
    headers = [header for _, header, _, _ in cols]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, row in frame.iterrows():
        lines.append(
            "| "
            + " | ".join(fmt(row[col], suffix, decimals) for col, _, suffix, decimals in cols)
            + " |"
        )
    return "\n".join(lines)


def bar_chart(
    title: str,
    rows: list[dict[str, object]],
    value_key: str,
    label_key: str,
    suffix: str = "%",
    decimals: int = 3,
    color: str = "#2563eb",
) -> str:
    if not rows:
        return ""
    max_value = max(abs(float(row[value_key])) for row in rows) or 1
    items = []
    for row in rows:
        value = float(row[value_key])
        width = max(2, abs(value) / max_value * 100)
        bar_color = color if value >= 0 else "#dc2626"
        label = html.escape(pretty_label(row[label_key]))
        items.append(
            f"""
            <div class="bar-row">
              <div class="bar-label">{label}</div>
              <div class="bar-track"><div class="bar" style="width:{width:.1f}%;background:{bar_color};"></div></div>
              <div class="bar-value">{value:.{decimals}f}{suffix}</div>
            </div>"""
        )
    return f"""
    <section class="chart-section">
      <h2>{html.escape(title)}</h2>
      {''.join(items)}
    </section>"""


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def build_observations() -> pd.DataFrame:
    nifty = load_nifty_15min()

    daily = nifty.groupby("date").agg(
        day_open=("open", "first"),
        day_high=("high", "max"),
        day_low=("low", "min"),
        day_close=("close", "last"),
        bars=("datetime", "size"),
    )
    dates = list(daily.index)
    daily["next_date"] = dates[1:] + [pd.NA]

    three_pm = candle_features(nifty, "15:00:00", "three")
    three_15 = candle_features(nifty, "15:15:00", "three_fifteen")

    obs = daily.join(three_pm, how="inner").join(three_15, how="left")
    obs = obs.join(daily.drop(columns=["next_date"]).shift(-1).add_prefix("next_"))
    obs = obs.dropna(subset=["next_day_open", "next_day_close"])

    obs["late_2bar_color_combo"] = (
        obs["three_color"] + "_" + obs["three_fifteen_color"].fillna("missing")
    )
    obs["late_2bar_pts"] = obs["three_fifteen_close"] - obs["three_open"]
    obs["late_2bar_pct"] = obs["late_2bar_pts"] / obs["three_open"] * 100

    obs["gap_pts"] = obs["next_day_open"] - obs["day_close"]
    obs["gap_pct"] = obs["gap_pts"] / obs["day_close"] * 100
    obs["gap_dir"] = obs["gap_pts"].apply(lambda v: direction(v, "gap_up", "gap_down", "flat"))

    obs["next_intraday_pts"] = obs["next_day_close"] - obs["next_day_open"]
    obs["next_intraday_pct"] = obs["next_intraday_pts"] / obs["next_day_open"] * 100
    obs["next_intraday_dir"] = obs["next_intraday_pts"].apply(
        lambda v: direction(v, "up_day", "down_day", "flat_day")
    )

    obs["next_total_pts"] = obs["next_day_close"] - obs["day_close"]
    obs["next_total_pct"] = obs["next_total_pts"] / obs["day_close"] * 100

    obs["next_open_vs_three_close_pct"] = (
        obs["next_day_open"] - obs["three_close"]
    ) / obs["three_close"] * 100
    obs["next_close_vs_three_close_pct"] = (
        obs["next_day_close"] - obs["three_close"]
    ) / obs["three_close"] * 100
    obs["next_close_vs_three_close_dir"] = obs["next_close_vs_three_close_pct"].apply(
        lambda v: direction(v, "above", "below", "at_level")
    )

    bars_by_date = {date: group.reset_index(drop=True) for date, group in nifty.groupby("date")}
    touch_rows: list[dict[str, object]] = []
    for date, row in obs.iterrows():
        next_bars = bars_by_date.get(row["next_date"])
        if next_bars is None:
            continue
        touch = touch_stats(next_bars, row["three_close"])
        touch_rows.append(
            {
                "date": date,
                "three_close_touched_next_day": touch["touched"],
                "first_touch_time": touch["first_touch_time"],
                "first_touch_bar": touch["first_touch_bar"],
                "touch_close": touch["touch_close"],
                "touch_close_vs_level_pct": touch["touch_close_vs_level_pct"],
                "post_touch_high_pct": touch["post_touch_high_pct"],
                "post_touch_low_pct": touch["post_touch_low_pct"],
            }
        )

    touch_frame = pd.DataFrame(touch_rows).set_index("date")
    obs = obs.join(touch_frame, how="left")
    obs["three_close_first_touch_period"] = obs["first_touch_bar"].apply(touch_period_label)
    obs["after_touch_close_vs_touch_pct"] = (
        (obs["next_day_close"] - obs["touch_close"]) / obs["touch_close"] * 100
    )
    obs["is_main_candidate"] = (
        obs["late_2bar_color_combo"].eq(MAIN_SETUP) & obs["gap_dir"].eq("gap_up")
    )
    obs["period"] = [period_bucket(d) for d in obs.index]
    return obs


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    obs = build_observations()

    focused_cols = [
        "date",
        "day_open",
        "day_high",
        "day_low",
        "day_close",
        "bars",
        "next_date",
        "three_open",
        "three_high",
        "three_low",
        "three_close",
        "three_color",
        "three_body_pct",
        "three_range_pts",
        "three_fifteen_open",
        "three_fifteen_high",
        "three_fifteen_low",
        "three_fifteen_close",
        "three_fifteen_color",
        "late_2bar_color_combo",
        "late_2bar_pct",
        "gap_pts",
        "gap_pct",
        "gap_dir",
        "next_day_open",
        "next_day_high",
        "next_day_low",
        "next_day_close",
        "next_intraday_pct",
        "next_intraday_dir",
        "next_total_pct",
        "next_open_vs_three_close_pct",
        "next_close_vs_three_close_pct",
        "next_close_vs_three_close_dir",
        "three_close_touched_next_day",
        "first_touch_time",
        "first_touch_bar",
        "three_close_first_touch_period",
        "after_touch_close_vs_touch_pct",
        "is_main_candidate",
        "period",
    ]
    obs.reset_index().rename(columns={"index": "date"})[focused_cols].to_csv(
        DETAIL_OUT, index=False
    )

    baseline = pd.DataFrame([summarize_slice(obs, "baseline_all_days")])
    gap_up = pd.DataFrame([summarize_slice(obs[obs["gap_dir"].eq("gap_up")], "all_gap_up_days")])
    main_candidate = pd.DataFrame(
        [summarize_slice(obs[obs["is_main_candidate"]], "main_candidate")]
    )
    setup_no_gap_filter = pd.DataFrame(
        [
            summarize_slice(
                obs[obs["late_2bar_color_combo"].eq(MAIN_SETUP)],
                "bullish_then_bearish_all_gaps",
            )
        ]
    )

    touch_by_gap = grouped_summary(obs[obs["gap_dir"].isin(["gap_up", "gap_down"])], [
        "gap_dir",
        "three_close_touched_next_day",
    ])
    touch_by_gap["edge"] = "touch_by_gap"

    candidate_touch = grouped_summary(obs[obs["is_main_candidate"]], [
        "three_close_touched_next_day",
    ])
    candidate_touch["edge"] = "main_candidate_touch_split"

    period_validation = grouped_summary(obs[obs["is_main_candidate"]], ["period"])
    period_validation["edge"] = "main_candidate_period_validation"

    combo_gap = grouped_summary(obs, ["late_2bar_color_combo", "gap_dir"])
    combo_gap["edge"] = "late_combo_gap_context"

    all_edges = pd.concat(
        [
            baseline,
            gap_up,
            setup_no_gap_filter,
            main_candidate,
            touch_by_gap,
            candidate_touch,
            period_validation,
            combo_gap,
        ],
        ignore_index=True,
        sort=False,
    ).round(4)
    all_edges.to_csv(EDGE_TABLES_OUT, index=False)

    candidates = pd.concat([main_candidate, candidate_touch, period_validation], ignore_index=True)
    candidates.to_csv(CANDIDATE_OUT, index=False)

    total = len(obs)
    date_start = obs.index.min()
    date_end = obs.index.max()
    gap_up_rate = obs["gap_dir"].eq("gap_up").mean() * 100
    next_up_rate = obs["next_intraday_dir"].eq("up_day").mean() * 100
    avg_oc = obs["next_intraday_pct"].mean()

    main_rows = obs[obs["is_main_candidate"]]
    main_touch = candidate_touch.copy().sort_values("three_close_touched_next_day")
    period_validation = period_validation.sort_values("period")
    touch_by_gap = touch_by_gap.sort_values(["gap_dir", "three_close_touched_next_day"])

    core_table = pd.concat(
        [baseline, gap_up, setup_no_gap_filter, main_candidate],
        ignore_index=True,
    ).round(4)

    combo_context = combo_gap[
        combo_gap["late_2bar_color_combo"].isin(
            ["bullish_bearish", "bullish_bullish", "bearish_bearish", "bearish_bullish"]
        )
        & combo_gap["gap_dir"].isin(["gap_up", "gap_down"])
    ].sort_values(["late_2bar_color_combo", "gap_dir"])

    main_report = f"""
# NIFTY 50 3 PM Focused Alpha Candidate

Dataset: `NIFTY 50 15min ({IS_LABEL})` | Period: `{date_start}` to `{date_end}` | N = `{total:,}` days

## Baseline

- Gap-up rate: **{gap_up_rate:.1f}%**
- Next-day open-to-close up rate: **{next_up_rate:.1f}%**
- Average next-day O-C: **{avg_oc:.4f}%**

## Kept Candidate

The only active alpha candidate is:

`3 PM bullish candle -> 3:15 bearish candle -> next-day gap-up`

This is the late-session failed-strength setup. It keeps the idea simple: price showed strength into 3 PM, failed by 3:15, then opened higher next day. The test is whether that gap is faded intraday, with the prior 3 PM close acting as the decision level.

{markdown_table(core_table, [
    ("edge", "Slice", "", 2),
    ("n", "N", "", 0),
    ("gap_up_rate", "Gap up", "%", 1),
    ("next_up_rate", "Next day up", "%", 1),
    ("avg_gap_pct", "Avg gap", "%", 3),
    ("avg_oc_pct", "Avg O-C", "%", 3),
    ("median_oc_pct", "Median O-C", "%", 3),
    ("touch_rate", "Touch rate", "%", 1),
])}

## Candidate Touch Split

The prior 3 PM close is retained as the execution/diagnostic level, not as another curve-fit parameter.

{markdown_table(main_touch, [
    ("three_close_touched_next_day", "Touched prior 3 PM close", "", 2),
    ("n", "N", "", 0),
    ("next_up_rate", "Next day up", "%", 1),
    ("avg_oc_pct", "Avg O-C", "%", 3),
    ("median_oc_pct", "Median O-C", "%", 3),
    ("close_above_level_rate", "Close>level", "%", 1),
])}

## Period Stability

This is the quick anti-overfit check. The candidate should stay directionally similar across time, not live only in one pocket of history.

{markdown_table(period_validation, [
    ("period", "Period", "", 2),
    ("n", "N", "", 0),
    ("next_up_rate", "Next day up", "%", 1),
    ("avg_oc_pct", "Avg O-C", "%", 3),
    ("median_oc_pct", "Median O-C", "%", 3),
    ("touch_rate", "Touch rate", "%", 1),
])}

## Touch Mechanic Context

Across all gap-up/gap-down days, the same level behavior shows why the candidate is worth focusing on.

{markdown_table(touch_by_gap, [
    ("gap_dir", "Gap", "", 2),
    ("three_close_touched_next_day", "Touched prior 3 PM close", "", 2),
    ("n", "N", "", 0),
    ("next_up_rate", "Next day up", "%", 1),
    ("avg_oc_pct", "Avg O-C", "%", 3),
    ("median_oc_pct", "Median O-C", "%", 3),
])}

## Combo Context Only

This table is kept only to compare the chosen setup against the other final-two-candle combinations. It is not a request to add more filters.

{markdown_table(combo_context, [
    ("late_2bar_color_combo", "3 PM + 3:15 combo", "", 2),
    ("gap_dir", "Gap", "", 2),
    ("n", "N", "", 0),
    ("next_up_rate", "Next day up", "%", 1),
    ("avg_oc_pct", "Avg O-C", "%", 3),
    ("median_oc_pct", "Median O-C", "%", 3),
    ("touch_rate", "Touch rate", "%", 1),
])}

## Removed From v2

The broad parameter sweep has been removed from this report. The v2 output now keeps only the selected setup, its touch-level behavior, and period stability.

## Files

- Focused daily observations: `../../{DETAIL_OUT}`
- Focused edge tables: `../../{EDGE_TABLES_OUT}`
- Candidate table: `../../{CANDIDATE_OUT}`
"""
    write_text(REPORT_DIR / "v2_main_report.md", main_report)

    chart_rows = core_table[core_table["edge"].isin(
        ["baseline_all_days", "all_gap_up_days", "main_candidate"]
    )].to_dict("records")
    touch_chart_rows = touch_by_gap.copy()
    touch_chart_rows["label"] = touch_chart_rows.apply(
        lambda r: f"{pretty_label(r['gap_dir'])}, {'touched' if r['three_close_touched_next_day'] else 'not touched'}",
        axis=1,
    )

    css = """
    body{margin:0;font-family:Arial,sans-serif;color:#172033;background:#f6f7f9}
    main{max-width:1040px;margin:0 auto;padding:32px 20px 48px}
    h1{margin:0 0 8px;font-size:26px;letter-spacing:0}
    h2{margin:0 0 16px;font-size:18px;letter-spacing:0}
    .note{color:#536075;line-height:1.5}
    .chart-section{background:#fff;border:1px solid #d9dee7;border-radius:8px;padding:20px;margin-top:20px}
    .bar-row{display:grid;grid-template-columns:minmax(220px,1.1fr) minmax(180px,2fr) 84px;gap:12px;align-items:center;min-height:32px;margin:6px 0}
    .bar-label{font-size:13px}.bar-track{height:16px;background:#edf0f5;border-radius:4px;overflow:hidden}
    .bar{height:100%;border-radius:4px}.bar-value{text-align:right;font-variant-numeric:tabular-nums;font-size:13px}
    @media (max-width:720px){.bar-row{grid-template-columns:1fr}.bar-value{text-align:left}}
    """
    charts_html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>NIFTY 3 PM Focused Candidate</title>
<style>{css}</style></head><body><main>
<h1>NIFTY 3 PM Focused Candidate</h1>
<p class="note">Only the selected late failed-strength candidate and the prior 3 PM close touch mechanic are charted.</p>
{bar_chart("Avg Next-Day O-C %", chart_rows, "avg_oc_pct", "edge", "%", 3, "#2563eb")}
{bar_chart("Touch Mechanic: Avg Next-Day O-C %", touch_chart_rows.to_dict("records"), "avg_oc_pct", "label", "%", 3, "#0f766e")}
</main></body></html>"""
    write_text(CHART_DIR / "v2_charts.html", charts_html)

    print(f"Wrote {DETAIL_OUT}")
    print(f"Wrote {EDGE_TABLES_OUT}")
    print(f"Wrote {CANDIDATE_OUT}")
    print(f"Wrote {REPORT_DIR / 'v2_main_report.md'}")
    print(f"Wrote {CHART_DIR / 'v2_charts.html'}")
    print(
        "Main candidate: "
        f"{len(main_rows):,} observations, "
        f"avg O-C {main_rows['next_intraday_pct'].mean():.3f}%, "
        f"next-day up {main_rows['next_intraday_dir'].eq('up_day').mean() * 100:.1f}%"
    )


if __name__ == "__main__":
    main()
