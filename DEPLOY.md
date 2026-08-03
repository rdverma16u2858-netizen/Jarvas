# Deploying MathBot

Everything below assumes a single small server (a €5 VPS is ample) running
Docker, with a domain pointed at it.

**Honest caveat before you start:** the compose files in this repository have
been syntax-checked but not run end to end, because Docker was not available
on the machine they were written on. Expect to hit one or two small things on
the first `up`. The failure modes worth knowing about are listed under
[When it does not work](#when-it-does-not-work).

---

## 1. Before anything else

```bash
cp .env.example .env
```

Then fill in, in `.env`:

| Variable | Why it matters |
|---|---|
| `GEMINI_API_KEY` | Without it the API boots fine and 503s on the first solve |
| `POSTGRES_PASSWORD` | Production compose refuses to start without it |
| `CORS_ORIGINS` | Your site's origin. Wrong here = every request fails with an opaque CORS error |
| `NEXT_PUBLIC_API_URL` | Inlined at **build** time — changing it later needs a rebuild, not a restart |

`.env` is gitignored and must stay that way. `.env.example` is committed and
must never contain a real key.

---

## 2. Bring it up

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Needs Docker Compose **v2.24 or newer** — the production file uses `!override`
to *replace* the development port bindings and bind mounts rather than append
to them. On an older Compose it fails to parse, which is better than silently
keeping the dev settings.

Migrations run automatically before the server starts. `alembic upgrade head`
is idempotent, so this is safe on every restart.

---

## 3. Put a proxy in front

The containers bind to `127.0.0.1` on purpose: they should not be reachable
from the internet directly. Terminate TLS at a proxy. Caddy is the least
work — this whole file is enough:

```
mathbot.example.com {
    handle /api/* {
        reverse_proxy localhost:8000
    }
    handle {
        reverse_proxy localhost:3000
    }
}
```

Then set `TRUST_PROXY_HEADER=true`. Without it every request appears to come
from the proxy, so all clients share one rate-limit bucket. **With** it and no
proxy actually in front, anyone can spoof `X-Forwarded-For` and the limiter
does nothing while still appearing to work. It is a deployment fact, not a
preference — set it to match reality.

---

## 4. Check it is actually working

```bash
curl -s https://mathbot.example.com/api/v1/health
```

Look for `"status": "healthy"` and, under `components`, `database: up` and
`cache: up` with detail `redis`. If the cache says `memory`, Redis never
connected — the app still runs, but nothing is shared between workers and the
rate limiter counts per-process.

Then confirm production mode really is on:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://mathbot.example.com/api/v1/docs
```

Must be **404**. `ENV=production` closes `/docs`, `/redoc` and
`/openapi.json`, and stops tracebacks appearing in error responses. A 200 here
means `ENV` did not take effect and your API surface — and your stack traces —
are public.

---

## Production checklist

- [ ] `.env` filled in; `.env` is not committed
- [ ] `ENV=production` — verified by `/api/v1/docs` returning 404
- [ ] `CORS_ORIGINS` is your real site origin, not `localhost:3000`
- [ ] TLS terminating at a proxy; containers on `127.0.0.1` only
- [ ] `TRUST_PROXY_HEADER` matches whether a proxy is actually in front
- [ ] `/api/v1/health` reports `cache: redis`, not `memory`
- [ ] Postgres volume (`pgdata`) is included in whatever backs the host up
- [ ] Rate limits reviewed against your own Gemini quota

---

## Operating it

**Logs**

```bash
docker compose logs -f api
```

**A migration you added**

```bash
docker compose exec api alembic upgrade head
```

**Back up the database**

```bash
docker compose exec -T db pg_dump -U mathbot mathbot > backup-$(date +%F).sql
```

**Rate limits.** The defaults (12/minute, 400/day for model calls) sit under
Gemini's free tier deliberately, so the app's own 429 arrives before the
provider's. If you are on a paid plan, raise `RATE_LIMIT_LLM_PER_DAY`. If you
are sharing the deployment with other people, lower it — the daily window is
the one that actually protects the budget.

---

## When it does not work

**Every request from the site fails, but `curl` works.**
`CORS_ORIGINS` does not match the browser's origin. It must be the site the
user is on, not the API's own address, and the scheme and any port must match
exactly.

**The UI cannot reach the API at all.**
`NEXT_PUBLIC_API_URL` is baked in at build time. If you changed it, rebuild:
`docker compose ... up -d --build web`.

**`relation "conversations" does not exist`.**
Migrations did not run. `docker compose exec api alembic upgrade head`, then
check the api container's start command — it should run migrations first.

**`/docs` returns 200 in production.**
`ENV` is not set to `production` in the running container. Confirm with
`docker compose exec api printenv ENV`.

**Rate limiting seems to do nothing behind a proxy.**
`TRUST_PROXY_HEADER=false` means every client shares the proxy's address — but
the symptom of *that* is over-limiting, not under. Under-limiting behind a
proxy usually means the header is set and being spoofed, or the proxy is not
setting `X-Forwarded-For` at all.

**Everything is slow and nothing is cached.**
`/api/v1/health` will say `cache: memory`. `REDIS_URL` is wrong or Redis is
not reachable; the app degrades rather than failing, which is why this is easy
to miss.
