# Luxe Salon & Spa - DBMS Project 66
Stack: Python/Flask, PostgreSQL (psycopg2, plain SQL), vanilla HTML/CSS/JS.
## Run
1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env`; set `DATABASE_URL` (local Postgres or Supabase connection string; needs the btree_gist extension).
3. `python setup_db.py`  (idempotent, never drops data)   4. `python seed.py` (skips if data exists)   5. `python make_er.py`   6. `python app.py` -> http://127.0.0.1:5000
## Tests
`python tests/test_salon.py` - uses `TEST_DATABASE_URL` (default `salon_test`). **It DROPS the public schema of that database; never point it at real data.**
## Not implemented yet
Role-specific views (customer/staff/admin), Solution/normalization page, SQL Lab named-query page with SQL text, customer update/delete, service/staff CRUD endpoints, syntax highlighting, authentication (demo only - do not expose publicly), browser UI testing.
