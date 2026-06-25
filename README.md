# Airbnb Review Tracker

A per-property dashboard of your latest guest reviews, pulled from Hospitable and
refreshed automatically every day by GitHub. Built to surface the **last 3 reviews**
per listing (the ones that most affect your Airbnb rating and ranking) and flag
anything that needs your attention.

## What it shows

For every property:

- The **last 3 reviews** with star ratings, guest, date, channel, and whether you've responded.
- **Recent average vs all-time average** with an up/down arrow, so you can see trends at a glance.
- **Sub-category ratings** (cleanliness, accuracy, communication, check-in, location, value); anything below 4★ is highlighted red.
- A **"needs attention"** flag when either:
  1. a recent review is 4★ or lower and you haven't responded, or
  2. the recent average has dropped below the all-time average (even if you responded).

It also appends a dated snapshot to `history.csv` on every run, which builds up
rating-over-time data (Hospitable's API keeps no history of its own).

---

## One-time setup (about 10 minutes, no coding)

### 1. Create the repository

1. Go to [github.com/new](https://github.com/new).
2. Name it something like `review-tracker`. Set it to **Private**. Click **Create repository**.

### 2. Upload these files

On the new repo page, click **uploading an existing file** and drag in:

- `review_tracker.py`
- `requirements.txt`
- `.gitignore`
- the `.github` folder (with `workflows/update-dashboard.yml` inside)

> Tip: if the web uploader won't take the `.github` folder, create the file manually:
> click **Add file → Create new file**, type `.github/workflows/update-dashboard.yml`
> as the name (the slashes create the folders), paste the contents, and commit.

Click **Commit changes**.

### 3. Add your Hospitable token as a secret

This keeps your token encrypted — it never appears in the code or the logs.

1. In the repo, go to **Settings → Secrets and variables → Actions**.
2. Click **New repository secret**.
3. Name: `HOSPITABLE_PAT`
4. Secret: paste your Hospitable Personal Access Token.
5. Click **Add secret**.

### 4. Turn on GitHub Pages

1. Go to **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.

### 5. First run — confirm the data (one time)

1. Go to the **Actions** tab → **Update review dashboard** → **Run workflow**.
2. Tick the **inspect** box, then click the green **Run workflow**.
3. When it finishes, open the run → the **Generate dashboard** step. Copy the
   block that starts with `INSPECT:` and send it to me. It contains only field
   names and types — no guest names, review text, or ratings — so it's safe to share.
4. I'll confirm the tracker is reading your data correctly (and adjust if needed).

### 6. Go live

1. Run the workflow again (Actions → Run workflow), this time **without** the inspect box ticked.
2. Your dashboard is now live at:
   `https://<your-username>.github.io/review-tracker/`
3. From now on it refreshes itself every morning. Share that link with your team.

---

## Running it locally (optional)

If you ever want to run it on your own computer:

```bash
pip install -r requirements.txt
export HOSPITABLE_PAT="your_token_here"     # Windows: set HOSPITABLE_PAT=your_token_here
python review_tracker.py                    # writes dashboard.html + history.csv
python review_tracker.py --sample           # preview with demo data, no token needed
```

## Settings you can tweak

Open `review_tracker.py` and edit the values near the top:

- `LOW_RATING_THRESHOLD` (default 4.0) — at/below this, a review is "low".
- `DROP_THRESHOLD` (default 0.1) — how far recent avg must fall below all-time to flag a drop.
- `RECENT_COUNT` (default 3) — how many recent reviews to headline.

## Notes

- The token expires after 1 year — regenerate it in Hospitable and update the
  `HOSPITABLE_PAT` secret when it does.
- Requires a paid Hospitable plan (Host, Professional, or Mogul). Essentials can't use the API.
