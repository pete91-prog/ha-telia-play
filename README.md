# Telia Play for Home Assistant

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

Netflix / Max may still ask you to sign in to *their* account in the browser. That login belongs to them; this only skips Telia’s “wait for the email” step.

## Install

1. Copy `custom_components/telia_play` into your Home Assistant `config/custom_components/` folder.
2. Restart Home Assistant.
3. Settings → Devices & services → Add integration → **Telia Play**.
4. Country **Norway**, then the Telia Play username and password.

The first login on Telia Play Norway may need BankID on [teliaplay.no](https://www.teliaplay.no/). After that, the same password works here.

Optional: enable **Also create switches for channel packages** if you also want to spend points on TV channels, not only streaming services.

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

Paste a code or magic link if a partner still sent one:

```yaml
action: telia_play.submit_activation_code
data:
  service: Viaplay
  code: AB12CD
```

Voice / Assist example: “Turn on Netflix on Telia Play” can call `telia_play.enable_service`.

Typical point costs (Norway, can change): Netflix 50, Viaplay 45, Max from 30, SkyShowtime 30, Prime from 20.

## Disclaimer

Unofficial. Not endorsed by Telia. Uses the same account APIs as the Telia Play website.
