from pathlib import Path
import html

import pandas as pd


from data_loader import load_nifty_15min  # noqa: E402  (after stdlib imports)
OUT_DIR = Path("data_quality_reports")
SUMMARY_DIR = OUT_DIR / "summaries"
CHART_DIR = SUMMARY_DIR / "charts"

DETAIL_OUT = OUT_DIR / "nifty_3pm_pattern_daily_observations.csv"
SUMMARY_OUT = OUT_DIR / "nifty_3pm_pattern_summary.csv"
TOUCH_OUT = OUT_DIR / "nifty_3pm_touch_reaction_summary.csv"
TOUCH_PERIOD_OUT = OUT_DIR / "nifty_3pm_touch_period_summary.csv"
COMBO_OUT = OUT_DIR / "nifty_3pm_315_combo_summary.csv"
CANDIDATE_OUT = OUT_DIR / "nifty_3pm_candidate_edges.csv"

MAIN_REPORT_OUT = SUMMARY_DIR / "nifty_3pm_alpha_report.md"
TOUCH_REPORT_OUT = SUMMARY_DIR / "nifty_3pm_touch_level_report.md"
COMBO_REPORT_OUT = SUMMARY_DIR / "nifty_3pm_315_combo_report.md"
INDEX_REPORT_OUT = SUMMARY_DIR / "README.md"
CHART_OUT = CHART_DIR / "nifty_3pm_charts.html"


def direction(value: float, up_label: str, down_label: str, flat_label: str) -> str:
    if pd.isna(value):
        return "not_available"
    if value > 0:
        return up_label
    if value < 0:
        return down_label
    return flat_label


def candle_color(row: pd.Series) -> str:
    return direction(row["close"] - row["open"], "bullish", "bearish", "doji")


def gap_bucket(gap_pct: float) -> str:
    if gap_pct <= -0.50:
        return "gap_down_big_le_-0.50"
    if gap_pct < -0.10:
        return "gap_down_mid_-0.50_to_-0.10"
    if gap_pct < 0:
        return "gap_down_tiny_0_to_-0.10"
    if gap_pct == 0:
        return "flat"
    if gap_pct < 0.10:
        return "gap_up_tiny_0_to_0.10"
    if gap_pct < 0.50:
        return "gap_up_mid_0.10_to_0.50"
    return "gap_up_big_ge_0.50"


def candle_features(df: pd.DataFrame, clock: str, prefix: str) -> pd.DataFrame:
    frame = df[df["datetime"].dt.strftime("%H:%M:%S") == clock].copy()
    frame[f"{prefix}_color"] = frame.apply(candle_color, axis=1)
    frame[f"{prefix}_body_pts"] = frame["close"] - frame["open"]
    frame[f"{prefix}_body_pct"] = frame[f"{prefix}_body_pts"] / frame["open"] * 100
    frame[f"{prefix}_range_pts"] = frame["high"] - frame["low"]
    frame[f"{prefix}_close_location"] = (
        (frame["close"] - frame["low"]) / frame[f"{prefix}_range_pts"].replace(0, pd.NA)
    )
    return frame.set_index("date")[
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
    ].rename(
        columns={
            "open": f"{prefix}_open",
            "high": f"{prefix}_high",
            "low": f"{prefix}_low",
            "close": f"{prefix}_close",
        }
    )


def touch_stats(next_bars: pd.DataFrame, level: float) -> dict[str, object]:
    touched = next_bars[(next_bars["high"] >= level) & (next_bars["low"] <= level)]
    if touched.empty:
        return {
            "touched": False,
            "first_touch_time": pd.NA,
            "first_touch_bar_number": pd.NA,
            "touch_bar_close": pd.NA,
            "touch_bar_close_vs_level_pct": pd.NA,
            "post_touch_high_vs_level_pct": pd.NA,
            "post_touch_low_vs_level_pct": pd.NA,
        }

    first_position = next_bars.index.get_loc(touched.index[0])
    after_touch = next_bars.iloc[first_position:]
    first_touch = touched.iloc[0]
    return {
        "touched": True,
        "first_touch_time": first_touch["datetime"],
        "first_touch_bar_number": first_position + 1,
        "touch_bar_close": first_touch["close"],
        "touch_bar_close_vs_level_pct": (first_touch["close"] - level) / level * 100,
        "post_touch_high_vs_level_pct": (after_touch["high"].max() - level) / level * 100,
        "post_touch_low_vs_level_pct": (after_touch["low"].min() - level) / level * 100,
    }


def touch_period(bar_number: object) -> str:
    if pd.isna(bar_number):
        return "not_touched"
    if bar_number <= 5:
        return "opening_5_bars"
    if bar_number <= 12:
        return "midday"
    if bar_number <= 20:
        return "afternoon"
    return "closing_hour"


def summarize(observations: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    grouped = observations.groupby(group_cols, dropna=False)
    summary = grouped.agg(
        n=("gap_pct", "size"),
        gap_up_rate=("gap_dir", lambda s: (s == "gap_up").mean() * 100),
        gap_down_rate=("gap_dir", lambda s: (s == "gap_down").mean() * 100),
        avg_gap_pct=("gap_pct", "mean"),
        median_gap_pct=("gap_pct", "median"),
        next_up_rate=("next_intraday_dir", lambda s: (s == "up_day").mean() * 100),
        next_down_rate=("next_intraday_dir", lambda s: (s == "down_day").mean() * 100),
        avg_next_intraday_pct=("next_intraday_pct", "mean"),
        median_next_intraday_pct=("next_intraday_pct", "median"),
        close_above_prior_rate=(
            "next_total_dir",
            lambda s: (s == "close_above_prior_close").mean() * 100,
        ),
        avg_next_total_pct=("next_total_pct", "mean"),
        touch_3pm_close_rate=("three_close_touched_next_day", "mean"),
        avg_close_vs_3pm_close_pct=("next_close_vs_three_close_pct", "mean"),
        close_above_3pm_close_rate=("next_close_vs_three_close_dir", lambda s: (s == "above").mean() * 100),
    )
    summary["touch_3pm_close_rate"] *= 100
    return summary.reset_index().round(4)


def pretty_label(value: object) -> str:
    if pd.isna(value):
        return ""
    labels = {
        "gap_down_big_le_-0.50": "gap down big <= -0.50%",
        "gap_down_mid_-0.50_to_-0.10": "gap down mid -0.50% to -0.10%",
        "gap_down_tiny_0_to_-0.10": "gap down tiny 0% to -0.10%",
        "gap_up_tiny_0_to_0.10": "gap up tiny 0% to 0.10%",
        "gap_up_mid_0.10_to_0.50": "gap up mid 0.10% to 0.50%",
        "gap_up_big_ge_0.50": "gap up big >= 0.50%",
        "gap_up": "gap up",
        "gap_down": "gap down",
        "up_day": "up day",
        "down_day": "down day",
        "bullish_bearish": "bullish then bearish",
        "bullish_bullish": "bullish then bullish",
        "bearish_bearish": "bearish then bearish",
        "bearish_bullish": "bearish then bullish",
        "bullish_30min": "bullish 30 min",
        "bearish_30min": "bearish 30 min",
        "opening_5_bars": "opening 5 bars",
        "closing_hour": "closing hour",
        "three_color_gap_bucket": "3 PM color + gap bucket",
        "late_combo_gap_bucket": "3 PM + 3:15 combo + gap bucket",
    }
    text = str(value)
    return labels.get(text, text.replace("_", " "))


def fmt(value: object, suffix: str = "", decimals: int = 2) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value}{suffix}"
    if isinstance(value, float):
        return f"{value:.{decimals}f}{suffix}"
    return pretty_label(value)


def markdown_table(frame: pd.DataFrame, columns: list[tuple[str, str, str, int]]) -> str:
    headers = [header for _, header, _, _ in columns]
    rows = []
    for _, row in frame.iterrows():
        rows.append(
            [
                fmt(row[source], suffix=suffix, decimals=decimals)
                for source, _, suffix, decimals in columns
            ]
        )

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def html_bar_chart(
    title: str,
    rows: list[dict[str, object]],
    value_key: str,
    label_key: str,
    suffix: str = "%",
    decimals: int = 1,
    color: str = "#2563eb",
) -> str:
    max_value = max(abs(float(row[value_key])) for row in rows) if rows else 1
    max_value = max(max_value, 1)
    items = []
    for row in rows:
        label = html.escape(pretty_label(row[label_key]))
        value = float(row[value_key])
        width = max(2, abs(value) / max_value * 100)
        bar_color = color if value >= 0 else "#dc2626"
        items.append(
            f"""
            <div class="bar-row">
              <div class="bar-label">{label}</div>
              <div class="bar-track">
                <div class="bar" style="width:{width:.2f}%; background:{bar_color};"></div>
              </div>
              <div class="bar-value">{value:.{decimals}f}{suffix}</div>
            </div>
            """
        )
    return f"""
    <section class="chart-section">
      <h2>{html.escape(title)}</h2>
      {''.join(items)}
    </section>
    """


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def generate_human_reports(
    observations: pd.DataFrame,
    touch_summary: pd.DataFrame,
    touch_period_summary: pd.DataFrame,
    combo_summary: pd.DataFrame,
    candidates: pd.DataFrame,
) -> None:
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    obs = observations.reset_index().rename(columns={"index": "date"})
    date_start = obs["date"].min()
    date_end = obs["date"].max()
    gap_up_rate = (obs["gap_dir"].eq("gap_up").mean() * 100).round(2)
    gap_down_rate = (obs["gap_dir"].eq("gap_down").mean() * 100).round(2)
    next_up_rate = (obs["next_intraday_dir"].eq("up_day").mean() * 100).round(2)
    avg_intraday = obs["next_intraday_pct"].mean()

    by_color = summarize(observations, ["three_color"]).sort_values("n", ascending=False)
    by_gap_bucket = summarize(observations, ["gap_bucket"]).sort_values(
        "avg_next_intraday_pct", ascending=False
    )
    combo_overall = summarize(observations, ["late_2bar_color_combo"]).sort_values(
        "n", ascending=False
    )

    top_candidates = candidates.head(12).copy()
    top_candidates["pattern"] = top_candidates.apply(
        lambda row: row["late_2bar_color_combo"]
        if pd.notna(row.get("late_2bar_color_combo"))
        else row["three_color"],
        axis=1,
    )

    main_report = f"""
# NIFTY 50 3 PM Alpha Study

Dataset: `NIFTY 50 15min (IS: 2015-01-09 to 2024-04-07)`

Period studied: `{date_start}` to `{date_end}`

Observations used: `{len(obs):,}` trading days with a 15:00 candle and a next trading day.

## Executive Read

The 3 PM candle is useful, but mainly as a reference level and context filter. The raw next-day gap-up tendency is strong at `{gap_up_rate}%`, while gap-downs are `{gap_down_rate}%`. This bias appears across bullish and bearish 3 PM candles, so candle color alone should not be treated as the full signal.

The stronger behavior is after the next open: when price gaps away from the prior 3 PM close, whether it revisits that level is a major decision point. The readable touch-level report has the key table.

## Baseline

- Overall next-day gap-up rate: `{gap_up_rate}%`
- Overall next-day open-to-close up rate: `{next_up_rate}%`
- Average next-day open-to-close return: `{avg_intraday:.3f}%`

## 3 PM Candle Color

{markdown_table(by_color, [
    ("three_color", "3 PM candle", "", 2),
    ("n", "N", "", 0),
    ("gap_up_rate", "Gap up", "%", 2),
    ("gap_down_rate", "Gap down", "%", 2),
    ("next_up_rate", "Next day up", "%", 2),
    ("avg_next_intraday_pct", "Avg O-C", "%", 3),
    ("touch_3pm_close_rate", "Touches 3 PM close", "%", 2),
])}

## Gap Size Buckets

{markdown_table(by_gap_bucket, [
    ("gap_bucket", "Gap bucket", "", 2),
    ("n", "N", "", 0),
    ("next_up_rate", "Next day up", "%", 2),
    ("avg_next_intraday_pct", "Avg O-C", "%", 3),
    ("touch_3pm_close_rate", "Touches 3 PM close", "%", 2),
    ("close_above_3pm_close_rate", "Close above 3 PM close", "%", 2),
])}

## Highest-Signal Candidate Buckets

These are sorted by absolute average next-day open-to-close return, with at least 100 observations.

{markdown_table(top_candidates, [
    ("pattern_family", "Family", "", 2),
    ("pattern", "Pattern", "", 2),
    ("gap_bucket", "Gap bucket", "", 2),
    ("n", "N", "", 0),
    ("next_up_rate", "Next day up", "%", 2),
    ("avg_next_intraday_pct", "Avg O-C", "%", 3),
    ("touch_3pm_close_rate", "Touches 3 PM close", "%", 2),
])}

## Files

- Raw daily observations: `../nifty_3pm_pattern_daily_observations.csv`
- Raw summary table: `../nifty_3pm_pattern_summary.csv`
- Charts: `charts/nifty_3pm_charts.html`
"""
    write_text(MAIN_REPORT_OUT, main_report)

    touch_display = touch_summary.sort_values(
        ["gap_dir", "three_color", "three_close_touched_next_day"]
    )
    touch_report = f"""
# Prior 3 PM Close Touch-Level Report

This report asks: after the next-day gap, does NIFTY trade back to the prior day's 3 PM candle close?

Interpretation:

- `touched = True` means any next-day 15-minute candle traded through the prior 3 PM close.
- `next day up` is next day close versus next day open.
- `after touch up` is close versus the first candle close that touched the level.

## Touch Versus No Touch

{markdown_table(touch_display, [
    ("three_color", "3 PM candle", "", 2),
    ("gap_dir", "Gap", "", 2),
    ("three_close_touched_next_day", "Touched", "", 2),
    ("n", "N", "", 0),
    ("next_up_rate", "Next day up", "%", 2),
    ("next_down_rate", "Next day down", "%", 2),
    ("avg_next_intraday_pct", "Avg O-C", "%", 3),
    ("close_above_3pm_close_rate", "Close above level", "%", 2),
])}

## First Touch Timing

{markdown_table(touch_period_summary.sort_values(["gap_dir", "three_close_first_touch_period"]), [
    ("gap_dir", "Gap", "", 2),
    ("three_close_first_touch_period", "First touch period", "", 2),
    ("n", "N", "", 0),
    ("next_up_rate", "Next day up", "%", 2),
    ("next_down_rate", "Next day down", "%", 2),
    ("avg_next_intraday_pct", "Avg O-C", "%", 3),
    ("after_touch_up_rate", "After touch up", "%", 2),
])}

## Practical Read

The prior 3 PM close behaves like a decision level. Gap-ups that do not revisit it tend to continue upward intraday. Gap-ups that revisit it tend to fade. Gap-downs show the mirror image: no revisit tends to stay weak, while a revisit often means recovery.
"""
    write_text(TOUCH_REPORT_OUT, touch_report)

    combo_report = f"""
# 3 PM Plus 3:15 PM Combo Report

This combines the final two 15-minute candles available in a normal session.

## Combo Summary

{markdown_table(combo_overall, [
    ("late_2bar_color_combo", "3 PM + 3:15 combo", "", 2),
    ("n", "N", "", 0),
    ("gap_up_rate", "Gap up", "%", 2),
    ("next_up_rate", "Next day up", "%", 2),
    ("avg_next_intraday_pct", "Avg O-C", "%", 3),
    ("touch_3pm_close_rate", "Touches 3 PM close", "%", 2),
    ("close_above_3pm_close_rate", "Close above 3 PM close", "%", 2),
])}

## Combo Plus Gap Direction

{markdown_table(combo_summary.sort_values(["late_2bar_color_combo", "gap_dir"]), [
    ("late_2bar_color_combo", "3 PM + 3:15 combo", "", 2),
    ("gap_dir", "Gap", "", 2),
    ("n", "N", "", 0),
    ("next_up_rate", "Next day up", "%", 2),
    ("avg_next_intraday_pct", "Avg O-C", "%", 3),
    ("touch_3pm_close_rate", "Touches 3 PM close", "%", 2),
])}
"""
    write_text(COMBO_REPORT_OUT, combo_report)

    index_report = f"""
# NIFTY 3 PM Study Summaries

Readable reports:

- [Main alpha report](nifty_3pm_alpha_report.md)
- [Prior 3 PM close touch-level report](nifty_3pm_touch_level_report.md)
- [3 PM plus 3:15 PM combo report](nifty_3pm_315_combo_report.md)
- [HTML charts](charts/nifty_3pm_charts.html)

Raw CSVs remain one folder up in `data_quality_reports/`.
"""
    write_text(INDEX_REPORT_OUT, index_report)

    touch_chart = observations[observations["gap_dir"].isin(["gap_up", "gap_down"])].groupby(
        ["gap_dir", "three_close_touched_next_day"], dropna=False
    ).agg(
        n=("gap_pct", "size"),
        next_up_rate=("next_intraday_dir", lambda s: (s == "up_day").mean() * 100),
        avg_next_intraday_pct=("next_intraday_pct", "mean"),
    ).reset_index()
    touch_chart["label"] = touch_chart.apply(
        lambda row: f"{pretty_label(row['gap_dir'])}, "
        f"{'touched' if row['three_close_touched_next_day'] else 'not touched'} "
        f"(n={int(row['n'])})",
        axis=1,
    )

    combo_chart = combo_overall.copy()
    combo_chart["label"] = combo_chart.apply(
        lambda row: f"{pretty_label(row['late_2bar_color_combo'])} (n={int(row['n'])})",
        axis=1,
    )

    bucket_chart = by_gap_bucket.copy()
    bucket_chart["label"] = bucket_chart.apply(
        lambda row: f"{pretty_label(row['gap_bucket'])} (n={int(row['n'])})",
        axis=1,
    )

    charts_html = f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>NIFTY 3 PM Pattern Charts</title>
  <style>
    body {{
      margin: 0;
      font-family: Arial, sans-serif;
      color: #172033;
      background: #f6f7f9;
    }}
    main {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
      letter-spacing: 0;
    }}
    h2 {{
      margin: 0 0 18px;
      font-size: 20px;
      letter-spacing: 0;
    }}
    p {{
      max-width: 820px;
      line-height: 1.55;
    }}
    .chart-section {{
      background: #ffffff;
      border: 1px solid #d9dee7;
      border-radius: 8px;
      padding: 20px;
      margin-top: 20px;
    }}
    .bar-row {{
      display: grid;
      grid-template-columns: minmax(220px, 1.1fr) minmax(180px, 2fr) 84px;
      gap: 12px;
      align-items: center;
      min-height: 34px;
      margin: 8px 0;
    }}
    .bar-label {{
      font-size: 14px;
    }}
    .bar-track {{
      height: 18px;
      background: #edf0f5;
      border-radius: 4px;
      overflow: hidden;
    }}
    .bar {{
      height: 100%;
      border-radius: 4px;
    }}
    .bar-value {{
      text-align: right;
      font-variant-numeric: tabular-nums;
      font-size: 14px;
    }}
    .note {{
      color: #536075;
      font-size: 14px;
    }}
    @media (max-width: 720px) {{
      .bar-row {{
        grid-template-columns: 1fr;
        gap: 6px;
      }}
      .bar-value {{
        text-align: left;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>NIFTY 3 PM Pattern Charts</h1>
    <p class="note">Generated from NIFTY 50 15min IS data (2015-01-09 to 2024-04-07). These charts are self-contained and do not need internet access.</p>
    {html_bar_chart("Touch of Prior 3 PM Close: Next-Day Up Rate", touch_chart.to_dict("records"), "next_up_rate", "label", "%", 1, "#0f766e")}
    {html_bar_chart("Touch of Prior 3 PM Close: Avg Next-Day Open-to-Close Return", touch_chart.to_dict("records"), "avg_next_intraday_pct", "label", "%", 3, "#2563eb")}
    {html_bar_chart("3 PM + 3:15 Combo: Avg Next-Day Open-to-Close Return", combo_chart.to_dict("records"), "avg_next_intraday_pct", "label", "%", 3, "#7c3aed")}
    {html_bar_chart("Gap Bucket: Avg Next-Day Open-to-Close Return", bucket_chart.to_dict("records"), "avg_next_intraday_pct", "label", "%", 3, "#9333ea")}
  </main>
</body>
</html>
"""
    write_text(CHART_OUT, charts_html)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    df = load_nifty_15min()  # IS only via data_loader

    daily = df.groupby("date").agg(
        day_open=("open", "first"),
        day_high=("high", "max"),
        day_low=("low", "min"),
        day_close=("close", "last"),
        first_time=("datetime", "first"),
        last_time=("datetime", "last"),
        bars=("datetime", "size"),
    )
    dates = list(daily.index)
    daily["next_date"] = dates[1:] + [pd.NA]

    three_pm = candle_features(df, "15:00:00", "three")
    three_fifteen = candle_features(df, "15:15:00", "three_fifteen")

    observations = daily.join(three_pm, how="inner").join(three_fifteen, how="left")
    observations = observations.join(
        daily.drop(columns=["next_date"]).shift(-1).add_prefix("next_")
    )
    observations = observations.dropna(subset=["next_day_open", "next_day_close"])

    observations["late_2bar_color_combo"] = (
        observations["three_color"] + "_" + observations["three_fifteen_color"].fillna("missing")
    )
    observations["late_2bar_pts"] = observations["three_fifteen_close"] - observations["three_open"]
    observations["late_2bar_pct"] = observations["late_2bar_pts"] / observations["three_open"] * 100
    observations["late_2bar_dir"] = observations["late_2bar_pts"].apply(
        lambda value: direction(value, "bullish_30min", "bearish_30min", "flat_30min")
    )
    observations["three_to_315_followthrough"] = (
        observations["three_color"] == observations["three_fifteen_color"]
    )

    observations["gap_pts"] = observations["next_day_open"] - observations["day_close"]
    observations["gap_pct"] = observations["gap_pts"] / observations["day_close"] * 100
    observations["gap_dir"] = observations["gap_pts"].apply(
        lambda value: direction(value, "gap_up", "gap_down", "flat")
    )
    observations["gap_bucket"] = observations["gap_pct"].apply(gap_bucket)

    observations["next_intraday_pts"] = (
        observations["next_day_close"] - observations["next_day_open"]
    )
    observations["next_intraday_pct"] = (
        observations["next_intraday_pts"] / observations["next_day_open"] * 100
    )
    observations["next_intraday_dir"] = observations["next_intraday_pts"].apply(
        lambda value: direction(value, "up_day", "down_day", "flat_day")
    )

    observations["next_total_pts"] = observations["next_day_close"] - observations["day_close"]
    observations["next_total_pct"] = (
        observations["next_total_pts"] / observations["day_close"] * 100
    )
    observations["next_total_dir"] = observations["next_total_pts"].apply(
        lambda value: direction(
            value, "close_above_prior_close", "close_below_prior_close", "flat_vs_prior"
        )
    )

    observations["next_open_vs_three_close_pct"] = (
        observations["next_day_open"] - observations["three_close"]
    ) / observations["three_close"] * 100
    observations["next_close_vs_three_close_pct"] = (
        observations["next_day_close"] - observations["three_close"]
    ) / observations["three_close"] * 100
    observations["next_close_vs_three_close_dir"] = observations[
        "next_close_vs_three_close_pct"
    ].apply(lambda value: direction(value, "above", "below", "at_level"))

    bars_by_date = {date: group.reset_index(drop=True) for date, group in df.groupby("date")}
    touch_rows: list[dict[str, object]] = []
    for date, row in observations.iterrows():
        next_bars = bars_by_date.get(row["next_date"])
        if next_bars is None:
            continue

        three_touch = touch_stats(next_bars, row["three_close"])
        prior_close_touch = touch_stats(next_bars, row["day_close"])

        touch_rows.append(
            {
                "date": date,
                "three_close_touched_next_day": three_touch["touched"],
                "three_close_first_touch_time": three_touch["first_touch_time"],
                "three_close_first_touch_bar_number": three_touch["first_touch_bar_number"],
                "three_close_touch_bar_close": three_touch["touch_bar_close"],
                "three_close_touch_bar_close_vs_level_pct": three_touch[
                    "touch_bar_close_vs_level_pct"
                ],
                "three_close_post_touch_high_vs_level_pct": three_touch[
                    "post_touch_high_vs_level_pct"
                ],
                "three_close_post_touch_low_vs_level_pct": three_touch[
                    "post_touch_low_vs_level_pct"
                ],
                "prior_close_touched_next_day": prior_close_touch["touched"],
                "prior_close_first_touch_time": prior_close_touch["first_touch_time"],
                "prior_close_first_touch_bar_number": prior_close_touch[
                    "first_touch_bar_number"
                ],
            }
        )

    touch_frame = pd.DataFrame(touch_rows).set_index("date")
    observations = observations.join(touch_frame, how="left")

    observations["three_close_touch_reaction"] = "not_touched"
    touched = observations["three_close_touched_next_day"].fillna(False)
    observations.loc[
        touched & observations["next_close_vs_three_close_pct"].gt(0),
        "three_close_touch_reaction",
    ] = "closed_above_level"
    observations.loc[
        touched & observations["next_close_vs_three_close_pct"].lt(0),
        "three_close_touch_reaction",
    ] = "closed_below_level"
    observations.loc[
        touched & observations["next_close_vs_three_close_pct"].eq(0),
        "three_close_touch_reaction",
    ] = "closed_at_level"
    observations["three_close_first_touch_period"] = observations[
        "three_close_first_touch_bar_number"
    ].apply(touch_period)
    observations["after_touch_close_vs_touch_bar_close_pct"] = (
        observations["next_day_close"] - observations["three_close_touch_bar_close"]
    ) / observations["three_close_touch_bar_close"] * 100
    observations["after_touch_close_dir"] = observations[
        "after_touch_close_vs_touch_bar_close_pct"
    ].apply(lambda value: direction(value, "up_after_touch", "down_after_touch", "flat_after_touch"))

    observations["gap_continues_intraday"] = (
        observations["gap_dir"].eq("gap_up") & observations["next_intraday_dir"].eq("up_day")
    ) | (
        observations["gap_dir"].eq("gap_down")
        & observations["next_intraday_dir"].eq("down_day")
    )
    observations["three_color_matches_gap"] = (
        observations["three_color"].eq("bullish") & observations["gap_dir"].eq("gap_up")
    ) | (
        observations["three_color"].eq("bearish") & observations["gap_dir"].eq("gap_down")
    )
    observations["three_color_matches_next_intraday"] = (
        observations["three_color"].eq("bullish")
        & observations["next_intraday_dir"].eq("up_day")
    ) | (
        observations["three_color"].eq("bearish")
        & observations["next_intraday_dir"].eq("down_day")
    )

    observations.reset_index().rename(columns={"index": "date"}).to_csv(
        DETAIL_OUT, index=False
    )

    summary_sections = [
        summarize(observations, ["three_color"]).assign(section="by_3pm_color"),
        summarize(observations, ["three_color", "gap_dir"]).assign(section="by_3pm_color_gap"),
        summarize(observations, ["gap_bucket"]).assign(section="by_gap_bucket"),
        summarize(observations, ["late_2bar_dir"]).assign(section="by_3pm_to_315_direction"),
        summarize(observations, ["late_2bar_color_combo"]).assign(section="by_3pm_315_combo"),
    ]
    pd.concat(summary_sections, ignore_index=True, sort=False).to_csv(SUMMARY_OUT, index=False)

    combo_summary = summarize(observations, ["late_2bar_color_combo", "gap_dir"])
    combo_summary.to_csv(COMBO_OUT, index=False)

    touch_summary = observations.groupby(
        ["three_color", "gap_dir", "three_close_touched_next_day"], dropna=False
    ).agg(
        n=("gap_pct", "size"),
        next_up_rate=("next_intraday_dir", lambda s: (s == "up_day").mean() * 100),
        next_down_rate=("next_intraday_dir", lambda s: (s == "down_day").mean() * 100),
        avg_next_intraday_pct=("next_intraday_pct", "mean"),
        close_above_3pm_close_rate=(
            "next_close_vs_three_close_dir",
            lambda s: (s == "above").mean() * 100,
        ),
        avg_close_vs_3pm_close_pct=("next_close_vs_three_close_pct", "mean"),
        after_touch_up_rate=("after_touch_close_dir", lambda s: (s == "up_after_touch").mean() * 100),
        avg_after_touch_close_pct=("after_touch_close_vs_touch_bar_close_pct", "mean"),
        avg_post_touch_high_vs_level_pct=("three_close_post_touch_high_vs_level_pct", "mean"),
        avg_post_touch_low_vs_level_pct=("three_close_post_touch_low_vs_level_pct", "mean"),
    )
    touch_summary_report = touch_summary.reset_index().round(4)
    touch_summary_report.to_csv(TOUCH_OUT, index=False)

    touch_period_summary = observations[
        observations["three_close_touched_next_day"].fillna(False)
    ].groupby(["gap_dir", "three_close_first_touch_period"], dropna=False).agg(
        n=("gap_pct", "size"),
        next_up_rate=("next_intraday_dir", lambda s: (s == "up_day").mean() * 100),
        next_down_rate=("next_intraday_dir", lambda s: (s == "down_day").mean() * 100),
        avg_next_intraday_pct=("next_intraday_pct", "mean"),
        close_above_3pm_close_rate=(
            "next_close_vs_three_close_dir",
            lambda s: (s == "above").mean() * 100,
        ),
        after_touch_up_rate=("after_touch_close_dir", lambda s: (s == "up_after_touch").mean() * 100),
        avg_after_touch_close_pct=("after_touch_close_vs_touch_bar_close_pct", "mean"),
    )
    touch_period_summary_report = touch_period_summary.reset_index().round(4)
    touch_period_summary_report.to_csv(TOUCH_PERIOD_OUT, index=False)

    candidates = pd.concat(
        [
            summarize(observations, ["three_color", "gap_bucket"]).assign(
                pattern_family="three_color_gap_bucket"
            ),
            summarize(observations, ["late_2bar_color_combo", "gap_bucket"]).assign(
                pattern_family="late_combo_gap_bucket"
            ),
        ],
        ignore_index=True,
        sort=False,
    )
    candidates = candidates[candidates["n"] >= 100].copy()
    candidates["edge_score_abs_avg_intraday"] = candidates["avg_next_intraday_pct"].abs()
    candidates = candidates.sort_values(
        ["edge_score_abs_avg_intraday", "n"], ascending=[False, False]
    )
    candidates.to_csv(CANDIDATE_OUT, index=False)

    generate_human_reports(
        observations,
        touch_summary_report,
        touch_period_summary_report,
        combo_summary,
        candidates,
    )

    print(f"Wrote {DETAIL_OUT}")
    print(f"Wrote {SUMMARY_OUT}")
    print(f"Wrote {TOUCH_OUT}")
    print(f"Wrote {TOUCH_PERIOD_OUT}")
    print(f"Wrote {COMBO_OUT}")
    print(f"Wrote {CANDIDATE_OUT}")
    print(f"Wrote {MAIN_REPORT_OUT}")
    print(f"Wrote {TOUCH_REPORT_OUT}")
    print(f"Wrote {COMBO_REPORT_OUT}")
    print(f"Wrote {CHART_OUT}")
    print(
        f"Observations: {len(observations):,} days with a 15:00 candle and a next trading day"
    )
    print(f"3:15 candles available in observations: {observations['three_fifteen_close'].notna().sum():,}")


if __name__ == "__main__":
    main()
