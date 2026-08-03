# MathBot

An AI mathematics tutor that solves advanced problems, explains every step,
offers more than one method, and **verifies its own answers symbolically
before showing them to you**.

That last point is the design centre of the project. LLMs are unreliable at
arithmetic and symbolic manipulation; a tutor that confidently produces a wrong
integral is worse than no tutor, because the student memorises the error. So
every computational answer is checked by SymPy independently of the model —
differentiate the result and compare, substitute roots back into the equation,
evaluate both sides numerically — and anything that fails is regenerated or
flagged rather than shown.

**Status: Phase 0 complete.** Foundation only — no maths yet. See the roadmap.

---

## Running it

You do **not** need Docker. The default configuration uses SQLite and an
in-process cache, so it runs with nothing installed but Python and Node.

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs
- Health: http://localhost:8000/api/v1/health

### Frontend

```bash
cd frontend
npm install
npm run dev
```

- http://localhost:3000 — shows a live system check against the backend

### Tests

```bash
cd backend
pytest
```

### With Docker (Postgres + Redis)

Optional. Use it when you want to develop against the same database the
deployment uses.

```bash
docker compose up -d
```

Postgres is on host port **5433** and Redis on **6380** so they cannot collide
with anything already installed.

---

## Project layout

```
mathbot/
├── docker-compose.yml       Postgres + Redis + API + web, for full-stack local dev
├── .env.example             Configuration template — copy to .env
├── .gitignore               Excludes secrets, build output, local databases
├── .github/workflows/ci.yml Runs tests on SQLite AND Postgres on every push
│
├── backend/
│   ├── Dockerfile           Multi-stage build; runs as a non-root user
│   ├── requirements.txt     Pinned dependencies (ASCII only — pip on Windows)
│   ├── pyproject.toml       pytest + ruff configuration
│   ├── app/
│   │   ├── main.py          Builds the FastAPI app; startup/shutdown lifecycle
│   │   ├── core/
│   │   │   ├── config.py    Every setting, validated once at startup
│   │   │   └── logging.py   Readable logs locally, JSON in production
│   │   ├── db/
│   │   │   ├── base.py      Declarative base + timestamp mixin
│   │   │   └── session.py   Async engine and the per-request session
│   │   ├── cache/
│   │   │   └── client.py    Redis when configured, in-memory when not
│   │   ├── api/
│   │   │   ├── router.py    One place where every route is registered
│   │   │   └── routes/
│   │   │       └── health.py  Full report, liveness, readiness
│   │   └── schemas/
│   │       └── health.py    Response models — these generate the OpenAPI docs
│   └── tests/
│       ├── conftest.py      Isolated test database + in-process HTTP client
│       └── test_health.py   Proves the whole foundation is wired together
│
└── frontend/
    ├── Dockerfile           Three-stage build; ships no source or dev deps
    ├── next.config.ts       Deliberately minimal
    ├── tsconfig.json        strict: true, and the @/* path alias
    ├── postcss.config.mjs   Tailwind v4 plugin
    └── src/
        ├── app/
        │   ├── layout.tsx   Root layout — wraps every page
        │   ├── page.tsx     System check page
        │   └── globals.css  Design tokens (Tailwind v4 uses CSS, not JS config)
        └── lib/
            └── api.ts       The only place the frontend calls the backend
```

---

## Design decisions worth knowing

**SQLite locally, Postgres in production, one set of models.** The database is
chosen entirely by `DATABASE_URL`. SQLAlchemy speaks both, so nothing else in
the codebase knows which is running. CI runs the suite against both, because
SQLite is forgiving in ways Postgres is not.

**Redis is optional.** An in-memory fallback means you are never blocked on
having Redis running. It reports itself as `degraded` rather than `up` in the
health check — it is per-process and empties on restart, which is a real
limitation that should be visible rather than hidden behind a green tick.

**Liveness and readiness are separate endpoints.** `/health/live` checks
nothing, so a database blip cannot trigger a container restart loop.
`/health/ready` does check, so a load balancer can pull the instance out of
rotation and put it back when the database recovers.

**Async throughout.** A maths request spends most of its time waiting — on the
model, on the database, on SymPy. Async lets one worker serve other requests
during that wait.

**Configuration is validated at startup.** A missing or malformed setting stops
the app immediately with a clear message, rather than failing on the first
request that happens to need it. `DATABASE_URL` is checked for an async driver
specifically, because a sync URL makes the app hang silently — one of the least
obvious failures in async Python.

---

## Roadmap

| Phase | Feature | Status |
|-------|---------|--------|
| 0 | Foundation: config, database, cache, health, CI, Docker | **done** |
| 1 | LLM provider layer — Claude, swappable by config | next |
| 2 | Solver core + SymPy verification + 10-part solution schema | |
| 3 | LaTeX rendering + streaming chat UI | |
| 4 | Conversation memory, history, search, bookmarks | |
| 5 | Question generation (16 topics x 7 levels x 6 types) | |
| 6 | Mistake detection in a student's own solution | |
| 7 | Quizzes and mock tests | |
| 8 | Progress tracking + adaptive difficulty | |
| 9 | OCR image upload | |
| 10 | PDF export, dark mode, polish | |
| 11 | Auth, rate limiting, deployment | |

Topics to cover: algebra, linear algebra, calculus, multivariable calculus,
differential equations, integral calculus, vector calculus, probability,
statistics, discrete mathematics, number theory, combinatorics, graph theory,
real analysis, complex analysis, abstract algebra.

---

## Configuration

Every setting lives in `.env` (copy from `.env.example`). Nothing reads
`os.environ` directly — it all goes through `app/core/config.py`, so there is
one place to look and one place to change.

The model is `claude-opus-5`, set in config rather than hardcoded, so Phase 1's
provider abstraction can swap it without touching application code.
