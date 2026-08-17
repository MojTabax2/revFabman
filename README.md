# Fabman

RFID access control for shop machines.

Press a button, tap your work badge, and the machine gets power for 15 minutes.
Press again to stop. Everything is logged, and who's allowed on what is managed
from a web app you can reach from anywhere.

Built for [Rev: Ithaca Startup Works](https://www.rev.ithaca.ny.us/), and
designed so one person with a soldering-free parts kit and an afternoon can
build another one.

---

## What it does

```
   IDLE ──press button──► READY ──tap badge──► RUNNING ──press button──► IDLE
                            │                    │
                            │                    ├─ tap your own badge again: +15 min
                     unknown badge               ├─ 1 minute left: 3 warning beeps
                            │                    └─ time's up: stops on its own
                            ▼
                         DENIED
```

- **The button arms the reader.** Badges do nothing until someone presses it, so
  a badge in a pocket walking past can't start a machine.
- **Only the person using the machine can extend it.** Someone else's badge is
  ignored mid-session.
- **Sessions end themselves.** 15 minutes by default, 5 hours maximum with
  extensions, so a machine can't be left running all night.
- **It works when the internet doesn't.** Each bridge keeps its own copy of who's
  allowed, so a Wi-Fi outage never locks the shop out of its tools.

## How it's put together

```
   ┌──────────────────────────────────────────────┐
   │  CLOUD                                       │
   │    Vercel  ── admin web app                  │
   │    Supabase ── database + login              │
   └────────────────────┬─────────────────────────┘
                        │ syncs every 30s
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   ┌─────────┐     ┌─────────┐     ┌─────────┐
   │ Bridge  │     │ Bridge  │     │   ...   │   one per machine
   │ (Pi)    │     │ (Pi)    │     │         │
   └────┬────┘     └────┬────┘     └─────────┘
        │               │
   [ machine ]     [ machine ]
```

Each **bridge** is a Raspberry Pi wired to a badge reader, a small screen, a
button, a buzzer, and a relay that switches the machine's power. It decides
access from a local copy of the member list, then syncs with the cloud in the
background.

The **web app** is where you add people, grant access, watch what's running, and
review usage history.

## What you need per bridge

| Part | Roughly | Notes |
|---|---|---|
| [Raspberry Pi 3B+](https://www.raspberrypi.com/products/raspberry-pi-3-model-b-plus/) | $35 | Plus microSD card and power supply |
| [HID OMNIKEY 5427CK Gen 2](https://www.hidglobal.com/products/omnikey-5427ck) | $80–110 | Part **R54270101**. Reads HID Prox employee badges |
| [SparkFun Qwiic OLED 0.91"](https://www.sparkfun.com/products/24606) | $13 | The little display |
| [SparkFun Qwiic SHIM](https://www.sparkfun.com/products/15794) | $2 | Connects the display to the Pi |
| [Adafruit 16mm metal pushbutton](https://www.adafruit.com/product/559) | $5 | With LED ring |
| [Adafruit piezo buzzer](https://www.adafruit.com/product/160) | $2 | |
| [SparkFun IoT Power Relay](https://www.sparkfun.com/products/14236) | $30 | Switches the machine's mains power |
| Jumper wires (female-female) | $5 | You need 8 |

**~$175 per machine.** No soldering required.

You'll also need free accounts with **GitHub**, **Supabase**, and **Vercel**.

> ⚠️ **Your badges must be compatible.** This reads 125 kHz **HID Prox** cards —
> the common corporate badge. It will *not* read 13.56 MHz MIFARE/NFC badges. If
> you're unsure, [BUILD.md](BUILD.md) shows how to check before you buy anything.

## Documentation

| Doc | Read it when |
|---|---|
| **[BUILD.md](BUILD.md)** | **Building one from scratch.** Start here. |
| [ADMIN.md](ADMIN.md) | Running it day to day — adding people, granting access, fixing problems |
| [DEVELOPING.md](DEVELOPING.md) | Changing the code, or understanding how it works inside |

## Repo layout

```
bridge/      Python program that runs on each Raspberry Pi
web/         Next.js admin app, deployed to Vercel
supabase/    Database setup (SQL you paste into Supabase once)
.github/     Scheduled job that keeps the free Supabase project awake
```

## License

MIT — see [LICENSE](LICENSE).
