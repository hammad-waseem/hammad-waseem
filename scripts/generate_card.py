#!/usr/bin/env python3
"""Generates assets/card.svg: a terminal-styled GitHub profile card built
from this account's real contribution + language data. Run locally with
GH_TOKEN set (or via `gh auth token`), or in CI with GITHUB_TOKEN."""

import json
import math
import os
import sys
import urllib.request
from datetime import datetime

USERNAME = os.environ.get("CARD_USERNAME", "hammad-waseem")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "assets", "card.svg")

GRAPHQL_QUERY = """
query($login: String!) {
  user(login: $login) {
    name
    bio
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false) {
      totalCount
      nodes {
        languages(first: 6, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
      }
    }
  }
}
"""


def fetch():
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": GRAPHQL_QUERY, "variables": {"login": USERNAME}}).encode(),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)["data"]["user"]


def flatten_days(calendar):
    days = []
    for week in calendar["weeks"]:
        for day in week["contributionDays"]:
            days.append((day["date"], day["contributionCount"]))
    return days


def compute_streaks(days):
    counts = [c for _, c in days]
    longest = cur = 0
    for c in counts:
        if c > 0:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0

    idx = len(counts) - 1
    if idx >= 0 and counts[idx] == 0:
        idx -= 1
    current = 0
    while idx >= 0 and counts[idx] > 0:
        current += 1
        idx -= 1
    return current, longest


def monthly_buckets(days):
    buckets = {}
    order = []
    for date_str, count in days:
        key = date_str[:7]
        if key not in buckets:
            buckets[key] = 0
            order.append(key)
        buckets[key] += count
    order = order[-12:]
    return [(k, buckets[k]) for k in order]


def level_for(count, p1, p2, p3, p4):
    if count == 0:
        return 0
    if count <= p1:
        return 1
    if count <= p2:
        return 2
    if count <= p3:
        return 3
    return 4


def esc(s):
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


PALETTE = ["#58a6ff", "#7ee787", "#d2a8ff", "#ffa657", "#79c0ff", "#f0883e", "#56d4dd"]
LEVEL_COLORS = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]


def pack_words(words, box_w, box_h):
    """Greedy row-packing of (text, size) tuples into centered rows."""
    rows = []
    current_row = []
    current_w = 0
    max_row_w = box_w - 20
    for text, size in words:
        w = len(text) * size * 0.62 + 18
        if current_row and current_w + w > max_row_w:
            rows.append((current_row, current_w))
            current_row = []
            current_w = 0
        current_row.append((text, size, w))
        current_w += w
    if current_row:
        rows.append((current_row, current_w))

    total_h = sum(max(s for _, s, _ in row) * 1.35 for row, _ in rows) if rows else 0
    scale = min(1.0, (box_h - 20) / total_h) if total_h > box_h - 20 else 1.0

    svg_parts = []
    y = (box_h - total_h * scale) / 2 + 14
    for row, row_w in rows:
        row_h = max(s for _, s, _ in row) * scale
        x = (box_w - row_w * scale) / 2
        for i, (text, size, w) in enumerate(row):
            fsize = size * scale
            color = PALETTE[hash(text) % len(PALETTE)]
            svg_parts.append(
                f'<text x="{x + w*scale/2:.1f}" y="{y:.1f}" font-size="{fsize:.1f}" '
                f'fill="{color}" text-anchor="middle" font-weight="600" '
                f'font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace">{esc(text)}</text>'
            )
            x += w * scale
        y += row_h * 1.35
    return "".join(svg_parts)


def build_svg(data):
    name = data.get("name") or USERNAME
    bio = data.get("bio") or ""
    calendar = data["contributionsCollection"]["contributionCalendar"]
    total = calendar["totalContributions"]
    days = flatten_days(calendar)
    counts = [c for _, c in days if c > 0]
    current_streak, longest_streak = compute_streaks(days)
    days_active = len(counts)
    best_day = max((c for _, c in days), default=0)
    avg_active = (sum(counts) / len(counts)) if counts else 0

    nonzero_sorted = sorted(counts)
    def pct(p):
        if not nonzero_sorted:
            return 0
        idx = min(len(nonzero_sorted) - 1, int(len(nonzero_sorted) * p))
        return nonzero_sorted[idx]
    p1, p2, p3, p4 = pct(0.25), pct(0.5), pct(0.75), pct(1.0)

    # --- contribution grid ---
    weeks = calendar["weeks"]
    cell = 10
    gap = 3
    grid_x0 = 40
    grid_y0 = 90
    cells_svg = []
    month_labels = []
    last_month = None
    for wi, week in enumerate(weeks):
        for di, day in enumerate(week["contributionDays"]):
            lvl = level_for(day["contributionCount"], p1, p2, p3, p4)
            x = grid_x0 + wi * (cell + gap)
            y = grid_y0 + di * (cell + gap)
            cells_svg.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{LEVEL_COLORS[lvl]}"/>'
            )
            if di == 0:
                m = day["date"][5:7]
                if m != last_month:
                    month_labels.append(
                        f'<text x="{x}" y="{grid_y0 - 10}" font-size="10" fill="#8b949e" '
                        f'font-family="ui-monospace,monospace">{["","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][int(m)]}</text>'
                    )
                    last_month = m
    grid_w = grid_x0 + len(weeks) * (cell + gap)
    grid_h = grid_y0 + 7 * (cell + gap) + 30

    # --- monthly bar chart ---
    months = monthly_buckets(days)
    bar_box_w = 260
    bar_box_h = 82
    max_m = max((v for _, v in months), default=1) or 1
    bar_w = bar_box_w / max(len(months), 1) - 4
    bars_svg = []
    month_abbr = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for i, (label, v) in enumerate(months):
        h = (v / max_m) * (bar_box_h - 14)
        x = i * (bar_box_w / max(len(months), 1))
        y = bar_box_h - 14 - h
        bars_svg.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" rx="1.5" fill="#26a641"/>'
            f'<text x="{x + bar_w/2:.1f}" y="{bar_box_h-10:.1f}" font-size="8" fill="#6e7681" '
            f'text-anchor="middle" font-family="ui-monospace,monospace">{month_abbr[int(label[5:7])]}</text>'
        )

    # --- language word cloud ---
    lang_bytes = {}
    for repo in data["repositories"]["nodes"]:
        for edge in repo["languages"]["edges"]:
            lang_bytes[edge["node"]["name"]] = lang_bytes.get(edge["node"]["name"], 0) + edge["size"]
    langs = sorted(lang_bytes.items(), key=lambda x: -x[1])[:9]
    if langs:
        mx, mn = math.log(langs[0][1] + 1), math.log(langs[-1][1] + 1)
        span = max(mx - mn, 1e-6)
        words = [(name_, 14 + 28 * ((math.log(b + 1) - mn) / span)) for name_, b in langs]
    else:
        words = []
    cloud_box_w, cloud_box_h = 260, 150
    cloud_svg = pack_words(words, cloud_box_w, cloud_box_h)

    width = max(grid_w + 40, 900)
    height = grid_h + 420

    def prompt_line(y, cmd):
        return (
            f'<text x="24" y="{y}" font-size="13" xml:space="preserve" font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace">'
            f'<tspan fill="#7ee787" font-weight="700">{esc(USERNAME)}@github</tspan>'
            f'<tspan fill="#8b949e"> ~ </tspan>'
            f'<tspan fill="#58a6ff">$</tspan>'
            f'<tspan fill="#e6edf3"> {esc(cmd)}</tspan></text>'
        )

    whoami_y = grid_h + 50
    col2_x = 320

    svg = f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <style>text{{dominant-baseline:hanging;}}</style>
  </defs>
  <rect x="0" y="0" width="{width}" height="{height}" rx="14" fill="#0d1117" stroke="#30363d"/>
  <circle cx="24" cy="20" r="6" fill="#ff5f56"/>
  <circle cx="44" cy="20" r="6" fill="#ffbd2e"/>
  <circle cx="64" cy="20" r="6" fill="#27c93f"/>
  <text x="{width/2}" y="16" font-size="12" fill="#8b949e" text-anchor="middle" font-family="ui-monospace,monospace">{esc(USERNAME)} — zsh</text>
  <line x1="0" y1="36" x2="{width}" y2="36" stroke="#21262d"/>

  {prompt_line(58, "./contributions.sh")}
  {"".join(month_labels)}
  {"".join(cells_svg)}
  <text x="{grid_x0}" y="{grid_h - 6}" font-size="12" fill="#8b949e" font-family="ui-monospace,monospace">{total:,} contributions in the last year</text>

  {prompt_line(whoami_y, "whoami")}

  <rect x="24" y="{whoami_y+14}" width="{cloud_box_w}" height="{cloud_box_h}" rx="8" fill="#161b22" stroke="#21262d"/>
  <g transform="translate(24,{whoami_y+14})">{cloud_svg}</g>

  <g transform="translate({col2_x},{whoami_y+14})">
    <rect x="0" y="0" width="100" height="56" rx="6" fill="#161b22" stroke="#21262d"/>
    <text x="10" y="12" font-size="20" fill="#39d353" font-weight="700" font-family="ui-monospace,monospace">{current_streak}</text>
    <text x="10" y="36" font-size="10" fill="#8b949e" font-family="ui-monospace,monospace">current streak</text>

    <rect x="108" y="0" width="100" height="56" rx="6" fill="#161b22" stroke="#21262d"/>
    <text x="118" y="12" font-size="20" fill="#e6edf3" font-weight="700" font-family="ui-monospace,monospace">{longest_streak}</text>
    <text x="118" y="36" font-size="10" fill="#8b949e" font-family="ui-monospace,monospace">longest streak</text>

    <rect x="0" y="64" width="100" height="56" rx="6" fill="#161b22" stroke="#21262d"/>
    <text x="10" y="76" font-size="20" fill="#e6edf3" font-weight="700" font-family="ui-monospace,monospace">{total}</text>
    <text x="10" y="100" font-size="10" fill="#8b949e" font-family="ui-monospace,monospace">total this year</text>

    <rect x="108" y="64" width="100" height="56" rx="6" fill="#161b22" stroke="#21262d"/>
    <text x="118" y="76" font-size="20" fill="#e6edf3" font-weight="700" font-family="ui-monospace,monospace">{days_active}</text>
    <text x="118" y="100" font-size="10" fill="#8b949e" font-family="ui-monospace,monospace">days active</text>

    <text x="0" y="134" font-size="10" fill="#8b949e" font-family="ui-monospace,monospace">best day: {best_day}  ·  avg/active day: {avg_active:.1f}</text>
    <g transform="translate(0,150)">{"".join(bars_svg)}</g>
  </g>

  <text x="24" y="{height-20}" font-size="11" fill="#30363d" font-family="ui-monospace,monospace">{esc(name)}{(" · " + esc(bio)) if bio else ""}</text>
</svg>'''
    return svg


def main():
    if not TOKEN:
        print("Set GH_TOKEN or GITHUB_TOKEN", file=sys.stderr)
        sys.exit(1)
    data = fetch()
    svg = build_svg(data)
    out = os.path.normpath(OUT_PATH)
    with open(out, "w") as f:
        f.write(svg)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
