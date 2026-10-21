# Telia Play for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pete91-prog&repository=ha-telia-play&category=integration)

Choose which **streaming services** are enabled on your Telia Play subscription, using the **TV points** that are already included.

This talks to the same **Valgmeny** as [teliaplay.no](https://account.teliaplay.no/subscription/selection-menu): you turn Netflix, Max, Viaplay, and the other services on or off. Points are not spent forever — turning a service off gives the points back.

The points picker is the Norwegian Telia Play model (often 60, 40 or 15 included points). Sweden uses fixed streaming packages instead.

## What you get

| Entity | What it does |
| --- | --- |
| `sensor.telia_play_tv_points_remaining` | How many included points you still have |
| `sensor.telia_play_tv_points_used` | Points currently allocated |
| `sensor.telia_play_tv_points_included` | Points in the subscription |
| `switch.telia_play_netflix` (and one switch per service) | Enable or disable that streaming service |
| `button.telia_play_activate_netflix` | Retry partner activation without waiting for email |

Turning a switch **on** uses points. Turning it **off** returns them. If there are not enough points left, the action fails instead of overspending.

## Activation codes (email / QR)

Some services (Netflix, Max, Prime, Viaplay, Disney+, …) still need a one-time partner activation after you spend the points. Telia normally sends that as **mail or a QR code**. This integration does **not wait for that mail**.

When you enable a service it will:

1. Read the same `externalRegistration` activation URL Telia would put in the email / QR.
2. Sign in to the partner through Telia’s SSO (`/rest/secure/v1/partner/{id}/login`) using your existing Telia session.
3. Open that URL immediately, following Telia redirects with your session (the Telia token is **not** sent on to Netflix/Max).
4. If Telia already issued a pairing code on that page, surface it in a persistent notification — so you can type it on the TV without opening mail.
5. If a partner still mailed a code or magic link anyway, paste it with `telia_play.submit_activation_code` (a full `https://…` link works too).

A persistent notification is created per service, with a one-tap activation link. Switch attributes include `needs_activation`, `activation_url`, `activation_status`, and `pairing_code`.

How partners usually finish:

- **Prime** — Telia’s activation link is enough. No SMS or email code.
- **HBO / Max** — the SMS goes to `activate.teliacompany.com/startactivation/…/max/…`. Paste that link if it is unused. If it is **already opened on the phone**, that UUID is spent: finish Max on that phone (the email code only works there), or press **Activate HBO/Max** so Telia can issue a new link. The 6-digit **email** code can be submitted automatically from a mailbox you own (see below).
- **Netflix** (and similar) — may still ask you to sign in to *their* account in the browser. That login belongs to them; this only skips Telia’s “wait for the email” step.

### Automatic Max email codes

Home Assistant can finish Max without you pasting the 6-digit code, using **your** inbox. This integration does **not** create or host a shared mailbox for those emails.

Create that mailbox yourself if you want a dedicated address. Enter the address, password, and IMAP login **only in Home Assistant** (Telia Play → Configure, or Home Assistant’s IMAP integration). Do **not** put that email, app password, Telia login, or any token in this GitHub repository.

1. Use the same address on the Max page as the inbox Home Assistant can read, **or** forward Max mail to that inbox.
2. Either:
   - **Telia Play → Configure**: IMAP server (Gmail: `imap.gmail.com`), port `993`, mailbox email, and password. Gmail needs an [app password](https://support.google.com/accounts/answer/185833) and IMAP enabled.
   - **or** add Home Assistant’s built-in **IMAP** integration on that same inbox. New `imap_content` events are picked up automatically.
3. Enable Max (or press **Activate HBO/Max**). Telia’s SMS link is opened from the API; when Max mails the code, it is submitted on its own.

Polling runs only while Max still needs a code. Telia login, mailbox address, app password, tokens, and one-time codes are stored **only in your Home Assistant** (config entries). They are not written to this repository, not included in diagnostics you download, and not written to Home Assistant logs.

## Install with HACS

### One tap (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pete91-prog&repository=ha-telia-play&category=integration)

That opens HACS on your Home Assistant and adds [pete91-prog/ha-telia-play](https://github.com/pete91-prog/ha-telia-play) as a custom integration.

### Manually in HACS

1. Open **HACS** in Home Assistant.
2. Three dots (top right) → **Custom repositories**.
3. Repository: `https://github.com/pete91-prog/ha-telia-play`
4. Type: **Integration** → **Add**.
5. Search for **Telia Play** → **Download**.
6. Restart Home Assistant.

### Without HACS

Copy `custom_components/telia_play` into `config/custom_components/`, then restart.

### Configure

1. Settings → Devices & services → Add integration → **Telia Play**.
2. Country **Norway**.
3. Use the **same email (or mobile) and password** as on [teliaplay.no](https://www.teliaplay.no/).
4. If Telia still rejects that password — common when the website only offers **Google or BankID** — continue and approve the **TV code** on your phone. You stay on your own account.

The first Telia Play Norway login on a new device may need BankID on [teliaplay.no](https://www.teliaplay.no/).

Optional: enable **Also create switches for channel packages** if you also want to spend points on TV channels, not only streaming services.

To let Max activation finish by itself, open **Configure** on the Telia Play integration and add IMAP for a mailbox you own — or add Home Assistant’s IMAP integration on that inbox.

## Use it

### Dashboard

Add the points sensors and the service switches to a dashboard. Flip **Netflix** on when you want it, off when you want the 50 points for Max instead. If activation is still pending, press **Activate Netflix** or open the notification link.

### Actions

```yaml
action: telia_play.enable_service
data:
  service: Netflix
```

```yaml
action: telia_play.disable_service
data:
  service: Netflix
```

Replace the streaming-service selection in one step (channel packages are left as they are):

```yaml
action: telia_play.set_streaming_services
data:
  services:
    - Max
    - SkyShowtime
```

Retry activation (same as the button, skips the mailbox):

```yaml
action: telia_play.activate_service
data:
  service: Viaplay
```

Paste a code or magic link if a partner still sent one. **Max** typically texts an `https://` link, then emails a 6-digit one-time code. If the mailbox is connected, the email code is submitted automatically. You can still paste the SMS link first, then the email code (the service name is optional if only Max is waiting):

```yaml
action: telia_play.submit_activation_code
data:
  service: Max
  code: https://www.max.com/activate/...
```

```yaml
action: telia_play.submit_activation_code
data:
  service: Max
  code: 123456
```

There is also an **Activation code** text box on the Telia Play device for the same paste.

Voice / Assist example: “Turn on Netflix on Telia Play” can call `telia_play.enable_service`.

Typical point costs (Norway, can change): Netflix 50, Viaplay 45, Max from 30, SkyShowtime 30, Prime from 20.

## Disclaimer

Unofficial. Not endorsed by Telia. Uses the same account APIs as the Telia Play website.
