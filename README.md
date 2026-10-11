# Triviador

A multiplayer quiz-territory game: Django + Django REST Framework backend, React + Vite frontend.

## Project layout

```text
backend/    Django project (config/, accounts/, questions/, games/, territories/)
frontend/   React + Vite app
```

## Backend setup

Windows PowerShell, from the repository root:

```powershell
py -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
```

Create a `.env` file in the repository root:

```env
SECRET_KEY=change-me
DEBUG=True
```

Apply migrations and load the question bank:

```bash
python backend/manage.py migrate
python backend/manage.py loaddata questions/question_bank.json
```

Optionally create an admin account for the Django Admin at http://127.0.0.1:8000/admin/:

```bash
python backend/manage.py createsuperuser
```

Run the development server:

```bash
python backend/manage.py runserver
```

## Frontend setup

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api` to Django on port 8000, so both servers must be running.

## Tests

```bash
python backend/manage.py test accounts questions games territories
```

## Question bank

The initial questions live in `backend/questions/fixtures/questions/question_bank.json`
(6 categories, 12 multiple-choice questions with 4 options each, 12 numeric questions).

Load it with:

```bash
python backend/manage.py loaddata questions/question_bank.json
```

Questions can then be managed in the Django Admin under **Questions**.

## Games and rounds

The `games` app stores the game domain: `Game`, `GamePlayer` (up to 3 per game),
`Round` (one choice or numeric question each) and `RoundAnswer` (one per player per round).
Database constraints guard the core rules, and everything can be inspected in the Django Admin under **Games**.

## Territories and map

The `territories` app holds each game's map state: `Territory` (name, slug, neighbors, owner, score)
and `Capital` (a player's capital territory and its health, 0-3).
Every game gets its **own** territory records, so ownership and scores never leak between games.

The map itself is defined once for the whole project in
`backend/territories/map_definition.py` (`PROJECT_MAP`): **18 territories, 38 borders**,
loosely following the regions of Bulgaria. Every border works in both directions.

| # | Territory | Slug | Neighbors |
|---|---|---|---|
| 1 | Skali | `skali` | Dunavia, Sredets |
| 2 | Dunavia | `dunaviya` | Balkania, Leventa, Skali, Sredets |
| 3 | Leventa | `leventa` | Balkania, Dunavia, Madara, Tsarevo, Zhitno Pole |
| 4 | Zhitno Pole | `zhitno-pole` | Kaliakra, Leventa, Madara, Zlaten Grozd |
| 5 | Kaliakra | `kaliakra` | Zhitno Pole, Zlaten Grozd |
| 6 | Sredets | `sredets` | Balkania, Chuden Kray, Dunavia, Ezera, Kukeri, Skali |
| 7 | Balkania | `balkania` | Chuden Kray, Dunavia, Leventa, Rozova Dolina, Sredets, Tsarevo |
| 8 | Tsarevo | `tsarevo` | Balkania, Karakachan, Leventa, Madara, Rozova Dolina |
| 9 | Madara | `madara` | Karakachan, Leventa, Slanchevo, Tsarevo, Zhitno Pole, Zlaten Grozd |
| 10 | Zlaten Grozd | `zlaten-grozd` | Kaliakra, Madara, Slanchevo, Zhitno Pole |
| 11 | Kukeri | `kukeri` | Ezera, Sredets |
| 12 | Rozova Dolina | `rozova-dolina` | Balkania, Chuden Kray, Karakachan, Tsarevo |
| 13 | Karakachan | `karakachan` | Chuden Kray, Madara, Pelikania, Rozova Dolina, Slanchevo, Tsarevo |
| 14 | Slanchevo | `slanchevo` | Karakachan, Madara, Pelikania, Zlaten Grozd |
| 15 | Pelikania | `pelikania` | Chuden Kray, Karakachan, Slanchevo |
| 16 | Ezera | `ezera` | Chuden Kray, Kukeri, Pirina, Sredets |
| 17 | Pirina | `pirina` | Chuden Kray, Ezera |
| 18 | Chuden Kray | `chuden-kray` | Balkania, Ezera, Karakachan, Pelikania, Pirina, Rozova Dolina, Sredets |

The definition is checked by `validate_map_definition()`: size 9-21 and a multiple of 3, unique names and slugs,
no self-links or duplicate borders, at least 2 neighbors each, fully connected, and at least one set of
three pairwise non-adjacent territories for the starting capitals. `validate_game_map(game)` checks,
read-only, that a game's saved territories and borders match the definition exactly.

Creating a game does not create its territories, pick capitals or change its status; that is the
separate initialization step below. In the Django Admin, territories and capitals can be inspected and
their `score` / `health` adjusted, but not added, deleted or re-linked.

## Game initialization

Initialization (`games/initialization.py`, `initialize_game(game_id)`) turns a waiting game into a
running one in **one database transaction**:

1. checks the game is `waiting`, has exactly 3 players and has no map records yet;
2. checks the project map definition is valid;
3. creates the game's own 18 territories and all their borders from `PROJECT_MAP`;
4. picks a random set of three pairwise non-adjacent territories and randomly gives one to each player
   as their capital (owner set, health 3); the other 15 territories stay neutral, every score stays 0;
5. sets the game status to `in_progress` and `started_at`.

The milestone calls the running state `active`; this project stores it as `in_progress`, the status
defined in M03. Player scores and rounds are not touched.

If any check fails, or anything goes wrong midway, nothing is changed: the error says why and the whole
transaction is rolled back. A second attempt, or a game that already has some map records, is refused
rather than repaired. Once a game has started, players can no longer join or leave it.

### How to run it

1. Create three users (sign up in the app or use `createsuperuser`).
2. In the Django Admin, add a **Game** and give it three **players** (orders 1, 2, 3).
3. Start it in one of two ways:
   - Admin: tick the game in the Games list, choose **Initialize selected games**, click **Go**.
   - Command line: `python backend/manage.py initialize_game <game id>`
4. The game's territories and capitals then appear under **Territories**.

### Concurrent attempts and the database

Two simultaneous attempts on the same game must not produce two maps. The code protects this in layers:

- `select_for_update()` locks the game row, so on **PostgreSQL** the second attempt waits for the first
  and then sees the game is no longer waiting. Joining or leaving a game locks the same row.
- Unique constraints (`(game, slug)` per territory, one capital per player and per territory, player
  order 1-3 per game) make a second map or a fourth player impossible at the database level anyway.

**SQLite (used for development) does not support row locks**: `select_for_update()` is silently ignored.
Instead, `transaction_mode: IMMEDIATE` in `settings.py` makes every transaction take the database-wide
write lock when it begins, so attempts run strictly one after another. This is coarser than PostgreSQL
(all writes are serialized, not just those for one game) and a writer gives up with
`database is locked` after the 20 s `timeout`. The concurrency tests (`games/tests/test_concurrency.py`)
run real threads against a file-based SQLite test database; they would need re-running on PostgreSQL to
confirm the row-lock behaviour there.
