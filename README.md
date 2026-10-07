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

**Game initialization is part of M05.** In M04, creating a game does not create its territories,
pick capitals or change its status. In the Django Admin, territories and capitals can be inspected and
their `score` / `health` adjusted, but not added, deleted or re-linked.
