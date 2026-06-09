# Avatar Forge — Microsoft Teams app (Phase 1, scope 1A)

This folder packages the Avatar Forge web app as an **anonymous Microsoft Teams
personal tab**. Scope **1A** is intentionally minimal:

- Personal-scope **static tab** that embeds the existing web UI (mic + WebRTC avatar).
- **No SSO** and **no org/targeted publishing** — those land in **1B**.
- Additive only: a templated manifest, two icons, a build script, a no-op-unless-in-Teams
  frontend init (`frontend/teams.js`), and one `frame-ancestors` CSP header in the backend.

## Contents

| File | Purpose |
| --- | --- |
| `manifest.template.json` | Teams manifest (schema v1.17) with `{{HOSTNAME}}`, `{{VERSION}}`, `{{APP_ID}}` placeholders. |
| `icons/color.png` | 192×192 color app icon. |
| `icons/outline.png` | 32×32 transparent outline icon (Teams recolors it). |
| `build_package.py` | Stdlib-only script that renders the manifest and zips a sideloadable package. |
| `build/avatar-forge-teams.zip` | Build output (git-ignored). |

## Build the package

You need the **live HTTPS hostname** of the deployed app — the bare host of your
Azure Container App, e.g. `avatar-forge.<region>.azurecontainerapps.io` (no
`https://`, no path, no port).

```bash
uv run python teams/build_package.py --hostname <your-app>.azurecontainerapps.io
```

Optional flags (env var equivalents in parentheses):

- `--version` (`TEAMS_APP_VERSION`) — manifest version, default `1.0.0`.
- `--app-id` (`TEAMS_APP_ID`) — stable app GUID. If omitted, a deterministic GUID is
  derived from the hostname so rebuilds produce the same id.

Output: `teams/build/avatar-forge-teams.zip` containing `manifest.json`,
`color.png`, and `outline.png` at the archive root.

## Sideload into Teams

1. In Teams, go to **Apps → Manage your apps → Upload an app → Upload a custom app**.
   (Custom app upload must be enabled for your tenant/account by a Teams admin.)
2. Select `teams/build/avatar-forge-teams.zip`.
3. Add the app; open the **Avatar** personal tab.
4. When prompted, **allow microphone** (and camera if requested) for the tab.

## Validation checklist (run in Teams **web** AND **desktop**)

- [ ] The tab loads the avatar UI over HTTPS (no blank frame / framing error).
- [ ] Microphone permission prompt appears and, once granted, `getUserMedia` succeeds.
- [ ] The WebRTC avatar **video** renders and the avatar **speaks** (audio out).
- [ ] Talking to the avatar works end-to-end (the WSS voice socket connects).
- [ ] Switching the Teams theme (light ↔ dark) updates the app theme live.
- [ ] The standalone app (`uv run avatar-forge`, port 3000) is unchanged — the Teams
      SDK is never loaded outside Teams.

If the avatar video or mic fails inside Teams, that is the gating risk for 1A — capture
the client (web/desktop), the console errors, and the permission state, and report back
before proceeding.

## How the in-Teams detection works

`frontend/teams.js` activates only when the page is inside Teams — detected via the
`?inTeams=1` query param that the manifest's `contentUrl` carries, with a framed-window
fallback. Outside Teams it returns immediately and the Teams JS SDK is never fetched, so
the standalone experience is byte-for-byte the same. Inside Teams it initializes the SDK
and mirrors the host theme into the app's existing `applyTheme()` hook.

## Embedding header

The backend sends a single response header so the Teams clients can frame the app:

```
Content-Security-Policy: frame-ancestors 'self' \
  https://teams.microsoft.com https://*.teams.microsoft.com \
  https://teams.live.com https://*.teams.live.com \
  https://*.skype.com
```

Only `frame-ancestors` is set on purpose — a full CSP (`script-src`/`connect-src`/
`media-src`) would break inline JS, the WSS voice socket, and WebRTC.

## Coming in 1B (not in this scope)

- Entra app registration + AAD SSO inside the Teams context.
- Targeted-user / org-catalog publishing and admin approval.
- Real privacy policy and terms-of-use pages (the manifest currently points at repo pages).
