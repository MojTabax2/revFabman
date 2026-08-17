# Build one from scratch

Everything from an empty SD card to a working machine lock. No prior Raspberry Pi
or web development experience assumed.

**Time:** about 3 hours, most of it waiting on downloads.
**Cost:** ~$175 in parts per machine, plus free-tier cloud accounts.

Work through the parts in order. The cloud comes before the hardware because the
Raspberry Pi needs cloud credentials before it can do anything.

| Part | What | Where |
|---|---|---|
| [0](#part-0--check-your-badges) | Check your badges will work | 5 min |
| [1](#part-1--the-database-supabase) | Database (Supabase) | browser |
| [2](#part-2--the-web-app-vercel) | Web app (Vercel) | browser |
| [3](#part-3--wire-the-hardware) | Wire the hardware | bench |
| [4](#part-4--set-up-the-raspberry-pi) | Set up the Raspberry Pi | terminal |
| [5](#part-5--configure-the-badge-reader) | Configure the badge reader | Windows PC |
| [6](#part-6--test-every-part) | Test every part | terminal |
| [7](#part-7--go-live) | Go live | both |

---

## Part 0 — Check your badges

**Do this before buying anything else.** This system reads **125 kHz HID Prox**
badges. If your building uses something else, the reader won't work.

Easiest check: ask whoever manages building access what badge format you use.
Answers that work: *HID Prox*, *HID Proximity*, *Corporate 1000*, *26-bit* or
*35-bit prox*.

Answers that **won't** work with this reader: *MIFARE*, *DESFire*, *iCLASS*,
*NFC*, *13.56 MHz*.

If you have a [Flipper Zero](https://flipperzero.one/), read a badge with it —
it names the format directly.

Got an incompatible badge type? The rest of this design still works; you'd swap
the reader for one that matches, and change one file
(`bridge/hardware/reader.py`).

---

## Part 1 — The database (Supabase)

Supabase gives you a Postgres database and user logins for free. It's where
members, permissions, and session history live.

### 1.1 Create the project

1. Go to [supabase.com](https://supabase.com) → sign up → **New project**
2. **Name:** `fabman` · **Region:** whichever is closest to you
3. Set a database password and **save it somewhere** — you won't need it often,
   but losing it is a nuisance
4. Wait ~2 minutes while it provisions

### 1.2 Copy three values

**Project Settings** (gear icon) → **API**. Write these down:

| Value | Used by | Keep secret? |
|---|---|---|
| **Project URL** | web app + Pi | no |
| **anon public** key | web app | no — it's public by design |
| **service_role** key | the Pi only | 🔒 **yes** — it bypasses all security |

Newer dashboards may call these *Publishable* and *Secret*. Same things.

### 1.3 Create the tables

Left sidebar → **SQL Editor** → **New query**.

Run these three files **in order**, one at a time. Open the file from this repo,
copy all of it, paste, click **Run**, wait for "Success", then clear the editor
and do the next.

1. [`supabase/migrations/0001_init.sql`](supabase/migrations/0001_init.sql)
2. [`supabase/migrations/0002_admins.sql`](supabase/migrations/0002_admins.sql)
3. [`supabase/migrations/0003_keepalive_and_orphans.sql`](supabase/migrations/0003_keepalive_and_orphans.sql)

Check it worked:

```sql
select table_name from information_schema.tables
where table_schema = 'public' order by table_name;
```

You should get 7 tables: `admins`, `bridges`, `keepalive`, `machines`,
`members`, `pending_enrollments`, `sessions`.

### 1.4 Turn off public sign-ups

**Authentication → Providers → Email.** Leave Email enabled, but switch **off
"Allow new users to sign up"**, then save.

This matters. It's on by default, and without it anyone who finds your web app's
address could create an account.

### 1.5 Create your own login

**Authentication → Users → Add user → Create new user.**

- Your email and a password
- ✅ **Tick "Auto Confirm User"** — otherwise you'll wait on a confirmation email
  that may never arrive

### 1.6 Make yourself an administrator

Having an account isn't enough — you also have to be on the admin list. Back in
**SQL Editor**, with your own email substituted in:

```sql
insert into admins (user_id, email, note)
select id, email, 'first admin'
from auth.users
where lower(email) = lower('you@example.com');
```

Confirm it returned one row:

```sql
select email, note from admins;
```

> **Don't skip this.** Until this row exists the web app shows "no access" to
> everyone, including you. It has to be done here rather than in the app because
> there's no admin yet to grant it.

### 1.7 Stop the project going to sleep

Free Supabase projects pause after **7 days with no database activity**. Once
bridges are running they poll every 30 seconds and it never sleeps — this covers
the case where every bridge is switched off for a week or more.

In your GitHub repo → **Settings → Secrets and variables → Actions → New
repository secret**, add two:

| Name | Value |
|---|---|
| `SUPABASE_URL` | your Project URL |
| `SUPABASE_ANON_KEY` | your anon key |

The scheduled job in
[`.github/workflows/supabase-keepalive.yml`](.github/workflows/supabase-keepalive.yml)
then pokes the database on a schedule. Test it now: **Actions** tab → **Supabase
keepalive** → **Run workflow**. You want a green tick and `HTTP 200`.

> GitHub disables scheduled jobs after ~60 days with no commits to the repo. If
> the shop goes quiet for months, check this job is still enabled.

---

## Part 2 — The web app (Vercel)

### 2.1 Deploy

1. [vercel.com](https://vercel.com) → sign in **with GitHub**
2. **Add New → Project** → import this repository
3. Add three **Environment Variables**:

| Name | Value |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | your Project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | your anon key |
| `NEXT_PUBLIC_TIMEZONE` | e.g. `America/New_York` |

4. **Deploy.** Takes about two minutes.

> If you're deploying from a repo where this project sits in a subfolder, set
> **Root Directory** to that folder. In this standalone repo, leave it alone.

### 2.2 Optional: let the app create admin accounts

Add a fourth variable, `SUPABASE_SERVICE_ROLE_KEY`, set to your service_role key.

This lets you add administrators entirely from the web app. Without it you can
still grant admin rights, but new accounts have to be created in the Supabase
dashboard first.

The trade-off: that key bypasses database security. It's only ever used
server-side and never sent to a browser, and the code checks you're already an
admin before using it — but it's a real key in one more place. Your call.

### 2.3 Sign in

Open your new `something.vercel.app` address. You should be sent to a login page.
Sign in with the account from step 1.5.

If you land on the dashboard, everything above worked. If you see **"no
access"**, step 1.6 didn't take — go back and run it.

### 2.4 Add your first machine

**Machines → Add a machine.**

- **Slug:** `laser-1` — lowercase, no spaces. **Write this down exactly**; the Pi
  needs it character-for-character.
- **Display name:** `Laser Cutter 1` — what shows on the little screen.

---

## Part 3 — Wire the hardware

**Unplug the Pi before wiring anything.**

### Finding pin 1

Hold the Pi with the two rows of pins along the top edge. **Pin 1 is the corner
closest to the microSD card slot and the power socket.** Odd numbers run along
the outer row, even numbers along the inner row.

```
                  RASPBERRY PI 3B+  —  40-PIN HEADER
        (pin 1 = corner nearest the microSD slot & power port)

                       ┌────┬────┐
   ▓ QWIIC  3.3V ──────┤  1 │  2 ├────── 5V
   ▓ QWIIC  SDA  GPIO2 ┤  3 │  4 ├────── 5V
   ▓ QWIIC  SCL  GPIO3 ┤  5 │  6 ├────── GND      ▓ QWIIC
                 GPIO4 ┤  7 │  8 ├── GPIO14
                   GND ┤  9 │ 10 ├── GPIO15
                GPIO17 ┤ 11 │ 12 ├── GPIO18
                GPIO27 ┤ 13 │ 14 ├────── GND      ▒ BUTTON  C1
                GPIO22 ┤ 15 │ 16 ├── GPIO23
                  3.3V ┤ 17 │ 18 ├── GPIO24       ▒ BUTTON  NO1
                GPIO10 ┤ 19 │ 20 ├────── GND
                 GPIO9 ┤ 21 │ 22 ├── GPIO25
                GPIO11 ┤ 23 │ 24 ├── GPIO8
                   GND ┤ 25 │ 26 ├── GPIO7
                 GPIO0 ┤ 27 │ 28 ├── GPIO1
                 GPIO5 ┤ 29 │ 30 ├────── GND      ▒ BUTTON  LED −
                 GPIO6 ┤ 31 │ 32 ├── GPIO12
  ░ BUZZER +   GPIO13 ─┤ 33 │ 34 ├────── GND      ░ BUZZER  −
                GPIO19 ┤ 35 │ 36 ├── GPIO16       ▒ BUTTON  LED +
  █ RELAY  +   GPIO26 ─┤ 37 │ 38 ├── GPIO20
                   GND ┤ 39 │ 40 ├── GPIO21       █ RELAY   −
                       └────┴────┘

   ▓ Qwiic SHIM → OLED     ▒ Button      ░ Buzzer      █ Relay
```

### The 8 connections

| From | To pin | |
|---|---|---|
| Button `NO1` | **18** | the switch |
| Button `C1` | **14** | ground |
| Button `LED +` | **36** | light ring |
| Button `LED −` | **30** | ground |
| Buzzer (either lead) | **33** | |
| Buzzer (other lead) | **34** | ground |
| Relay `+` | **37** | low-voltage control side |
| Relay `−` | **39** | ground |

Plus the **Qwiic SHIM** across pins 1–6, a Qwiic cable from it to the display,
and the **badge reader into any USB port**.

### Four things that catch people out

**Use `NO1` on the button, not `NC1`.** The button has both. `NC1` is connected
at rest, which the software reads as "permanently held down" — it will never
notice a press. Not sure which is which? A multimeter on continuity: the correct
pair beeps *only while you hold the button in*.

**Press the Qwiic SHIM down hard.** It grips by friction with no clip. One that
looks seated but isn't is the single most common problem, and it shows up as a
display that never turns on. The Qwiic socket should hang off the edge of the
board, not point inwards.

**Button LED ground goes to pin 30, not 34.** Pin 34 is closer, but the buzzer is
already using it.

**Relay polarity matters, buzzer polarity doesn't.** The piezo works either way
round. The relay's `+` and `−` do not.

### About the relay

> ⚠️ **This switches mains electricity.** Never open the enclosure or modify the
> power cord. You only ever connect to the small screw terminals on the side.

Its four sockets are one switching circuit, not four independent ones:

| Socket | What to plug in |
|---|---|
| Always-on | The Raspberry Pi's own power supply |
| Normally-on | Nothing, or an "unlocked" indicator light |
| Normally-off ×2 | **The machine.** Both turn on together. |

**For your first test, plug in a desk lamp instead of the machine.** Much less
exciting if something's wired wrong.

---

## Part 4 — Set up the Raspberry Pi

### 4.1 Write the SD card

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. **Choose OS** → Raspberry Pi OS (64-bit) · **Choose Storage** → your card
3. Click the **gear / Edit Settings** button and fill in *before* writing:
   - **Hostname:** `fabman-laser-1`
   - ✅ **Enable SSH** → use password authentication
   - **Username and password** — this guide assumes `fabman`
   - **Wi-Fi** network name, password, and country
   - Your timezone
4. **Write**, then put the card in the Pi and power it on

Wait a minute or two, then from your own computer open a terminal:

```bash
ssh fabman@fabman-laser-1.local
```

If that name isn't found, look up the Pi's IP address in your router's list of
connected devices and use `ssh fabman@192.168.1.x` instead.

### 4.2 Turn on I²C and install software

I²C is the connection the little display uses. It's off by default.

```bash
sudo raspi-config nonint do_i2c 0
sudo reboot
```

Wait ~40 seconds, reconnect, then:

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip python3-dev \
                    i2c-tools swig liblgpio-dev fonts-dejavu
```

*(`swig`, `python3-dev` and `liblgpio-dev` build the library that reads the
button. `fonts-dejavu` makes the display readable rather than tiny.)*

### 4.3 Check the hardware is detected

```bash
i2cdetect -y 1
lsusb | grep -i omnikey
```

- `i2cdetect` should show **`3c`** in the grid — that's the display.
  All dashes means the Qwiic SHIM isn't pressed down firmly enough.
- `lsusb` should list the OMNIKEY reader.

### 4.4 Download and install

```bash
cd ~
git clone https://github.com/YOUR-USERNAME/YOUR-REPO.git fabman
cd ~/fabman/bridge

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt -r requirements-pi.txt
```

The install takes 3–5 minutes.

Check your user has hardware permissions:

```bash
groups
```

You need `gpio`, `i2c`, and `input` in that list. If any are missing:

```bash
sudo usermod -aG gpio,i2c,input $USER
exit
```

...then log back in, because group changes only apply to a fresh login.

### 4.5 Add your cloud credentials

```bash
cd ~/fabman/bridge
mkdir -p secrets
nano secrets/env
```

Type in (with your real values from step 1.2):

```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key
```

Save with `Ctrl+O`, `Enter`, then `Ctrl+X`. Lock the file down:

```bash
chmod 600 secrets/env
```

> This is the **service_role** key, not the anon key. The bridge needs full
> database access to read the member list and write sessions.

### 4.6 Tell it which machine it controls

```bash
nano config.yaml
```

Change the top two lines:

```yaml
station:
  machine_name: "laser-1"          # must match the slug from step 2.4 exactly
  bridge_name: "bridge-laser-1"    # any unique name for this Pi
```

Leave everything else as it is — it matches the wiring you just did.

---

## Part 5 — Configure the badge reader

The reader ships in a mode where it acts as a smart-card reader. It needs to be
switched to **keyboard mode**, where it simply types the badge number. This is a
one-time change stored in the reader itself.

You need a Windows PC for this.

1. Plug the reader into Windows
2. Download and open **OMNIKEY Workbench** from
   [HID's download page](https://www.hidglobal.com/drivers)
3. Set the mode to **Keyboard Wedge**, with 125 kHz **HID Prox** reading enabled
4. **Test it:** open Notepad and tap a badge. Something like `043f6afba2` should
   type itself. If nothing appears, the change didn't take.

> ⚠️ **Do not disable the "EEM" interface.** If you disable EEM *and* switch to
> keyboard mode, the configuration tool can no longer reach the reader and
> recovering it needs a special configuration card. Only change the mode.

Move the reader back to the Pi.

---

## Part 6 — Test every part

Test one component at a time, so a failure points at one thing. Press `Ctrl+C` to
stop any of them early.

```bash
cd ~/fabman/bridge

# Screen: cycles through all five displays, 2 seconds each
.venv/bin/python -m hardware.display

# Buzzer: plays all eight sounds
.venv/bin/python -m hardware.buzzer

# Button: 30 seconds. Each press prints "press!" and lights the ring
.venv/bin/python -m hardware.button

# Relay: three on/off cycles. Watch the lamp, listen for the click
.venv/bin/python -m hardware.relay

# Badge reader: tap your badge, it prints the ID
.venv/bin/python -m hardware.reader usb_keyboard OMNIKEY
```

Tap the **same badge twice** (must give the identical number) and a **different
badge** (must give a different one).

Fix anything that fails before continuing. A button that prints nothing is
almost always wired to `NC1` instead of `NO1`.

### First full run

```bash
.venv/bin/python bridge.py config.yaml
```

You want to see:

```
Bridge bridge-laser-1 started for machine laser-1 (0 cached credentials)
Sync ok: access=0 pushed=0 enrolled=0
```

**`Sync ok` is the important line** — it means the Pi reached Supabase, the
credentials are right, and the machine name matched. `0 cached credentials` is
correct; nobody's enrolled yet.

The display should read **Press button**, and in the web app your machine should
switch to **online** within 30 seconds.

Press `Ctrl+C` to stop.

**If you see `machine 'laser-1' not found`:** the name in `config.yaml` doesn't
match the slug in the web app. They must be identical.

---

## Part 7 — Go live

### 7.1 Start it automatically

This makes the bridge run on boot and restart itself if it ever crashes.

```bash
sudo tee /etc/systemd/system/fabman-bridge.service > /dev/null <<EOF
[Unit]
Description=Fabman bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/home/$USER/fabman/bridge
ExecStart=/home/$USER/fabman/bridge/.venv/bin/python bridge.py config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now fabman-bridge
systemctl status fabman-bridge --no-pager
```

Watch what it's doing at any time with:

```bash
journalctl -u fabman-bridge -f
```

### 7.2 Enroll yourself and run a session

In the web app:

1. **Enroll** → choose your machine, type your name → **Start enrollment**
2. The Pi's screen changes to **ENROLL / your name**
3. **Tap your badge.** Three rising beeps, and the browser shows the captured ID
4. **Members** → click **Grant** next to your machine

Then at the machine:

1. **Press the button** → screen shows `Tap card` and a countdown
2. **Tap your badge** → two rising beeps, the relay clicks, **the lamp comes on**,
   and the screen counts down from 15 minutes
3. **Tap your badge again** (wait 10 seconds first) → one beep, 15 more minutes
4. **Press the button** → two falling beeps, lamp off, back to the start

Check **Sessions** in the web app — your session should be listed with its
duration.

### 7.3 Prove it survives an outage

Worth doing once, because working offline is the whole point of the design.

```bash
sudo ip link set wlan0 down
```

With no internet at all: press the button, tap your badge — **it still works**.
Run a short session and stop it. Then:

```bash
sudo ip link set wlan0 up
```

Within 30 seconds the offline session appears in the web app. Nothing lost.

### 7.4 Swap in the real machine

Unplug the lamp, plug the actual machine into a **normally-off** socket on the
relay. Done.

---

## When something goes wrong

| What you see | Usually means | Fix |
|---|---|---|
| `i2cdetect` errors about `/dev/i2c-1` | I²C isn't enabled | `sudo raspi-config nonint do_i2c 0` then reboot |
| `i2cdetect` grid is all dashes | Qwiic SHIM not seated | Press it down harder on pins 1–6 |
| Button presses do nothing | Wired to `NC1` | Move that wire to `NO1` (pin 18) |
| `Failed to add edge detection` | Missing GPIO library | `sudo apt install -y swig liblgpio-dev` then reinstall requirements |
| Reader types nothing | Still in smart-card mode | Redo [Part 5](#part-5--configure-the-badge-reader) |
| `No input device matching 'OMNIKEY'` | Same as above | Redo [Part 5](#part-5--configure-the-badge-reader) |
| `machine 'x' not found` | Name mismatch | Make `config.yaml` match the slug in the web app |
| Web app says "no access" | Not on the admin list | Run the SQL in [step 1.6](#16--make-yourself-an-administrator) |
| Dashboard shows bridge offline | Service stopped, or no network | `systemctl status fabman-bridge` |
| Display text is tiny | Font missing | `sudo apt install -y fonts-dejavu` |
| Vercel build fails | Wrong root directory | Only set it if the project is in a subfolder |
| Supabase project paused | No activity for 7+ days | Restore it in the dashboard, check the keepalive job |

---

## Adding a second machine

Much quicker — the cloud side already exists.

1. Web app → **Machines → Add a machine** with a new slug
2. Build and wire another Pi ([Part 3](#part-3--wire-the-hardware) and
   [Part 4](#part-4--set-up-the-raspberry-pi))
3. Use the new slug and a different `bridge_name` in `config.yaml`

Members and permissions are shared across every bridge, so someone you've already
enrolled just needs granting on the new machine.
