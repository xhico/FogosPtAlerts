# FogosPT Alerts

Watches the [fogos.pt](https://fogos.pt) occurrence feed (ANEPC civil-protection data) and emails you when a wildfire starts, changes materially, or ends near the places you care about.

Runs as a Docker service, published to GitHub Container Registry.

---

## How it works

Every cycle it fetches the live feed, keeps only occurrences inside your geofence, and compares them against the last snapshot on disk:

| Event | When |
| --- | --- |
| **Novo incêndio** | An occurrence appears in your area for the first time |
| **Atualização** | Its status changes, or its resources move meaningfully |
| **Terminado** | It leaves the feed |

"Meaningfully" is the important word. Resource counts jitter constantly — 20 operacionais becomes 21 and back again — and alerting on every delta makes the mailbox useless. An update is only sent when:

- the **status** changes (*Em Curso* → *Em Resolução* → *Conclusão* → *Vigilância*), or
- a resource type **crosses zero** — aircraft arriving or leaving is always news, or
- a resource count moves by **at least 25% and at least 5 units**, or
- the fire crosses a **severity band**.

### Severity

| Band | Meaning |
| --- | --- |
| `info` | Everything else inside the geofence |
| `elevated` | 20+ operacionais, or any aircraft |
| `major` | 50+ operacionais, or 2+ aircraft |

Occurrences already winding down (*Em Resolução* onwards) never rate above `info`.

`FOGOS_MIN_SEVERITY` gates **new** fires only. Once a fire has been reported, its updates and resolution are always delivered — going quiet halfway through an incident is worse than never having started.

### Polling is deliberately irregular

`FOGOS_POLL_MINUTES` is a floor, not a period. Every sleep carries a random buffer of up to +25% on top, so the service never settles into a fixed beat against a third-party API that owes us nothing — and repeated failures back the interval off up to four cycles, jittered the same way. The jitter is only ever added, so the configured interval is never undershot.

### Silence is never ambiguous

A monitoring tool whose failure mode is silence is indistinguishable from one that has nothing to report. Four things guard against that:

- a **heartbeat email** every `FOGOS_HEARTBEAT_HOURS` summarising what is being watched,
- an **SMTP check at startup** — the service refuses to start on a broken mail config rather than failing silently later,
- a **Docker healthcheck** that goes unhealthy if the state file stops being refreshed,
- **upstream outage alerts**, described below.

### Upstream outages are reported, once

If the fogos.pt API stops answering, no fires are detected — which looks exactly like a quiet day. So the service tracks upstream health separately from fire activity:

- after **3 consecutive failures** (roughly 7 minutes once backoff is applied) it sends **one** ⚠️ email and then stays quiet, however long the outage lasts,
- when the API answers again it sends **one** ✅ email with the total downtime,
- a shorter blip sends nothing at all — no outage email means no recovery email either,
- the **heartbeat still fires during an outage**, reworded to say the service is alive but out of contact, and listing the last known state.

Outage state is persisted, so restarting mid-outage does not restart the notification sequence. The Docker healthcheck deliberately stays green throughout: the container is healthy, the upstream is not, and restarting the container would not fix it.

### State survives restarts

The snapshot lives in `/data` on a named volume. If it were inside the container, every restart would re-alert every active fire. On the very first run the service adopts what is already burning and sends a single "monitorização iniciada" summary instead of one email per fire.

---

## Quick start

Deploy the stack and set the environment variables in your Docker UI. [docker-compose.yaml](docker-compose.yaml) declares every variable it understands in the form `${VAR:-default}`, so the compose file doubles as the reference: anything you set in the UI wins, anything you leave alone falls back to the default shown. [env.example](env.example) is the same list annotated, ready to copy into a `.env`.

`EMAIL_TO` and `SMTP_HOST` have no default — leave them unset and the service exits at startup rather than pretending to work.

On startup the service logs its full resolved configuration:

```
FogosPT Alerts v2.0.0
  Raio            : 15 km de (38.7223, -9.1393)
  Localidades     : Sintra, Mafra
  Intervalo       : 5 min
  SMTP            : smtp.example.com:587 (STARTTLS)
  Para            : you@example.com
```

Check that block first after any deploy — if a variable did not reach the container, it shows up here immediately rather than as mysteriously missing alerts later.

Do a dry run before pointing it at real mail: set `FOGOS_DRY_RUN=true` and the emails are rendered and logged instead of sent.

---

## Configuration

All configuration is environment variables. See [env.example](env.example) for a commented template.

### Where to watch

| Variable | Default | Description |
| --- | --- | --- |
| `FOGOS_MAX_DISTANCE_KM` | `0` | Radius around the centre point. `0` disables radius matching |
| `FOGOS_CENTER_LAT` | `0` | Centre latitude |
| `FOGOS_CENTER_LON` | `0` | Centre longitude |
| `FOGOS_LOCATIONS` | — | Comma-separated places always alerted on, regardless of distance. Accent- and case-insensitive; matched against district, concelho, freguesia and locality |

At least one of `FOGOS_MAX_DISTANCE_KM` or `FOGOS_LOCATIONS` must be set, or startup fails.

### How loud

| Variable | Default | Description |
| --- | --- | --- |
| `FOGOS_POLL_MINUTES` | `1` | Minimum minutes between polls. A random buffer of up to +25% is added to every sleep, so `1` polls every 60–75s rather than on a fixed beat |
| `FOGOS_MIN_SEVERITY` | `info` | `info` \| `elevated` \| `major` — threshold for new fires |
| `FOGOS_HEARTBEAT_HOURS` | `24` | Hours between summary emails; `0` disables |

### Email

| Variable | Default | Description |
| --- | --- | --- |
| `EMAIL_TO` | **required** | Comma-separated recipients |
| `EMAIL_FROM` | `SMTP_USERNAME` | From address; `Name <addr@host>` is fine |
| `SMTP_HOST` | **required** | SMTP server |
| `SMTP_PORT` | `587`, or `465` with SSL | |
| `SMTP_USERNAME` | — | Omit for an unauthenticated relay |
| `SMTP_PASSWORD` | — | For Gmail, an [app password](https://support.google.com/accounts/answer/185833) |
| `SMTP_STARTTLS` | `true` unless SSL | |
| `SMTP_SSL` | `false` | Implicit TLS (port 465) |
| `SMTP_TIMEOUT` | `30` | Seconds |

### Runtime

| Variable | Default | Description |
| --- | --- | --- |
| `FOGOS_STATE_DIR` | `/data` | Must be a writable volume |
| `FOGOS_API_URL` | `https://api-dev.fogos.pt/new/fires` | Override if upstream moves |
| `FOGOS_API_KEY` | — | Access key, sent as the `X-API-Key` header. Empty calls the API anonymously, which is now rate-limited |
| `FOGOS_USER_AGENT` | `FogosPtAlerts/2.0 (+…)` | Must match the User-Agent declared on your API access request |
| `LOG_LEVEL` | `INFO` | |
| `FOGOS_DRY_RUN` | `false` | Render and log emails without sending |

---

## Email design

Subjects are front-loaded, because a phone notification cuts off around 40 characters and the whole point is triage without opening:

```
🔴 NOVO INCÊNDIO · Óbidos · A dos Negros · 80 op, 14 vt, 3 aéreos
🔺 Vouzela · Meios aéreos no local (2)
🔻 Bombarral · Em Resolução
✅ Terminado · Óbidos · Serra d'El-Rei · durou 4h12
```

`🔺`/`🔻` say at a glance whether things got worse or better. Update subjects name *what changed* rather than repeating the location twice.

Bodies are table-based with inline styles — the only layout that survives Gmail, Outlook and Apple Mail intact. Each carries a severity-coloured header, a "what changed" old → new block, a resource grid, full details, and links to fogos.pt and the map. A plain-text alternative is always included.

Updates to the same fire thread together via `In-Reply-To`/`References`, so one incident is one conversation in your mailbox.

---

## Running locally

```bash
pip install -r requirements.txt
cp env.example .env   # then edit it; set FOGOS_STATE_DIR=./data
python3 FogosPtAlerts.py
```

If `python-dotenv` is installed, `.env` is loaded automatically (falling back to `stack.env`); otherwise export the variables yourself. Real environment variables always take precedence over the file, and neither file is required — `python-dotenv` is a convenience, not a dependency.

---

## Container image

Built and pushed to GHCR by [`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml) on every push to `main` and every `v*.*.*` tag, for `linux/amd64` and `linux/arm64`.

```
ghcr.io/xhico/fogosptalerts:latest
ghcr.io/xhico/fogosptalerts:1.2.3
```

The image runs as an unprivileged user; `/data` is the only writable path.

---

## Layout

```
FogosPtAlerts.py   entry point: poll loop, signals, backoff
config.py          env parsing and validation
fogos.py           API client, Fire model, geofencing, severity
geo.py             haversine, bearing, accent-insensitive matching
changes.py         meaningful-change detection
state.py           atomic persisted snapshot
render.py          subject lines and email bodies
mailer.py          SMTP with per-fire threading
```

Nothing here is fire-specific below `fogos.py` — the *poll → geofence → diff → notify* shape works for any public feed.

---

## Caveats

- Upstream is `api-dev.fogos.pt`, a development host. It can change without notice; `FOGOS_API_URL` exists for that day.
- Anonymous requests are rate-limited (HTTP 429). Request a key at [fogos.pt](https://fogos.pt) and set `FOGOS_API_KEY`; a rejected key returns 403 rather than 429.
- Alerts are **indicative**. In an emergency call **112**.

## License

MIT
