# Triviador

A multiplayer quiz-territory game: Django + Django REST Framework backend, React + Vite frontend.

## Project layout

```text
backend/    Django project (config/, accounts/, questions/, games/)
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
python backend/manage.py test accounts questions games
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
