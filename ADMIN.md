# Running it day to day

Everything here happens in the web app unless it says otherwise.

---

## Give someone access to a machine

Two steps: enroll their badge once, then grant them each machine they need.

### Enroll a badge

1. **Enroll** → pick the machine whose reader they'll tap
2. Type their name → **Start enrollment**
3. That machine's screen changes to **ENROLL / their name**
4. They tap their badge. Three rising beeps, and the browser confirms

You have 60 seconds. If it runs out, just start again.

> They only need enrolling **once**, on any machine. The badge is then known
> everywhere.

### Grant access

**Members** → find them → click **Grant** under each machine they're allowed on.

Bridges pick up the change within 30 seconds.

### Take access away

Same place, click **Revoke**. They can no longer start that machine, but their
past sessions stay in the record.

---

## Remove someone entirely

**Members → Deactivate.** They can't start anything, on any machine, but their
session history is kept.

If they come back, either **Reactivate** them, or just enroll the same badge
again — that brings the account back with the name you type.

---

## Change how long sessions last

**Machines → Edit timings.** Each machine is set separately.

| Setting | Default | What it does |
|---|---|---|
| Session length | 15 min | Time granted per badge tap |
| Extension per re-tap | 15 min | Added when the user taps again mid-session |
| Max total session | 5 hours | Ceiling including all extensions |
| Warning buzz at | 60 sec | When the three warning beeps sound |
| Tap window | 30 sec | How long the reader waits after the button |
| Enrollment window | 60 sec | How long an enrollment stays open |

Changes reach the bridge within 30 seconds, but are **only applied between
sessions** — nobody's countdown ever shortens under them mid-job.

---

## Watch what's happening

**Dashboard** shows every machine live, refreshing every few seconds: idle,
running (and who), or enrolling. It also shows whether each bridge is talking to
the cloud.

**"Bridge offline"** means that Pi hasn't checked in for 90 seconds — powered
off, or off the network. **The machine keeps working** from its stored copy of
the member list; you just can't see or change it remotely until it's back.

---

## Review usage

**Sessions** lists every session, newest first, filterable by machine, with
**Export CSV** for spreadsheets.

- **Extended** shows how much extra time was taken
- **auto-stop** means it hit the time limit rather than the user pressing stop

---

## Manage administrators

**Admins.**

- **Add an admin** — type their email. If they have no account, one is created
  with the starter password you set. Hand that password over directly; it isn't
  emailed. They can change it under **Your account**.
- **Revoke** — takes away access; the account remains but can see nothing
- **Your account** — change your own password

You can't remove the last administrator; the database refuses, so the app can't
lock everyone out.

### If you see an orange "accounts without admin access" warning

Every account is supposed to be an administrator. That warning means one exists
that isn't — usually created directly in Supabase, or signed up before sign-ups
were disabled. **Grant** or **Delete** each one to clear it.

---

## Update the software

On the Pi:

```bash
cd ~/fabman
git pull
sudo systemctl restart fabman-bridge
```

If the update changed dependencies:

```bash
cd ~/fabman/bridge
.venv/bin/pip install -r requirements.txt -r requirements-pi.txt
sudo systemctl restart fabman-bridge
```

The web app redeploys itself whenever you push to GitHub.

---

## Useful commands on a bridge

```bash
# Is it running?
systemctl status fabman-bridge

# Watch it live
journalctl -u fabman-bridge -f

# Recent history
journalctl -u fabman-bridge -n 100 --no-pager

# Restart / stop / start
sudo systemctl restart fabman-bridge
sudo systemctl stop fabman-bridge
sudo systemctl start fabman-bridge
```

Normal running looks like a `Sync ok` line every 30 seconds, and session
start/stop lines as people use the machine.

---

## Common problems

| Symptom | Cause | Fix |
|---|---|---|
| Badge does nothing | The button arms the reader — press it first | Press the button, *then* tap |
| "DENIED" for someone who should have access | Not granted on this machine, or deactivated | Check **Members** |
| Newly granted person still denied | Bridge hasn't synced yet | Wait 30 seconds |
| Machine won't start, screen blank | Bridge not running | `systemctl status fabman-bridge` |
| Dashboard shows offline but the machine works | Network or power to the Pi | Expected behaviour — it runs from its local copy |
| Re-tap doesn't extend the session | Under the 10-second cooldown, or at the 5-hour cap | Wait 10 seconds; one flat beep means the cap |
| Enrollment ends instantly | Stray badge read | Just start the enrollment again |
| Nobody can log in to the web app | Supabase project paused | Restore it in the Supabase dashboard |

---

## What the beeps mean

| Sound | Meaning |
|---|---|
| Two rising beeps | Session started |
| Two falling beeps | Session stopped |
| One short beep | Extra time granted |
| Two low beeps | Denied |
| **Three beeps** | **One minute left** |
| Three low beeps | Time's up, stopped automatically |
| Three rising beeps | Badge enrolled |
| One flat low beep | At the 5-hour limit, no more time |

Too quiet? The buzzer's volume depends heavily on how it's mounted — fixing the
disc flat against a panel is far louder than leaving it dangling. You can also
find the loudest pitch for your particular unit:

```bash
sudo systemctl stop fabman-bridge
cd ~/fabman/bridge
.venv/bin/python -m hardware.buzzer sweep
sudo systemctl start fabman-bridge
```

Note the loudest frequency, then adjust the constants at the top of
`bridge/hardware/buzzer.py`.
