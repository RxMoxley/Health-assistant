# VitaAssist — AI-Driven Personalized Health Assistant

Personalized health assistant built with Flask + MySQL + Google Gemini.
Every response is contextualized by the user's stored health profile
and conversation history.

## Local / Cloud setup
1. Copy `.env.example` to `.env` and paste your Gemini API key.
2. `pip install -r requirements.txt`
3. Start MySQL/MariaDB and run `schema.sql`.
4. `python app.py` → open http://localhost:5000

## Roles
- Register a normal user via /register
- To promote yourself to admin, run in the DB:
  `UPDATE users SET role='admin' WHERE email='you@example.com';`
