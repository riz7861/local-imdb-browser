# Local IMDb Browser

A local Flask browser for IMDb non-commercial datasets. It uses `imdb-sqlite` to build `imdb.db` with only the `titles`, `ratings`, and `episodes` tables, then stores local users, per-user watchlists, external ratings, and optional poster URLs in that same SQLite file.

Everything runs on your machine. Public search browsing is open, but saving and managing watchlist items requires a local username/password login. There is no hosted service or remote database.

## What It Includes

- Browse IMDb titles with filters for type, start year, end year, genre, minimum rating, minimum votes, and title search.
- Results show title, year, type, genres, rating, votes, and an IMDb link.
- Adult titles are hidden by default. The include-adult checkbox is locked behind a local confirmation screen and defaults back to off in each new browser session.
- Default browsing includes movies, TV series, mini-series, and TV movies. Episodes only appear when the Episode type is selected.
- TV episodes show their parent series plus season and episode numbers when available.
- Optional region/language filtering uses IMDb akas data when imported.
- Search results can be sorted by rating, votes, year, title, or Quality Score.
- Quality Score supports Balanced, Audience-heavy, and Critic-heavy profiles, alternate score modes, diagnostics, and side-by-side comparison.
- Page sizes include `25`, `50`, `100`, `250`, `500`, `1000`, and guarded `All`.
- Export all filtered search results to CSV without rendering every row in the browser first.
- Optional OMDb fetching stores Metascore and Rotten Tomatoes scores locally in `external_ratings`.
- Optional TMDb poster fetching stores poster URLs locally in `poster_cache`.
- Add and remove titles from a per-user local watchlist after login.
- Track watchlist status, notes, and `added_at`.
- Filter the watchlist and export it as CSV.

## Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python setup_imdb.py
python manage_users.py create-admin
python app.py
```

Open http://127.0.0.1:5000

## macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python setup_imdb.py
python manage_users.py create-admin
python app.py
```

Open http://127.0.0.1:5000

## Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python setup_imdb.py
python manage_users.py create-admin
python app.py
```

Open http://127.0.0.1:5000

## Railway Deployment

This repository includes a `Procfile` for Railway:

```text
web: gunicorn app:app --bind 0.0.0.0:$PORT
```

Railway's Flask guide recommends Gunicorn for production serving, and Railway public networking expects the app to listen on `0.0.0.0:$PORT`.

Deploy from GitHub:

1. Push this repository to GitHub.
2. In Railway, create a new project and choose `Deploy from GitHub repo`.
3. Select `riz7861/local-imdb-browser` and deploy the app service.
4. In the Railway service variables, set `SECRET_KEY` to a long random value. Optional fetcher variables such as `OMDB_API_KEY`, `TMDB_API_KEY`, and `IMDB_BROWSER_DB` can also be set there.
5. Create or restore the production SQLite database separately. The repository does not include `imdb.db`; use a persistent Railway volume or another restore process, then point `IMDB_BROWSER_DB` at that database path if it is not the default app root path.
6. Open the service logs to confirm Gunicorn started, then use Networking > Public Networking > Generate Domain to expose the app.

Startup note: production data must exist before useful browsing. The app can start without `imdb.db` and show the setup-needed page, but Railway deployment does not build or restore the IMDb database automatically.

Do not commit local databases, downloaded IMDb datasets, `.env` files, or secrets. `.gitignore` excludes `imdb.db`, `*.db`, `.env`, `downloads/`, and secret folders/files.

## Setup Notes

`setup_imdb.py` installs the Python dependencies from `requirements.txt`, downloads the IMDb non-commercial datasets through `imdb-sqlite`, and builds:

```bash
imdb-sqlite --db imdb.db --cache-dir downloads --only titles,ratings,episodes
```

The import is large and can take a while. The `imdb-sqlite` project notes that importing only titles, ratings, and episodes is much smaller than a full import, but still needs multiple gigabytes of free disk space.

Region/language filtering requires the IMDb `title.akas` dataset. To include it, rebuild with:

```bash
python setup_imdb.py --with-akas --rebuild
```

That runs:

```bash
imdb-sqlite --db imdb.db --cache-dir downloads --only titles,akas,ratings,episodes
```

This increases database size. The filters are region/language based and may not be perfect because IMDb `title.basics` does not contain a canonical language field. The app maps:

- Hollywood / English: regions `US`, `GB`, `CA`, `AU` or language `en`
- Bollywood / Hindi: region `IN` or language `hi`
- Turkish: region `TR` or language `tr`
- Arabic: regions `EG`, `SA`, `AE`, `LB`, `MA`, `DZ` or language `ar`

IMDb data is provided for personal and non-commercial use. Review IMDb's dataset terms before using the data beyond personal/local browsing.

## Users And Login

Search and browsing stay public inside your local app. Watchlist pages, CSV export, and add/remove/update actions require login.

Create the first admin/local user:

```bash
python manage_users.py create-admin
```

The command prompts for a password. You can also pass a username:

```bash
python manage_users.py create-admin --username admin
```

For unattended local setup, set `IMDB_ADMIN_PASSWORD` before running the command:

```powershell
$env:IMDB_ADMIN_PASSWORD = "change-this-local-password"
python manage_users.py create-admin --username admin
```

On macOS/Linux:

```bash
export IMDB_ADMIN_PASSWORD=change-this-local-password
python manage_users.py create-admin --username admin
```

Passwords are stored with Werkzeug password hashing in the local `users` table. Watchlist rows are keyed by `user_id` and `title_id`, so each local user gets a separate watchlist.

## Useful Options

Use an existing database and only create the app tables/indexes:

```bash
python setup_imdb.py --skip-install --skip-import --db path/to/imdb.db
```

Rebuild the database from scratch:

```bash
python setup_imdb.py --rebuild
```

Save disk space by asking `imdb-sqlite` not to create its default indexes:

```bash
python setup_imdb.py --no-index
```

Build with region/language filter support:

```bash
python setup_imdb.py --with-akas
```

## External Ratings

Metascore and Rotten Tomatoes data come from OMDb and are never fetched inside a Flask request. Set `OMDB_API_KEY` in your environment or in a local `.env` file:

```text
OMDB_API_KEY=your_key_here
```

Fetch ratings for up to 100 titles:

```bash
python fetch_external_ratings.py --limit 100
```

By default the fetcher only selects non-adult `movie`, `tvSeries`, `tvMiniSeries`, and `tvMovie` titles with at least 5,000 IMDb votes, ordered by highest vote count first. Tune that with:

```bash
python fetch_external_ratings.py --limit 100 --min-votes 10000 --types movie,tvSeries
```

To include local watchlist titles even when they are below the vote threshold, and process them first:

```bash
python fetch_external_ratings.py --limit 100 --watchlist-priority
```

Refresh already fetched rows:

```bash
python fetch_external_ratings.py --limit 100 --force
```

The script fetches by IMDb ID with OMDb's `i=<title_id>` parameter, stores safe resume state in `external_ratings`, handles `N/A` values, rate limits requests with a small delay between calls, and prints fetched/skipped/remaining counts plus an API quota estimate before and during the run.

## Posters

Poster support is optional and uses TMDb. Set `TMDB_API_KEY` in your environment or in `.env`:

```text
TMDB_API_KEY=your_key_here
```

Fetch poster URLs for up to 100 titles:

```bash
python fetch_posters.py --limit 100
```

The poster job fetches by IMDb ID, skips rows already in `poster_cache`, prioritizes titles that appear on any local watchlist, rate limits requests, and stores only the poster URL, TMDb ID, and fetch timestamp. Flask never calls TMDb during page requests. If a title has no cached poster, the UI shows a clean placeholder.

## Performance Notes

`setup_imdb.py` creates SQLite indexes for common browsing paths: title type, year, adult flag, title text, IMDb rating, votes, Metascore, Rotten Tomatoes, poster lookup, and per-user watchlist filters. The Flask app maintains lightweight local-table indexes for users, watchlists, external ratings, and posters during startup/request schema checks. Query parameters for sorting and filtering are allowlisted before SQL is built.

For an existing database created before these indexes were added, run:

```bash
python setup_imdb.py --skip-install --skip-import --db path/to/imdb.db
```

Search queries slower than 500ms are logged through Python logging. When Flask debug mode is enabled, the results page also shows query timing; in normal mode timing stays hidden. Expensive Quality Score diagnostics are calculated only when `Show score breakdown` is enabled.

The `All` page size remains guarded by the safe display limit, so large filtered result sets should be exported instead of rendered into the browser all at once.

## Recommended Background Commands

Build IMDb data with region/language support:

```bash
python setup_imdb.py --with-akas --rebuild
```

Create or refresh your local admin user:

```bash
python manage_users.py create-admin --username admin
```

Fetch external critic ratings:

```bash
python fetch_external_ratings.py --limit 500 --watchlist-priority
```

Fetch posters:

```bash
python fetch_posters.py --limit 500
```

## Quality Score

Quality Score is a local 0-100 discovery score. It blends IMDb rating, IMDb vote confidence, Metascore, and Rotten Tomatoes when those values are available. Scores display with one decimal place throughout the table, cards, and comparison panel.

IMDb is first adjusted with Bayesian vote confidence so low-vote titles are pulled toward a baseline instead of floating to the top from a tiny sample:

```text
adjusted_imdb =
  (votes / (votes + 25000)) * (imdb_rating * 10)
  + (25000 / (votes + 25000)) * 65
```

The default `25000` threshold means high-vote titles stay close to their IMDb rating, while very low-vote titles move strongly toward the global mean of `65`. The vote confidence component is kept small once a title is already well established, so popular titles can still separate based on Metascore and Rotten Tomatoes.

Profiles control the weights:

- Balanced: IMDb adjusted `45%`, Metascore `30%`, Rotten Tomatoes `20%`, vote confidence `5%`
- Audience-heavy: IMDb adjusted `70%`, Metascore `15%`, Rotten Tomatoes `10%`, vote confidence `5%`
- Critic-heavy: IMDb adjusted `25%`, Metascore `45%`, Rotten Tomatoes `25%`, vote confidence `5%`

Score modes change the objective:

- Profile Score: the selected profile's weighted discovery score.
- Consensus Score: rewards close agreement between IMDb, Metascore, and Rotten Tomatoes.
- Polarizing Score: highlights large audience/critic gaps.
- Hidden Gem Score: boosts high-scoring titles with lower but credible vote counts while penalizing tiny vote samples.

When Metascore or Rotten Tomatoes is missing, the app uses a conservative IMDb/global fallback for that source instead of treating it as zero or letting IMDb take over the full weight. Enable `Show score breakdown` in the sidebar to see:

- raw IMDb converted to 0-100
- Bayesian adjusted IMDb
- IMDb vote confidence
- Metascore and Rotten Tomatoes values used by the score
- missing-rating fallback status
- weighted score before rounding
- final displayed Quality Score

Use the row or card checkboxes and `Compare selected` to compare 2 to 4 titles side by side. The comparison panel shows each title's components, source spread, audience/critic gap, mode adjustment, and the reason their scores differ.

## Large Result Sets

The browser page owns vertical scrolling. The results table uses natural page flow with horizontal scrolling only when the table is wider than the viewport. On narrow screens, the default view falls back to cards unless the table view is explicitly selected.

`All` page size is allowed only when the filtered result count is at or below `2,000`. Larger result sets show:

```text
Too many results to display at once. Narrow filters or export results.
```

Use `Export all filtered results to CSV` to export the full filtered and sorted set with the active score profile and mode without rendering all rows in the browser.

Use a custom database path when running the app:

```powershell
$env:IMDB_BROWSER_DB = "C:\path\to\imdb.db"
python app.py
```

On macOS/Linux:

```bash
export IMDB_BROWSER_DB=/path/to/imdb.db
python app.py
```
