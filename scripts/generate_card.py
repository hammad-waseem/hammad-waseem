#!/usr/bin/env python3
"""Generates assets/card.svg: a terminal-styled GitHub profile card built
from this account's real contribution + language data, plus an ASCII-art
rendering of the account's real avatar. Run locally with GH_TOKEN set (or
via `gh auth token`), or in CI with GITHUB_TOKEN."""

import io
import json
import os
import sys
import urllib.request
from datetime import datetime

from PIL import Image, ImageFilter, ImageOps

USERNAME = os.environ.get("CARD_USERNAME", "hammad-waseem")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "assets", "card.svg")

MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAY_LABELS = {1: "Mon", 3: "Wed", 5: "Fri"}

GRAPHQL_QUERY = """
query($login: String!) {
  user(login: $login) {
    name
    bio
    avatarUrl(size: 200)
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch():
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": GRAPHQL_QUERY, "variables": {"login": USERNAME}}).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)["data"]["user"]


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def flatten_days(calendar):
    days = []
    for week in calendar["weeks"]:
        for day in week["contributionDays"]:
            days.append((day["date"], day["contributionCount"]))
    return days


def runs(counts):
    """Yield (start_idx, end_idx, length) for every run of consecutive >0 values."""
    start = None
    for i, c in enumerate(counts):
        if c > 0 and start is None:
            start = i
        elif c == 0 and start is not None:
            yield (start, i - 1, i - start)
            start = None
    if start is not None:
        yield (start, len(counts) - 1, len(counts) - start)


def compute_streaks(days):
    counts = [c for _, c in days]
    all_runs = list(runs(counts))
    longest = max(all_runs, key=lambda r: r[2], default=(0, 0, 0))

    idx = len(counts) - 1
    if idx >= 0 and counts[idx] == 0:
        idx -= 1
    current_len = 0
    end = idx
    while idx >= 0 and counts[idx] > 0:
        current_len += 1
        idx -= 1
    current = (idx + 1, end, current_len)
    return current, longest


def fmt_date(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{MONTH_ABBR[d.month]} {d.day}"


def monthly_buckets(days):
    buckets, order = {}, []
    for date_str, count in days:
        key = date_str[:7]
        if key not in buckets:
            buckets[key] = 0
            order.append(key)
        buckets[key] += count
    order = order[-12:]
    return [(k, buckets[k]) for k in order]


def level_for(count, p1, p2, p3):
    if count == 0:
        return 0
    if count <= p1:
        return 1
    if count <= p2:
        return 2
    if count <= p3:
        return 3
    return 4


LEVEL_COLORS = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]
# fine-grained light->dark ramp for smoother tonal gradation
ASCII_RAMP = " .'`^\",:;Il!i><~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"


def fetch_avatar(url):
    req = urllib.request.Request(url, headers={"User-Agent": "profile-card-generator"})
    with urllib.request.urlopen(req) as resp:
        img = Image.open(io.BytesIO(resp.read())).convert("L")
    w, h = img.size
    # focus on head/shoulders: drop the lower strip of a square avatar
    img = img.crop((0, 0, w, int(h * 0.82)))
    img = ImageOps.autocontrast(img, cutoff=1)
    img = img.filter(ImageFilter.GaussianBlur(radius=0.6))  # smooth sensor/jpeg noise before sampling
    return img


def ascii_art_svg(img, avail_w, avail_h, row_fade, cw=3.2, ch=5.8):
    rows = max(1, int(avail_h // ch))
    cols = max(1, min(int(avail_w // cw), round(rows * ch / cw)))
    rows = max(1, round(cols * cw / ch))
    small = img.resize((cols, rows), Image.LANCZOS)
    pixels = list(small.getdata())

    lo, hi = min(pixels), max(pixels)
    span = max(hi - lo, 1)
    BLANK_CUTOFF = 0.86  # anything this bright renders as true blank, no faint dot

    def normalize(lum):
        v = (lum - lo) / span
        v = v ** 0.8
        return max(0.0, min(1.0, v))

    art_w, art_h = cols * cw, rows * ch
    ox = (avail_w - art_w) / 2
    oy = (avail_h - art_h) / 2

    parts = []
    for r in range(rows):
        row_chars = []
        for c in range(cols):
            v = normalize(pixels[r * cols + c])
            if v >= BLANK_CUTOFF:
                row_chars.append(" ")
                continue
            density = min(len(ASCII_RAMP) - 1, int((1 - v) * (len(ASCII_RAMP) - 1)))
            row_chars.append(ASCII_RAMP[density])
        row_text = esc("".join(row_chars)).replace(" ", "&#160;")
        y = oy + r * ch + ch * 0.85
        delay = row_fade[0] + r * row_fade[1]
        parts.append(
            f'<text class="rv" style="animation-delay:{delay:.3f}s" x="{ox:.1f}" y="{y:.1f}" '
            f'font-size="{ch*1.15:.1f}" xml:space="preserve" '
            f'fill="#c9d1d9" font-family="{MONO}" letter-spacing="-0.2">{row_text}</text>'
        )
    return "".join(parts), rows


_clip_counter = [0]


def typed_prompt(width, y, cmd, delay, size=13):
    """A centered terminal prompt that types itself in via an animated clip-path."""
    _clip_counter[0] += 1
    clip_id = f"tc{_clip_counter[0]}"
    text_full = f"{USERNAME}@github ~ $ {cmd}"
    char_w = size * 0.62
    text_w = len(text_full) * char_w
    left = width / 2 - text_w / 2 - 6
    top = y - 2
    h = size * 1.6
    text_svg = (
        f'<text x="{width/2:.1f}" y="{y}" font-size="{size}" text-anchor="middle" xml:space="preserve" '
        f'font-family="{MONO}">'
        f'<tspan fill="#7ee787" font-weight="700">{esc(USERNAME)}@github</tspan>'
        f'<tspan fill="#8b949e"> ~ </tspan>'
        f'<tspan fill="#58a6ff">$</tspan>'
        f'<tspan fill="#e6edf3"> {esc(cmd)}</tspan></text>'
    )
    clip = (
        f'<clipPath id="{clip_id}"><rect class="rv-clip" style="--w:{text_w:.0f}px;animation-delay:{delay:.2f}s" '
        f'x="{left:.1f}" y="{top:.1f}" width="{text_w:.0f}" height="{h:.1f}"/></clipPath>'
    )
    return f'{clip}<g clip-path="url(#{clip_id})">{text_svg}</g>', text_w, left


def mini_chrome(x, y, w, title):
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="22" rx="6" fill="#0d1117"/>'
        f'<circle cx="{x+14}" cy="{y+11}" r="3.5" fill="#ff5f56"/>'
        f'<circle cx="{x+26}" cy="{y+11}" r="3.5" fill="#ffbd2e"/>'
        f'<circle cx="{x+38}" cy="{y+11}" r="3.5" fill="#27c93f"/>'
        f'<text x="{x+w/2:.1f}" y="{y+11}" font-size="10" fill="#8b949e" text-anchor="middle" '
        f'dominant-baseline="middle" font-family="{MONO}">{esc(title)}</text>'
    )


def stat_cell(x, y, w, label, value, sub, delay, value_color="#e6edf3"):
    return (
        f'<g class="rv-up" style="animation-delay:{delay:.2f}s">'
        f'<text x="{x}" y="{y}" font-size="10.5" fill="#6e7681" font-family="{MONO}">$ {esc(label)}</text>'
        f'<text x="{x}" y="{y+22}" font-size="20" font-weight="700" fill="{value_color}" font-family="{MONO}">{esc(value)}</text>'
        f'<text x="{x}" y="{y+38}" font-size="9.5" fill="#6e7681" font-family="{MONO}">{esc(sub)}</text>'
        f'</g>'
    )


def build_svg(data):
    name = data.get("name") or USERNAME
    bio = data.get("bio") or ""
    avatar_url = data.get("avatarUrl")
    calendar = data["contributionsCollection"]["contributionCalendar"]
    total = calendar["totalContributions"]
    days = flatten_days(calendar)
    counts = [c for _, c in days]
    nz = [c for c in counts if c > 0]
    days_active = len(nz)
    pct_active = round(days_active / len(days) * 100) if days else 0
    avg_active = (sum(nz) / len(nz)) if nz else 0

    current, longest = compute_streaks(days)
    best_idx = max(range(len(days)), key=lambda i: days[i][1]) if days else 0
    best_day_count = days[best_idx][1] if days else 0

    s = sorted(nz)
    def pct(p):
        if not s:
            return 0
        return s[min(len(s) - 1, int(len(s) * p))]
    p1, p2, p3 = pct(0.25), pct(0.5), pct(0.75)

    width = 900

    # ---------- section 1: contributions.sh ----------
    weeks = calendar["weeks"]
    cell, gap = 10, 3
    grid_x0 = 56
    sec1_top = 36
    prompt1_y = sec1_top + 26
    month_label_y = prompt1_y + 24
    grid_y0 = month_label_y + 10

    GRID_BASE_DELAY = 0.55
    GRID_COL_STEP = 0.03

    cells_svg, month_labels, day_labels = [], [], []
    last_month = None
    for wi, week in enumerate(weeks):
        col_delay = GRID_BASE_DELAY + wi * GRID_COL_STEP
        for di, day in enumerate(week["contributionDays"]):
            lvl = level_for(day["contributionCount"], p1, p2, p3)
            x = grid_x0 + wi * (cell + gap)
            y = grid_y0 + di * (cell + gap)
            cells_svg.append(
                f'<rect class="rv-cell" style="--c:{LEVEL_COLORS[lvl]};animation-delay:{col_delay:.3f}s" x="{x}" y="{y}" '
                f'width="{cell}" height="{cell}" rx="2" fill="{LEVEL_COLORS[lvl]}"/>'
            )
            if di == 0:
                m = day["date"][5:7]
                if m != last_month:
                    month_labels.append(
                        f'<text x="{x}" y="{month_label_y}" font-size="10" fill="#8b949e" font-family="{MONO}">{MONTH_ABBR[int(m)]}</text>'
                    )
                    last_month = m
    for di, lbl in DAY_LABELS.items():
        y = grid_y0 + di * (cell + gap) + cell - 1
        day_labels.append(f'<text x="{grid_x0-8}" y="{y}" font-size="9" fill="#6e7681" text-anchor="end" font-family="{MONO}">{lbl}</text>')

    grid_bottom = grid_y0 + 7 * (cell + gap)
    total_line_y = grid_bottom + 22
    sec1_bottom = total_line_y + 20

    t_total_line = GRID_BASE_DELAY + len(weeks) * GRID_COL_STEP + 0.25
    t_prompt2 = t_total_line + 0.35
    t_whoami_base = t_prompt2 + 0.65

    # ---------- section 2: whoami (two mini terminals) ----------
    prompt2_y = sec1_bottom + 30
    sub_top = prompt2_y + 22
    col_gap = 16
    margin = 24
    col_w = (width - margin * 2 - col_gap) / 2
    left_x = margin
    right_x = margin + col_w + col_gap

    # right (stats) content sizing drives both heights
    row_h, row_gap = 48, 14
    stats_pad = 18
    stats_rows_h = row_h * 3 + row_gap * 2
    bar_box_h = 72
    bar_gap = 18
    right_content_h = 22 + stats_pad + stats_rows_h + bar_gap + bar_box_h + 14
    sub_h = right_content_h

    # left (ascii art) chrome + footer caption
    footer_h = 34
    art_avail_w = col_w - 24
    art_avail_h = sub_h - 22 - footer_h - 10

    ART_ROW_STEP = 0.014
    try:
        img = fetch_avatar(avatar_url) if avatar_url else None
        art_svg, art_rows = ascii_art_svg(img, art_avail_w, art_avail_h, (t_whoami_base, ART_ROW_STEP)) if img else ("", 0)
    except Exception as e:
        print(f"avatar fetch/render failed: {e}", file=sys.stderr)
        art_svg, art_rows = "", 0

    sub_bottom = sub_top + sub_h
    sec2_bottom = sub_bottom + 16

    # ---------- section 3: links.sh ----------
    prompt3_y = sec2_bottom + 30
    tagline_y = prompt3_y + 22
    note_y = tagline_y + 24
    height = note_y + 30

    t_stats_base = t_whoami_base
    STATS_STEP = 0.12
    t_art_end = t_whoami_base + art_rows * ART_ROW_STEP + 0.3
    t_stats_end = t_stats_base + 6 * STATS_STEP + 0.45
    t_bars_base = max(t_art_end, t_stats_end)
    BAR_STEP = 0.045

    months = monthly_buckets(days)
    max_m = max((v for _, v in months), default=1) or 1
    peak_i = max(range(len(months)), key=lambda i: months[i][1]) if months else 0
    bar_w = (col_w - 2 * stats_pad) / max(len(months), 1) - 4
    bars_svg = []
    chart_w = col_w - 2 * stats_pad
    for i, (label, v) in enumerate(months):
        h = (v / max_m) * (bar_box_h - 24)
        x = i * (chart_w / max(len(months), 1))
        y = bar_box_h - 16 - h
        bar_color = "#39d353" if i == peak_i else "#26a641"
        bar_delay = t_bars_base + i * BAR_STEP
        bars_svg.append(
            f'<rect class="rv-bar" style="animation-delay:{bar_delay:.2f}s" x="{x:.1f}" y="{y:.1f}" '
            f'width="{bar_w:.1f}" height="{max(h,1.5):.1f}" rx="1.5" fill="{bar_color}"/>'
        )
        bars_svg.append(
            f'<text class="rv" style="animation-delay:{bar_delay:.2f}s" x="{x+bar_w/2:.1f}" y="{bar_box_h:.1f}" '
            f'font-size="8" fill="#6e7681" text-anchor="middle" font-family="{MONO}">{MONTH_ABBR[int(label[5:7])][0]}</text>'
        )
        if i == peak_i and v > 0:
            bars_svg.append(
                f'<text class="rv" style="animation-delay:{bar_delay+0.15:.2f}s" x="{x+bar_w/2:.1f}" y="{y-5:.1f}" '
                f'font-size="9" fill="#39d353" text-anchor="middle" font-weight="700" font-family="{MONO}">{v:,}</text>'
            )

    cur_start, cur_end, cur_len = current
    lon_start, lon_end, lon_len = longest
    cur_range = f"{fmt_date(days[cur_start][0])} – {fmt_date(days[cur_end][0])}" if cur_len else "—"
    lon_range = f"{fmt_date(days[lon_start][0])} – {fmt_date(days[lon_end][0])}" if lon_len else "—"

    r1y = sub_top + 22 + stats_pad + 14
    r2y = r1y + row_h + row_gap
    r3y = r2y + row_h + row_gap
    chart_y = r3y + row_h + bar_gap

    stats_svg = "".join([
        stat_cell(right_x + stats_pad, r1y, col_w / 2, "current_streak", f"{cur_len} days", cur_range, t_stats_base + 0 * STATS_STEP, "#39d353"),
        stat_cell(right_x + col_w / 2, r1y, col_w / 2, "longest_streak", f"{lon_len} days", lon_range, t_stats_base + 1 * STATS_STEP),
        stat_cell(right_x + stats_pad, r2y, col_w / 2, "contributions", f"{total:,}", "in the last year", t_stats_base + 2 * STATS_STEP),
        stat_cell(right_x + col_w / 2, r2y, col_w / 2, "active_days", f"{days_active} / {len(days)}", f"{pct_active}% of the year", t_stats_base + 3 * STATS_STEP),
        stat_cell(right_x + stats_pad, r3y, col_w / 2, "best_day", f"{best_day_count}", fmt_date(days[best_idx][0]) if days else "", t_stats_base + 4 * STATS_STEP),
        stat_cell(right_x + col_w / 2, r3y, col_w / 2, "avg / active_day", f"{avg_active:.1f}", "contributions", t_stats_base + 5 * STATS_STEP),
        f'<text class="rv" style="animation-delay:{t_bars_base:.2f}s" x="{right_x+stats_pad}" y="{chart_y-8}" font-size="10.5" fill="#6e7681" font-family="{MONO}">$ contributions / month</text>',
        f'<g transform="translate({right_x+stats_pad},{chart_y})">{"".join(bars_svg)}</g>',
    ])

    t_prompt3 = t_bars_base + len(months) * BAR_STEP + 0.35
    typed1, _, _ = typed_prompt(width, prompt1_y, "./contributions.sh", 0.0)
    typed2, _, _ = typed_prompt(width, prompt2_y, "whoami", t_prompt2)
    typed3, text3_w, text3_left = typed_prompt(width, prompt3_y, "./links.sh", t_prompt3)
    t_tagline = t_prompt3 + 0.65
    t_note = t_tagline + 0.3
    cursor_x = text3_left + text3_w + 10

    style = '''<style>
    text{dominant-baseline:hanging;}
    .rv{animation:rv .45s ease-out backwards;}
    .rv-up{animation:rvup .45s ease-out backwards;}
    .rv-cell{animation:rvcell .55s cubic-bezier(.2,.75,.3,1) backwards;transform-box:fill-box;transform-origin:center;}
    .rv-bar{animation:rvbar .45s cubic-bezier(.22,.9,.32,1) backwards;transform-box:fill-box;transform-origin:bottom;}
    .rv-clip{animation:rvclip .6s steps(18,end) both;}
    .cursor{animation:blink 1s steps(1,end) infinite;}
    @keyframes rv{from{opacity:0}to{opacity:1}}
    @keyframes rvup{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
    @keyframes rvcell{0%{fill:#0d1117;opacity:.5;transform:scale(.55)}40%{fill:#7cffb3;opacity:1;transform:scale(1.1)}100%{fill:var(--c);opacity:1;transform:scale(1)}}
    @keyframes rvbar{from{transform:scaleY(0)}to{transform:scaleY(1)}}
    @keyframes rvclip{from{width:0}to{width:var(--w)}}
    @keyframes blink{0%,49%{opacity:1}50%,100%{opacity:0}}
    </style>'''

    svg = f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
  <defs>{style}</defs>
  <rect x="0" y="0" width="{width}" height="{height}" rx="14" fill="#0d1117" stroke="#30363d"/>
  <circle cx="24" cy="18" r="6" fill="#ff5f56"/>
  <circle cx="44" cy="18" r="6" fill="#ffbd2e"/>
  <circle cx="64" cy="18" r="6" fill="#27c93f"/>
  <text x="{width/2}" y="13" font-size="12" fill="#8b949e" text-anchor="middle" font-family="{MONO}">{esc(USERNAME)} — zsh</text>
  <line x1="0" y1="34" x2="{width}" y2="34" stroke="#21262d"/>

  {typed1}
  {"".join(month_labels)}
  {"".join(day_labels)}
  {"".join(cells_svg)}
  <text class="rv" style="animation-delay:{t_total_line:.2f}s" x="{grid_x0}" y="{total_line_y}" font-size="12" fill="#8b949e" font-family="{MONO}">{total:,} contributions in the last year</text>

  {typed2}

  <rect x="{left_x}" y="{sub_top}" width="{col_w}" height="{sub_h}" rx="8" fill="#161b22" stroke="#21262d"/>
  {mini_chrome(left_x, sub_top, col_w, f"{USERNAME} — art.sh")}
  <g transform="translate({left_x+12},{sub_top+22+6})">{art_svg}</g>
  <line x1="{left_x+10}" y1="{sub_bottom-footer_h}" x2="{left_x+col_w-10}" y2="{sub_bottom-footer_h}" stroke="#21262d"/>
  <text x="{left_x+14}" y="{sub_bottom-footer_h+9}" font-size="9.5" xml:space="preserve" font-family="{MONO}">
    <tspan fill="#7ee787">{esc(USERNAME)}@github</tspan><tspan fill="#8b949e"> ~ </tspan><tspan fill="#58a6ff">$</tspan><tspan fill="#e6edf3"> whoami</tspan>
  </text>
  <text x="{left_x+14}" y="{sub_bottom-14}" font-size="11" fill="#e6edf3" font-weight="700" font-family="{MONO}">{esc(name)} ▸</text>

  <rect x="{right_x}" y="{sub_top}" width="{col_w}" height="{sub_h}" rx="8" fill="#161b22" stroke="#21262d"/>
  {mini_chrome(right_x, sub_top, col_w, f"{USERNAME} — stats.sh")}
  {stats_svg}

  {typed3}
  <rect class="cursor" style="animation-delay:{t_note:.2f}s" x="{cursor_x:.1f}" y="{prompt3_y-1}" width="7" height="14" fill="#e6edf3"/>
  <text class="rv" style="animation-delay:{t_tagline:.2f}s" x="{width/2}" y="{tagline_y}" font-size="11.5" fill="#8b949e" text-anchor="middle" font-family="{MONO}">{esc(bio) if bio else "GitHub profile"}</text>
  <text class="rv" style="animation-delay:{t_note:.2f}s" x="{width/2}" y="{note_y}" font-size="10" fill="#484f58" text-anchor="middle" font-family="{MONO}">↓ links below ↓</text>
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
