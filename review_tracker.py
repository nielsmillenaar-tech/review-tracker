#!/usr/bin/env python3
"""
Airbnb Review Tracker — Hospitable Public API v2

Pulls guest reviews for every property on your Hospitable account and builds a
per-property HTML dashboard that headlines the LAST 3 reviews for each listing
(the most recent reviews carry the most weight on Airbnb's rating + ranking).

A listing is flagged as "needs attention" when either:
  1. a recent review is <= LOW_RATING_THRESHOLD and you haven't responded, OR
  2. the recent average has dropped vs the all-time average (even if responded).

Every run also appends a dated snapshot to history.csv so you build up
rating-over-time data (the API itself keeps no history).

Usage
-----
  # Preview with realistic sample data (no token needed):
  python review_tracker.py --sample

  # Use your real Hospitable data:
  export HOSPITABLE_PAT="your_token_here"
  python review_tracker.py

Get a token: my.hospitable.com -> Apps -> API access -> Access tokens -> + Add new
(read permissions are enough). Requires a paid Hospitable plan.
"""

import os
import sys
import csv
import json
import argparse
from datetime import datetime, timedelta

API_BASE = "https://public.api.hospitable.com/v2"

# A review at/below this overall rating gets flagged for attention.
LOW_RATING_THRESHOLD = 4.0
# Recent avg this far (or more) below all-time avg counts as a "drop".
DROP_THRESHOLD = 0.1
# How many recent reviews to headline per property.
RECENT_COUNT = 3
# Airbnb sub-rating categories (label -> short display name).
CATEGORY_LABELS = {
    "cleanliness": "Clean",
    "accuracy": "Accuracy",
    "communication": "Comms",
    "checkin": "Check-in",
    "location": "Location",
    "value": "Value",
}


# --------------------------------------------------------------------------- #
# Data fetching
# --------------------------------------------------------------------------- #
def _shape(obj, depth=0):
    """Return a redacted structural view of a JSON object: keys and value
    TYPES only — never the actual values. Safe to share for parser debugging."""
    if depth > 4:
        return "..."
    if isinstance(obj, dict):
        return {k: _shape(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_shape(obj[0], depth + 1)] if obj else []
    return type(obj).__name__  # e.g. "str", "int", "float", "bool", "NoneType"


def _print_inspect(reservations):
    """Print the redacted shape of the first reservation that carries a review."""
    print("\n===== INSPECT: redacted data structure (no names, text, or ratings) =====")
    sample = next((r for r in reservations if r.get("review")), None)
    if not sample:
        print("No reservations with a review were returned in this window.")
        print("=========================================================================\n")
        return
    print("Reservation keys -> types:")
    print(json.dumps(_shape(sample), indent=2))
    print("\nReview object keys -> types:")
    print(json.dumps(_shape(sample.get("review")), indent=2))
    print("=========================================================================\n")


def _get(session, url, params=None):
    r = session.get(url, params=params, timeout=30)
    if r.status_code == 401:
        sys.exit("Auth failed (401). Check your HOSPITABLE_PAT token is valid and not expired.")
    if r.status_code == 403:
        sys.exit("Forbidden (403). Your token needs read access / your plan must support the API.")
    r.raise_for_status()
    return r.json()


def fetch_live():
    """Fetch properties and their reviews from the Hospitable API."""
    import requests

    token = os.getenv("HOSPITABLE_PAT") or os.getenv("HOSPITABLE_TOKEN")
    if not token:
        sys.exit("No token found. Set HOSPITABLE_PAT, or run with --sample to preview.")

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })

    # 1) Properties
    properties = {}
    page = 1
    while True:
        data = _get(session, f"{API_BASE}/properties", params={"page": page, "per_page": 50})
        for p in data.get("data", []):
            properties[p["id"]] = {"id": p["id"], "name": p.get("name", "Unnamed listing"), "reviews": []}
        meta = data.get("meta", {})
        if not meta or page >= meta.get("last_page", page):
            break
        page += 1

    # 2) Reservations with reviews attached (last ~2 years)
    start = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    end = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    inspect = os.getenv("HOSPITABLE_INSPECT") == "1"
    raw_with_review = []
    prop_ids = list(properties.keys())
    page = 1
    while True:
        params = {
            "start_date": start, "end_date": end,
            "include": "review,guest,properties", "page": page, "per_page": 50,
        }
        for i, pid in enumerate(prop_ids):
            params[f"properties[{i}]"] = pid
        data = _get(session, f"{API_BASE}/reservations", params=params)
        for res in data.get("data", []):
            if inspect and res.get("review"):
                raw_with_review.append(res)
            review = res.get("review")
            if not review:
                continue
            pid = res.get("property_id") or res.get("property_uuid")
            if pid in properties:
                properties[pid]["reviews"].append(_normalize_review(review, res))
        meta = data.get("meta", {})
        if not meta or page >= meta.get("last_page", page):
            break
        page += 1

    if inspect:
        _print_inspect(raw_with_review)

    return list(properties.values())


def _mask_guest(guest):
    """Privacy: show first name + last initial only (e.g. 'Tom R.')."""
    first = guest.get("first_name") or ""
    last = guest.get("last_name") or ""
    if not (first or last) and guest.get("name"):
        parts = guest["name"].split()
        first = parts[0]
        last = parts[1] if len(parts) > 1 else ""
    if not first:
        return "Guest"
    return f"{first} {last[0]}." if last else first


def _normalize_review(review, reservation):
    """Map an API review object into the simple shape the dashboard expects."""
    guest = (reservation or {}).get("guest", {}) or {}
    # Hospitable exposes category sub-ratings under a few possible shapes;
    # pull whatever is present and normalize to our label keys.
    raw_cats = review.get("categories") or review.get("category_ratings") or {}
    cats = {}
    if isinstance(raw_cats, dict):
        for key, val in raw_cats.items():
            k = key.lower().replace("_", "").replace("-", "")
            for label in CATEGORY_LABELS:
                if label.replace("_", "") in k:
                    if isinstance(val, dict):
                        val = val.get("rating") or val.get("value")
                    if val is not None:
                        cats[label] = round(float(val), 1)
    elif isinstance(raw_cats, list):
        for item in raw_cats:
            name = (item.get("category") or item.get("name") or "").lower()
            val = item.get("rating") or item.get("value")
            for label in CATEGORY_LABELS:
                if label in name and val is not None:
                    cats[label] = round(float(val), 1)
    return {
        "date": review.get("created_at") or review.get("submitted_at") or "",
        "guest": _mask_guest(guest),
        "rating": review.get("rating") or review.get("overall_rating"),
        "text": review.get("public_review") or review.get("comments") or review.get("text") or "",
        "channel": (reservation or {}).get("platform") or review.get("channel") or "airbnb",
        "responded": bool(review.get("response") or review.get("host_reply")),
        "categories": cats,
    }


# --------------------------------------------------------------------------- #
# Sample data (for the prototype)
# --------------------------------------------------------------------------- #
def fetch_sample():
    def d(days_ago):
        return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")

    def c(cl, ac, co, ci, lo, va):
        return {"cleanliness": cl, "accuracy": ac, "communication": co,
                "checkin": ci, "location": lo, "value": va}

    return [
        {
            "id": "p1", "name": "Sunny Loft · Downtown",
            "reviews": [
                {"date": d(4),  "guest": "Marta L.",  "rating": 5.0, "channel": "airbnb", "responded": True,
                 "text": "Spotless, great light, perfect location. Host was super responsive.",
                 "categories": c(5, 5, 5, 5, 5, 4)},
                {"date": d(19), "guest": "James K.",  "rating": 3.0, "channel": "airbnb", "responded": False,
                 "text": "Nice place but the AC was loud and there was no hot water the first night.",
                 "categories": c(4, 3, 3, 2, 5, 3)},
                {"date": d(33), "guest": "Lena V.",   "rating": 5.0, "channel": "vrbo", "responded": True,
                 "text": "Would absolutely book again. Beds are very comfortable.",
                 "categories": c(5, 5, 5, 5, 4, 5)},
                {"date": d(61), "guest": "Omar D.",   "rating": 4.0, "channel": "airbnb", "responded": True,
                 "text": "Good value, a little noisy from the street at night.",
                 "categories": c(4, 4, 5, 4, 5, 4)},
                {"date": d(90), "guest": "Priya S.",  "rating": 5.0, "channel": "airbnb", "responded": True,
                 "text": "Loved it. Check-in was seamless.", "categories": c(5, 5, 5, 5, 5, 5)},
            ],
        },
        {
            "id": "p2", "name": "Seaside Cottage · Bay View",
            "reviews": [
                {"date": d(2),  "guest": "Tom R.",    "rating": 2.0, "channel": "airbnb", "responded": False,
                 "text": "Disappointed — the place wasn't as clean as the photos and check-in was confusing.",
                 "categories": c(2, 2, 3, 2, 5, 3)},
                {"date": d(11), "guest": "Sofia M.",  "rating": 5.0, "channel": "booking", "responded": True,
                 "text": "Magical sunsets from the deck. Highly recommend.",
                 "categories": c(5, 5, 5, 5, 5, 5)},
                {"date": d(27), "guest": "Daniel B.", "rating": 4.0, "channel": "airbnb", "responded": True,
                 "text": "Lovely spot, kitchen could use more basics.",
                 "categories": c(4, 4, 4, 5, 5, 4)},
                {"date": d(48), "guest": "Aiko T.",   "rating": 5.0, "channel": "airbnb", "responded": True,
                 "text": "Perfect weekend getaway, very peaceful.", "categories": c(5, 5, 5, 5, 5, 5)},
            ],
        },
        {
            "id": "p3", "name": "Mountain Cabin · Pine Ridge",
            "reviews": [
                {"date": d(6),  "guest": "Greg H.",   "rating": 5.0, "channel": "airbnb", "responded": True,
                 "text": "Cozy, warm, and the views are unreal. Five stars.",
                 "categories": c(5, 5, 5, 5, 5, 5)},
                {"date": d(15), "guest": "Hannah W.", "rating": 5.0, "channel": "airbnb", "responded": True,
                 "text": "Everything you need for a quiet escape. Will return!",
                 "categories": c(5, 5, 5, 5, 4, 5)},
                {"date": d(40), "guest": "Carlos N.", "rating": 4.0, "channel": "vrbo", "responded": True,
                 "text": "Great cabin, road up was a bit rough in the rain.",
                 "categories": c(5, 4, 5, 4, 3, 5)},
            ],
        },
    ]


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
def summarize(prop):
    reviews = sorted(
        [r for r in prop["reviews"] if r.get("rating") is not None],
        key=lambda r: r["date"], reverse=True,
    )
    ratings = [r["rating"] for r in reviews]
    avg = round(sum(ratings) / len(ratings), 2) if ratings else None
    recent = reviews[:RECENT_COUNT]
    recent_avg = round(sum(r["rating"] for r in recent) / len(recent), 2) if recent else None

    reasons = []
    if any(r["rating"] <= LOW_RATING_THRESHOLD and not r["responded"] for r in recent):
        reasons.append("unanswered low review")
    dropping = (recent_avg is not None and avg is not None
                and recent_avg <= avg - DROP_THRESHOLD)
    if dropping:
        reasons.append("rating trending down")

    # Average each sub-category across recent reviews (where present).
    cat_avgs = {}
    for label in CATEGORY_LABELS:
        vals = [r["categories"].get(label) for r in recent if r.get("categories", {}).get(label) is not None]
        if vals:
            cat_avgs[label] = round(sum(vals) / len(vals), 1)

    return {
        "id": prop["id"], "name": prop["name"],
        "avg": avg, "recent_avg": recent_avg, "count": len(reviews),
        "recent": recent, "dropping": dropping,
        "needs_attention": bool(reasons), "reasons": reasons,
        "category_avgs": cat_avgs,
    }


def log_history(summaries, path):
    """Append a dated snapshot per property so trends accrue over time."""
    today = datetime.now().strftime("%Y-%m-%d")
    new_file = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "property_id", "property", "all_time_avg", "recent_avg", "review_count"])
        for s in summaries:
            w.writerow([today, s["id"], s["name"], s["avg"], s["recent_avg"], s["count"]])


# --------------------------------------------------------------------------- #
# Dashboard rendering
# --------------------------------------------------------------------------- #
def render_dashboard(props_summary, out_path):
    generated = datetime.now().strftime("%b %d, %Y · %H:%M")
    payload = json.dumps(props_summary)
    cat_labels = json.dumps(CATEGORY_LABELS)
    html = (_TEMPLATE
            .replace("__DATA__", payload)
            .replace("__CATLABELS__", cat_labels)
            .replace("__GENERATED__", generated)
            .replace("__THRESHOLD__", str(LOW_RATING_THRESHOLD))
            .replace("__RECENT__", str(RECENT_COUNT)))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Airbnb Review Tracker</title>
<style>
  :root{
    --bg:#f5f5f4; --card:#ffffff; --ink:#1c1917; --muted:#78716c;
    --line:#e7e5e4; --good:#16a34a; --warn:#d97706; --bad:#dc2626;
    --shadow:0 1px 3px rgba(0,0,0,.08),0 8px 24px rgba(0,0,0,.04);
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    line-height:1.45;padding:28px 18px 60px;}
  .wrap{max-width:1180px;margin:0 auto;}
  header{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px 16px;margin-bottom:6px}
  h1{font-size:22px;margin:0;letter-spacing:-.01em}
  .sub{color:var(--muted);font-size:13px}
  .legend{color:var(--muted);font-size:12.5px;margin:10px 0 22px;max-width:760px}
  .legend b{color:var(--bad)}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:18px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:16px;
    box-shadow:var(--shadow);overflow:hidden;display:flex;flex-direction:column}
  .card.attn{border-color:#fecaca;box-shadow:0 0 0 1px #fecaca,var(--shadow)}
  .chead{padding:16px 18px 12px;border-bottom:1px solid var(--line)}
  .pname{font-weight:650;font-size:16px;margin:0 0 8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .flag{font-size:10.5px;font-weight:700;color:#fff;background:var(--bad);
    padding:2px 7px;border-radius:99px;letter-spacing:.03em}
  .flag.drop{background:var(--warn)}
  .stats{display:flex;gap:18px;align-items:flex-end}
  .stat .n{font-size:24px;font-weight:700;line-height:1}
  .stat .l{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin-top:3px}
  .delta{font-size:12px;font-weight:600}
  .delta.up{color:var(--good)} .delta.down{color:var(--bad)} .delta.flat{color:var(--muted)}
  .cats{display:flex;flex-wrap:wrap;gap:5px;padding:10px 18px 0}
  .cat{font-size:11px;padding:2px 8px;border-radius:99px;background:#f5f5f4;border:1px solid var(--line);color:#57534e}
  .cat.lowcat{background:#fef2f2;border-color:#fecaca;color:var(--bad);font-weight:600}
  .recent-h{padding:13px 18px 4px;font-size:11px;font-weight:700;color:var(--muted);
    text-transform:uppercase;letter-spacing:.05em}
  .reviews{padding:0 18px 8px;display:flex;flex-direction:column}
  .rv{padding:11px 0;border-bottom:1px dashed var(--line)}
  .rv:last-child{border-bottom:none}
  .rv-top{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:4px}
  .rv-who{font-weight:600;font-size:13.5px}
  .rv-meta{font-size:11.5px;color:var(--muted);font-weight:400}
  .stars{font-size:13px;letter-spacing:1px;white-space:nowrap}
  .rv-text{font-size:13px;color:#44403c;margin:2px 0 6px}
  .rv-low .rv-text{color:#7f1d1d}
  .rv-cats{display:flex;flex-wrap:wrap;gap:4px;margin:4px 0 6px}
  .rvcat{font-size:10px;padding:1px 6px;border-radius:4px;background:#fafaf9;border:1px solid var(--line);color:#78716c}
  .rvcat.low{background:#fef2f2;border-color:#fecaca;color:var(--bad)}
  .tags{display:flex;gap:6px;flex-wrap:wrap}
  .tag{font-size:10.5px;padding:1.5px 7px;border-radius:99px;border:1px solid var(--line);color:var(--muted)}
  .tag.need{background:#fef2f2;border-color:#fecaca;color:var(--bad);font-weight:600}
  .tag.ok{background:#f0fdf4;border-color:#bbf7d0;color:var(--good)}
  .chan{text-transform:capitalize}
  footer{color:var(--muted);font-size:12px;margin-top:26px;text-align:center}
  .empty{padding:24px 18px;color:var(--muted);font-size:13px}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Airbnb Review Tracker</h1>
    <span class="sub">Last __RECENT__ reviews per property · generated __GENERATED__</span>
  </header>
  <div class="legend">
    Listings <b>needing attention</b> are sorted first. A listing is flagged when a recent review is
    ≤ __THRESHOLD__★ and unanswered, <b>or</b> when its recent average has dropped below its all-time average
    (even if you responded). Sub-category averages below 4★ are highlighted in red.
  </div>
  <div class="grid" id="grid"></div>
  <footer>Built for your Hospitable account · re-run the tracker to refresh · history saved to history.csv</footer>
</div>
<script>
const DATA = __DATA__;
const CATS = __CATLABELS__;
const THRESHOLD = __THRESHOLD__;

function stars(n){
  const full = Math.round(n);
  return '<span class="stars" style="color:'+(n<=THRESHOLD?'#dc2626':'#f59e0b')+'">'
    + '★'.repeat(full) + '<span style="color:#d6d3d1">'+'★'.repeat(5-full)+'</span></span>';
}
function fmtDate(s){
  if(!s) return '';
  const d = new Date(s);
  if(isNaN(d)) return s;
  return d.toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'});
}
function delta(recent, all){
  if(recent==null||all==null) return '';
  const diff = +(recent-all).toFixed(2);
  if(diff===0) return '<span class="delta flat">±0 vs all-time</span>';
  const cls = diff>0?'up':'down';
  return '<span class="delta '+cls+'">'+(diff>0?'▲ +':'▼ ')+diff+' vs all-time</span>';
}

const grid = document.getElementById('grid');
DATA.sort((a,b)=> (b.needs_attention - a.needs_attention) || (a.recent_avg - b.recent_avg));

for(const p of DATA){
  const card = document.createElement('div');
  card.className = 'card' + (p.needs_attention?' attn':'');

  let flags = '';
  for(const r of (p.reasons||[])){
    const drop = r.indexOf('down')>=0;
    flags += '<span class="flag'+(drop?' drop':'')+'">'+r.toUpperCase()+'</span>';
  }

  let catRow = '';
  for(const key in CATS){
    if(p.category_avgs[key]!=null){
      const low = p.category_avgs[key] < 4;
      catRow += '<span class="cat'+(low?' lowcat':'')+'">'+CATS[key]+' '+p.category_avgs[key]+'</span>';
    }
  }
  if(catRow) catRow = '<div class="cats">'+catRow+'</div>';

  let rv = '';
  if(p.recent.length===0){
    rv = '<div class="empty">No reviews yet.</div>';
  } else {
    for(const r of p.recent){
      const low = r.rating <= THRESHOLD;
      let rc = '';
      for(const key in CATS){
        const v = (r.categories||{})[key];
        if(v!=null) rc += '<span class="rvcat'+(v<4?' low':'')+'">'+CATS[key]+' '+v+'</span>';
      }
      if(rc) rc = '<div class="rv-cats">'+rc+'</div>';
      rv += '<div class="rv'+(low?' rv-low':'')+'">'
          + '<div class="rv-top"><span class="rv-who">'+r.guest
          + ' <span class="rv-meta">· '+fmtDate(r.date)+'</span></span>'+stars(r.rating)+'</div>'
          + '<div class="rv-text">'+(r.text||'<em>No comment left.</em>')+'</div>'
          + rc
          + '<div class="tags"><span class="tag chan">'+r.channel+'</span>'
          + (r.responded?'<span class="tag ok">responded</span>'
                        :'<span class="tag '+(low?'need':'')+'">'+(low?'needs response':'no response')+'</span>')
          + '</div></div>';
    }
  }

  card.innerHTML =
    '<div class="chead">'
    + '<div class="pname">'+p.name+flags+'</div>'
    + '<div class="stats">'
    +   '<div class="stat"><div class="n">'+(p.recent_avg??'—')+'</div><div class="l">recent avg</div></div>'
    +   '<div class="stat"><div class="n">'+(p.avg??'—')+'</div><div class="l">all-time</div></div>'
    +   '<div class="stat"><div class="n">'+p.count+'</div><div class="l">reviews</div></div>'
    +   '<div style="margin-left:auto">'+delta(p.recent_avg,p.avg)+'</div>'
    + '</div></div>'
    + catRow
    + '<div class="recent-h">Last '+p.recent.length+' reviews</div>'
    + '<div class="reviews">'+rv+'</div>';
  grid.appendChild(card);
}
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Airbnb review tracker dashboard")
    ap.add_argument("--sample", action="store_true", help="use built-in sample data (no token needed)")
    ap.add_argument("--out", default="dashboard.html", help="output HTML file")
    ap.add_argument("--history", default="history.csv", help="CSV file to append snapshots to")
    ap.add_argument("--no-history", action="store_true", help="skip writing to history.csv")
    args = ap.parse_args()

    props = fetch_sample() if args.sample else fetch_live()
    summaries = [summarize(p) for p in props]
    render_dashboard(summaries, args.out)
    if not args.no_history:
        log_history(summaries, args.history)

    flagged = [(s["name"], ", ".join(s["reasons"])) for s in summaries if s["needs_attention"]]
    print(f"Wrote {args.out} — {len(summaries)} properties, "
          f"{sum(s['count'] for s in summaries)} reviews total.")
    if not args.no_history:
        print(f"Logged snapshot to {args.history}.")
    for name, why in flagged:
        print(f"  ⚠ {name} — {why}")


if __name__ == "__main__":
    main()
