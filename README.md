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

## Numeric rounds (M06)

`games/rounds.py` is the service layer for one numeric **city capture** round of an active game. Every
operation runs in one transaction: it either applies all of its changes or raises an error and changes nothing.

| Operation | What it does |
|---|---|
| `create_round(game_id)` | Next round number (highest existing + 1), random unused numeric question, all 3 players, status `pending`. No timer yet. |
| `start_round(round_id)` | `pending` → `open`: the first question gets a common start and a deadline 10 s later. |
| `submit_answer(round_id, player_id, value)` | Records one whole-number answer. When every participant has answered, the question is evaluated at once. |
| `evaluate_question(round_id)` | Evaluates the active question without a new answer. Refused while time is left and someone has not answered. |
| `round_state(round_id)` | Read-only: current question, deadline, seconds left, answers, ranking and winner, as plain data. |

The milestone's round statuses `pending → active → completed` are stored as the M03 statuses
`pending → open → evaluated` (`closed` is not used by this workflow), and `completed_at` is `Round.finished_at`.
`Game.get_current_round()` returns the round with the highest number.

### Rules

- **Time.** The server reads the clock (`games.rounds.now()`) as soon as an answer comes in, before the
  deadline check, and uses that same moment to rank speed. The client never sends a timestamp. An answer
  at `started_at + 10 s` or later is refused. Times are not rounded to whole seconds.
- **Answers.** One answer per player per question. A wrong or invalid attempt (`"3.5"`, `3.5`, `"abc"`,
  `True`, out of range) is rejected and the player can still answer. A second answer never replaces the
  first, and a player who doesn't answer is not counted as having answered 0.
- **Ranking.** Smaller distance from the correct answer wins. At equal distance the faster answer wins,
  even when the two answers are on opposite sides of the correct one. A city capture round decides
  places 1 and 2 (`places_needed = 2`, fixed when the round is created). With only one answer there is
  only a first place.
- **Follow-up questions.** If players tie exactly (same distance and same moment) for a needed place, a
  new question goes only to the tied group. If nobody answered, a new question goes to the same group.
  Places already decided are kept, and answers to different questions are never compared. A round
  asks at most 3 questions, and each new one gets its own 10 s from the moment it is activated.
  Closing one question and asking the next happen in one transaction.
- **Ending.** When the result is decided, the round becomes `evaluated` with `finished_at` and its place-1
  player as `winner`. Unfilled places get a recorded `unresolved_reason`:
  `question_limit` (3 questions used; this reason wins if the bank is also empty), `bank_exhausted`, or
  `not_enough_answers`. A round without a winner is valid only with a reason, which the model validation
  and database constraints both enforce. An evaluated round is never re-evaluated.
- **Questions** are never repeated within a game. A round's text and correct answer are copied
  when it is asked, so editing the question bank later changes neither the history nor the scoring.

### Errors

Every refusal is a subclass of `games.rounds.RoundError` (a `ValidationError`). Its class and `kind`
tell the caller why the request was refused:

| Class | `kind` | Example from the project |
|---|---|---|
| `InvalidRoundInput` | `invalid_input` | `submit_answer(r, p, "1886.5")` → *The answer must be a whole number (got '1886.5').* |
| `RoundStateError` | `invalid_state` | a second answer → *This player has already answered this question.* |
| `RoundNotFound` | `not_found` | `submit_answer(999999, p, 1)` → *Round #999999 does not exist.* |

### How to run it

The game must already be initialized (M05) and the question bank must have numeric questions.

```bash
python backend/manage.py numeric_round create <game id>
python backend/manage.py numeric_round start <round id>
python backend/manage.py numeric_round answer <round id> <username> <number>   # within 10 s
python backend/manage.py numeric_round evaluate <round id>                     # after the deadline
python backend/manage.py numeric_round show <round id>
```

On Windows, set `PYTHONIOENCODING=utf-8` so the Cyrillic question text prints. Real run with three
users (ana, boris, cveta) on an initialized game:

```
$ manage.py numeric_round answer 1 ana 1880
Answer accepted.
$ manage.py numeric_round answer 1 ana 1885
CommandError: [invalid_state] This player has already answered this question.
$ manage.py numeric_round answer 1 boris 1886.5
CommandError: [invalid_input] The answer must be a whole number (got '1886.5').
$ manage.py numeric_round evaluate 1
CommandError: [invalid_state] The question is still running: not everyone has answered and time is not up.
$ manage.py numeric_round answer 1 boris 1890
$ manage.py numeric_round answer 1 cveta 1885
Round 1 of game #1: evaluated
  Q1 [completed] През коя година е Съединението на България?  players: ana, boris, cveta
      ana: 1880  (correct 1885)
      boris: 1890  (correct 1885)
      cveta: 1885  (correct 1885)
  Place 1: cveta
  Place 2: ana
  Winner: cveta
```

Ana and Boris are both 5 away from the correct answer, so Ana's earlier answer takes 2nd place.

### Data decision

**Chosen:** a separate `RoundQuestion` row for every question asked in a round. It stores a snapshot of
the text and correct answer, its group of players, the places it decides, its start and deadline.
`RoundAnswer` is reused with a `question` link and the `place` the answer earned. The ranking is just
the answers that have a place, so a reload rebuilds the current question, the time left and the ranking
from stored rows alone, and the full history of tie-breaks is kept.

**Rejected:** creating a new `Round` for each tie-break question. It would reuse the M03 model as is,
but the milestone says follow-up questions keep the round's number, and round numbers are unique
per game. The "at most 3 questions" and "only the tied group answers" rules would also have to span
several rounds.

### Two simultaneous answers from one player

The unique constraint `(question, player)` on `RoundAnswer` guarantees at most one stored answer at the
database level, on any database. If two attempts get past the "already answered?" check at the same
moment, the second insert fails and is reported as `RoundStateError`, never as a server error. Every
changing operation also locks the round row with `select_for_update()`. On PostgreSQL that serializes
attempts on the same round. On SQLite, `transaction_mode: IMMEDIATE` serializes all writers instead (see
above). `games/tests/test_concurrency.py` runs two real threads and checks that exactly one answer is
accepted.

## Territory capture and scoring (M07)

`games/capture.py` links the result of a city capture round to territory picks and points.
`games/scoring.py` holds the scoring systems.

### Flow

1. **Round creation** (`create_round`) is refused while a round is unfinished, while capture picks from
   the last round are still unused, or when no territory is free (the conquest phase is over; the game
   itself stays `in_progress`). The number of free territories is stored on the round
   (`Round.free_territories`) and fixes, before the start, how many places are needed. With 3 or more free
   it needs 1st and 2nd. With 1 or 2 free it needs only 1st, so no question is asked just to decide 2nd.
2. **Round completion** (the M06 `_complete`) does three things in the same transaction: it saves the final
   ranking and the winner, awards the win, and creates the capture rights. Re-evaluating a completed round
   is refused, so it can add neither rights nor a second award.

   | Free territories at creation | First picks | Second picks (if decided) |
   |---|---|---|
   | 3 or more | 2 | 1 |
   | 2 | 2 | 0 |
   | 1 | 1 | 0 |

   A round without a winner gives no award and no rights; the territories wait for the next round. If
   2nd place is unresolved, the first keeps both picks and the third free territory stays for the next round.
3. **Picks** (`choose_territory(game_id, player_id, territory_id)`) are separate operations. The first player
   uses all of their picks before the second picks. A pick needs an active game, the player's turn and a
   free territory of the same game that borders something the player owns (the capital counts). Only
   when no free territory borders any of their territories may the player pick any free one. Allowed
   territories are recomputed after every pick, so the first's second pick may border the territory just
   taken. Each pick does four things in one transaction: change the owner, raise `Territory.score`, credit
   the capture points and use one pick. A refused pick changes nothing and keeps the right.

### Scoring (chosen system: value-based)

| What | Points |
|---|---|
| Round win (1st place) | **+150** to `GamePlayer.score`, once per round, when the round is completed |
| Territory value | `Territory.score` **+100** per capture (0 → 100 on the first capture) |
| Capture | the territory's value **after** the increase, i.e. 100 for a first capture |

Follow-up questions in a round give no separate award. Every change is stored in `ScoreEntry` with the
score before and after, the territory score before and after, and the calculation as text. Database
constraints guarantee that after = before + points, that points are positive, that a round has one win
entry, and that a territory has one capture entry. Scores never go down here and never become negative.

### Two systems on the same results

Same results: round 1 ana 1st, boris 2nd, ana picks 2 and boris picks 1; round 2 cveta 1st, 2nd unresolved, cveta picks 2.

| System | Win | Capture | Territory | ana | boris | cveta |
|---|---|---|---|---|---|---|
| **A. Value-based (chosen)** | 150 | value after increase (100) | +100 | 150+100+100 = **350** | **100** | 150+100+100 = **350** |
| B. Rank-based | 50 | 100 for 1st's pick, 60 for 2nd's | +1 | 50+100+100 = **250** | **60** | 50+100+100 = **250** |

Both are defined in `games/scoring.py`. `test_two_scoring_systems_on_the_same_results` checks the numbers above.

**Why A.** The difference between 1st and 2nd comes from what they actually win: 1st gets the win bonus
and two territories, 2nd gets one territory. A territory is worth the same to whoever takes it,
because its points come from the territory, not from the rank. Ranking is already rewarded by the
number of picks, so B rewards rank twice and makes 2nd's territory worth less for no map-related reason.
In A the capture points follow `Territory.score`, so a territory that gains value later is worth more
when someone captures it.

### How to run it (real local records)

```bash
python backend/manage.py numeric_round create <game id>    # then start / answer as in M06
python backend/manage.py capture show <game id>            # free territories, whose turn, scores
python backend/manage.py capture pick <game id> <username> <territory slug>
```

Run on a fresh local database (three users, game initialized by M05):

```
# Round 1: ana 1885, boris 1880, cveta 1900 (correct 1885) -> places 1: ana, 2: boris
$ manage.py capture show 1
ana's turn, 2 pick(s) left; allowed: Zhitno Pole, Zlaten Grozd
Scores: ana 150, boris 0, cveta 0
$ manage.py capture pick 1 ana zhitno-pole
ana took Zhitno Pole: 150 + 100 = 250 (territory 0 + 100 = 100; points = territory value after = 100)
ana's turn, 1 pick(s) left; allowed: Madara, Zlaten Grozd          <- Madara borders the new territory
$ manage.py capture pick 1 boris ezera
CommandError: [invalid_state] It is not this player's turn to pick (waiting for ana).
$ manage.py capture pick 1 ana zlaten-grozd
ana took Zlaten Grozd: 250 + 100 = 350 (territory 0 + 100 = 100; points = territory value after = 100)
$ manage.py capture pick 1 boris ezera
boris took Ezera: 0 + 100 = 100 (territory 0 + 100 = 100; points = territory value after = 100)
No picks pending.
Scores: ana 350, boris 100, cveta 0
# Round 2: cveta 1990, boris 1970, ana 2000 (correct 1989) -> places 1: cveta, 2: ana
$ manage.py capture pick 1 cveta balkania
cveta took Balkania: 150 + 100 = 250 (territory 0 + 100 = 100; points = territory value after = 100)
Scores: ana 350, boris 100, cveta 250
```

### Concurrent picks

Every pick locks the game row (`select_for_update`). It then changes the territory and the right only
with conditional updates: `filter(pk=..., owner__isnull=True).update(...)` and
`filter(pk=..., used__lt=F('allowed')).update(used=F('used') + 1)`. If either touches 0 rows, another pick
got there first: the transaction is rolled back and the attempt is refused as `invalid_state`. The unique
constraint "one capture entry per territory" is a last line of defence. On SQLite the
`IMMEDIATE` transactions serialize the picks (see M05). `ConcurrentCaptureTests` runs two real
threads for one free territory and for the last pick, and in both cases exactly one succeeds.

## Battles (M08)

`games/battles.py` creates battles and applies their result. The round itself (start, one answer per
player, 10 s, evaluation, the worker, the limit of 3 questions) is the same shared lifecycle in
`games/rounds.py` that city capture rounds use. Only the answer check and the "what's next" step
depend on the kind of question.

### Who fights over what

`create_battle(game_id, attacker_id, target_id, defender_id=None)` is allowed when the game is active,
every territory has an owner, and there is no unfinished round and no unused pick. The target must be
another player's territory of the same game that borders something the attacker owns. The "any free
territory" exception from M07 does not apply here. The defender is always the target's owner; a
different `defender_id` is refused. A capital makes the round a `capital_attack`, any other territory a
`battle`. Attacker, defender and target are stored on the `Round`, and the third player cannot answer.

### Questions

| Step | Result | Next step (same round, new 10 s) |
|---|---|---|
| choice | one correct answer | its author wins |
| choice | two correct | numeric question for the same two |
| choice | none correct (wrong = no answer) | another choice question for the same two |
| numeric | closer, then faster wins 1st | if tied or unanswered, numeric again (never back to choice) |

Two correct choice answers are not compared by speed. A battle needs only 1st place
(`places_needed = 1`), so a tie for 2nd never asks a question. A next step is asked only if there is
no winner yet, the limit of 3 questions allows it, and the bank has a suitable unused question.
Otherwise the round completes without a winner, with reason `question_limit` (preferred) or
`bank_exhausted`. A battle with no valid unused choice question to start with is refused without
changes. A valid choice question has exactly 4 options, exactly one of them correct.

A choice answer is the id of one of the step's own four options. An option of any other question
(even an existing one) is refused as `invalid_input` and the player can still answer.

### Data: how the different questions are stored

**Chosen:** every step is a `RoundQuestion` with a `kind` (`numeric` / `choice`) and **two separate
links**, `numeric_question` and `choice_question`. Exactly one link is set, matching the kind, and a
database constraint enforces it. The bank models stay as they are. What was asked is copied into the
step: the text and correct number for numeric steps, and the four options into `RoundQuestionOption`
(text, which one is correct) for choice steps. An answer is a `RoundAnswer` linked to **one step and
one player** (unique together), holding either `selected_option` or `numeric_value`. A round's history
is its steps ordered by `sequence`, each with its own kind, options and answers (`round_state()`
returns it as data). Editing the bank afterwards changes neither the shown history nor the result.

**Same ids in both models:** if `ChoiceQuestion` #777 and `NumericQuestion` #777 both exist, a battle
can ask the choice one as step 1 and the numeric one as step 2. Step 1 is stored as
`kind='choice', choice_question_id=777, numeric_question_id=NULL`, step 2 as
`kind='numeric', numeric_question_id=777, choice_question_id=NULL`. The id never stands alone; it is
always read through the column of its own model. "Already used in this game" is also checked per model,
so using choice #777 does not block numeric #777 (`test_same_id_in_both_question_models_stays_unambiguous`).

**Rejected:** one generic link (a `question_id` plus a `question_type`, or a Django `GenericForeignKey`).
It is shorter, but the database could no longer protect the link (no real foreign key, no `PROTECT`),
and a bare `question_id = 777` would mean two different questions depending on another column.

### Consequences (value-based scoring, same numbers as M07)

| Result | Map | Points |
|---|---|---|
| `battle`, attacker wins | target's owner → attacker; `Territory.score` + 100 (never reset) | attacker: win **150** + conquest = territory value **after** the increase |
| `battle`, defender wins | nothing changes | defender: win **150** |
| `capital_attack`, either wins | the capital, its territory, owner and health stay the same | winner: win **150** |
| no winner | nothing | nothing |

The result, the awards and the target's new owner and score are saved in the same transaction that
completes the round. Every change is a `ScoreEntry` with before/after values. A database constraint
allows one win and one conquest entry per round, and re-evaluating a completed round is refused.
Before the result is applied, the target must still belong to the recorded defender. If it does not,
applying is refused (`BattleTargetChanged`, an `invalid_state`): no result, no award, no other target.
Answers accepted earlier stay saved.

### How to run it

```bash
python backend/manage.py battle <game id> <attacker>                 # what the attacker may attack
python backend/manage.py battle <game id> <attacker> <target slug>   # create the battle
python backend/manage.py numeric_round start <round id>
python backend/manage.py numeric_round answer <round id> <username> <option id or number>
```

Run on a fresh local database (map shared out, every non-capital territory worth 100):

```
$ manage.py battle 1 ana balkania
battle: ana attacks boris
  Q1 choice [pending] Коя е най-дългата река в България?  players: ana, boris
$ manage.py numeric_round answer 1 ana 1          (option 1: Искър)
$ manage.py numeric_round answer 1 cveta 1
CommandError: [invalid_state] Player #3 is not a participant of the current question.
$ manage.py numeric_round answer 1 boris 1
  Q1 choice [completed] ...  ana: Искър (correct), boris: Искър (correct)
  Q2 numeric [active] Колко метра е връх Мусала?  players: ana, boris  (10.0 s left)
$ manage.py numeric_round answer 1 ana 2900
$ manage.py numeric_round answer 1 boris 3000
  Q2 numeric [completed] ...  ana: 2900, boris: 3000 (correct 2925)
  Winner: ana
# Balkania: owner ana, score 200
# ana round_win: 0 + 150 = 150  (round win = 150)
# ana conquest: 150 + 200 = 350  (territory 100 + 100 = 200; points = territory value after = 200)
```

### Two simultaneous attempts after a tie

Evaluating a step first switches it from active to completed with a conditional
`filter(status='active').update(...)`. Only the attempt that changes the row goes on to create the next
step; the other one touches 0 rows and changes nothing. Every attempt also locks the round row, and
`(round, sequence)` is unique. `ConcurrentBattleTieTests` runs two real threads after two correct
answers and checks that the history holds exactly one numeric step 2.
