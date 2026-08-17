# Developing

How the code is organised, why it's built this way, and how to work on it.

---

## Layout

```
bridge/                  Python, runs on each Raspberry Pi
  bridge.py                main loop: collect events, run state machine, act
  core/
    state_machine.py       pure logic — no hardware, no network, no database
    cache.py               local SQLite: access list + session outbox
    sync.py                talks to Supabase
    config.py              reads config.yaml + secrets/env
  hardware/                one module per device, each with a simulate mode
  tests/                   112 tests, no hardware required

web/                     Next.js admin app (Vercel)
  app/(app)/               authenticated pages
  app/login/               sign-in
  app/api/admins/create/   server-side admin creation
  lib/supabase/            browser + server clients
  middleware.ts            auth gate

supabase/migrations/     SQL, applied in order
```

---

## The two ideas worth understanding

### 1. Access decisions never touch the network

A machine must not become unusable because Wi-Fi dropped. So:

- Each bridge keeps a local SQLite copy of who may use **its** machine
- A badge tap is answered from that copy. Always. No network call on that path.
- Sync runs on a **background thread** and can fail freely

Consequences designed around:

- **A failed sync never clears the cache.** `replace_access()` swaps atomically
  only after a *fully successful* fetch, so a network error leaves the last
  known-good list in place rather than locking everyone out.
- **`sync_once()` cannot raise.** Every step is caught individually and collected
  into a result object.
- **Sessions are written locally first**, then pushed. Each gets a UUID generated
  on the Pi and is *upserted*, so a retry after an outage can't create duplicates.
- **Push before pull.** Locally-produced facts are the ones you can't recover.

Cost of this design: revoking access takes up to one sync interval (~30s) to
reach a bridge.

### 2. The state machine is pure

`core/state_machine.py` imports nothing but the standard library. It takes
`(state, data, event, config)` and returns `(new state, new data, [actions])`.

`bridge.py` is what talks to hardware and storage — it builds events and executes
the actions it gets back.

This is why the whole workflow — timeouts, extensions, the warning beep, the
5-hour cap — is testable on any machine with no Pi attached, and why swapping the
badge reader touched exactly one file.

---

## Running tests

```bash
cd bridge
python -m venv .venv
.venv/bin/pip install -r requirements.txt    # not requirements-pi.txt
.venv/bin/python -m pytest
```

112 tests, ~4 seconds, no hardware and no network. `requirements-pi.txt` is
Linux/GPIO-only — skip it off the Pi.

| File | Covers |
|---|---|
| `test_state_machine.py` | Every transition, timeout, extension, cap, warning |
| `test_cache.py` | Local database and the session outbox |
| `test_sync.py` | Supabase sync against a fake client, including total outage |
| `test_reader.py` | Keystroke framing and the keycode map |
| `test_bridge.py` | End-to-end through the simulated hardware |

## Running the bridge without hardware

Set every device to `simulate` in `config.yaml`:

```yaml
hardware:
  reader:  { type: "simulate" }
  display: { type: "simulate" }
  button:  { type: "simulate" }
  buzzer:  { type: "simulate" }
  relay:   { type: "simulate" }
```

Then drive it by writing files:

```bash
mkdir -p _sim
echo "press" > _sim/button_in.txt          # button press
echo "043f6afba2" > _sim/reader_in.txt     # badge tap
```

The display renders as text boxes in the terminal.

## Running the web app locally

```bash
cd web
npm install
cp .env.example .env.local     # add your Supabase URL and anon key
npm run dev
```

---

## The badge reader

The OMNIKEY runs in keyboard-wedge mode: it appears as a USB keyboard and "types"
the badge number. `hardware/reader.py` reads it through `evdev`.

Two details that matter:

**It's grabbed exclusively.** `device.grab()` means keystrokes reach only this
process. Without it, badge numbers would be typed into whatever has focus on the
Pi — including a console login prompt.

**Reads are framed by timing.** Our unit sends no Enter key after the number, so
a "read" is a burst of keystrokes followed by a gap (150 ms by default). An Enter
also ends a read if a reader is configured to send one. The framing logic is a
pure class, `KeystrokeFramer`, so it's unit-tested without evdev.

### Using a different reader

Add a class to `hardware/reader.py` with `poll()`, `drain()` and `close()`, then
register it in `make()`. Nothing else changes — the rest of the system only ever
sees an opaque ID string.

---

## Database notes

- **Row Level Security is on for every table.** Policies call `is_admin(auth.uid())`,
  backed by the `admins` table.
- **`is_admin()` is `SECURITY DEFINER`.** It reads `admins` without re-entering
  RLS, which is what avoids Postgres's "infinite recursion detected in policy"
  error. Every other `SECURITY DEFINER` function re-checks `is_admin()` internally,
  because they run with elevated rights.
- **Bridges use the `service_role` key**, which bypasses RLS entirely. Scoping
  this to a per-bridge key would reduce the damage if a Pi were stolen — it's the
  most worthwhile hardening left.
- **The web app's anon key is public by design.** RLS is the protection, not key
  secrecy.

---

## Deferred / known limitations

**Admin invites by email.** Adding an admin means handing over a starter password
in person. A proper "click this link to set your password" flow needs real email
(Supabase's built-in mailer is rate-limited and not for production). To add it:
sign up for Resend, point Supabase's SMTP settings at it, then swap
`createUser()` for `inviteUserByEmail()` in `app/api/admins/create/route.ts` and
add a set-password page.

**Per-bridge database credentials.** See above.

**Revocation latency.** Up to one sync interval. Supabase Realtime would make it
immediate if that ever mattered.

**Scheduled keepalive margin.** The GitHub job runs weekly against a 7-day
pause threshold — very little slack if GitHub delays a run, and GitHub disables
scheduled jobs after ~60 days without commits. Running it every 2–3 days is a
one-line change. Irrelevant while any bridge is powered on, since those poll
constantly.

**Buzzer volume.** Driven at ~4 kHz to match the piezo's resonance. Mounting it
against a panel matters more than anything electrical. Differential drive (a
second GPIO pin with an inverted signal) would roughly double it.

---

## Design decisions, briefly

| Decision | Why |
|---|---|
| Button arms the reader, not the reverse | Badges in pockets can't start machines; also stops stray reads doing anything |
| Only the active user can extend | Someone walking past with a badge shouldn't affect a running job |
| 10-second extension cooldown | A badge left on the reader would otherwise extend forever |
| Settings applied only when idle | Shortening the session length shouldn't move the goalposts mid-job |
| Soft-delete for members | Session history must survive removing someone |
| Client-generated session UUIDs | Makes retried pushes idempotent after an outage |
| Machine timings in the database | Tune them from the web app instead of SSHing into each Pi |
| PostgREST over plain `requests` | One less dependency on the Pi, and explicit timeouts so sync can never stall the main loop |
