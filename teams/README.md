# Avatar Forge — Microsoft Teams app (Phase 1, scope 1A)

This folder packages the Avatar Forge web app as an **anonymous Microsoft Teams
personal tab**. Scope **1A** is a **prototype** with one goal: **make it run in Teams
via sideload, with no Teams-admin access required.**

- Personal-scope **static tab** that embeds the existing web UI (mic + WebRTC avatar).
- **No SSO** and **no org/admin publishing** — those are a later phase you'll drive
  from the Teams admin center once the prototype works.
- Additive only: a templated manifest, two icons, a build script, a no-op-unless-in-Teams
  frontend init (`frontend/teams.js`), and one `frame-ancestors` CSP header in the backend.

> The same package built here is reused unchanged when you later publish it through the
> admin center — so the manifest stays templated and the zip stays valid (manifest + 2
> icons at the archive root).

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

## Run it in Teams (no admin access needed)

You do **not** need to publish to an org catalog. Two sideload routes — try A first; if
your tenant has custom-app upload disabled, use B.

### Route A — Upload a custom app (personal scope)

1. In Teams, go to **Apps → Manage your apps → Upload an app → Upload a custom app**.
2. Select `teams/build/avatar-forge-teams.zip`.
3. Add the app; open the **Avatar** personal tab.
4. When prompted, **allow microphone** (and camera if requested) for the tab.

> ⚠️ Even this basic, personal sideload can be blocked: the **"Upload a custom app"**
> option only appears if the tenant policy *Allow uploading custom apps* is enabled. If you
> don't see it, you're not doing anything wrong — use Route B instead.

### Route B — Teams Developer Portal "Preview in Teams" (admin-free fallback)

The Developer Portal lets you preview a sideloaded app without the upload-custom-app
policy, and is the recommended no-admin path for this prototype.

1. Open the **Teams Developer Portal** — <https://dev.teams.microsoft.com> (also available
   as the **Developer Portal** app inside Teams).
2. **Apps → Import app** and select `teams/build/avatar-forge-teams.zip`.
3. Open the imported app and click **Preview in Teams** (top right). Teams opens and adds
   the app for you.
4. Open the **Avatar** personal tab and **allow microphone** (and camera if requested).

If neither route is available, the last admin-free option is to **add the app to a team
you own** (some tenants allow app uploads scoped to a team even when personal upload is
off) — but for this prototype Route B is the simplest.

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

## Deferred to a later phase (not in this prototype)

- Entra app registration + AAD SSO inside the Teams context.
- Publishing through the **Teams admin center** (org catalog / targeted release / admin
  approval) — you'll do this from the admin portal once the sideloaded prototype works.
  The package built here is reused unchanged for that step.
- Real privacy-policy and terms-of-use pages (the manifest currently points at repo pages,
  which is fine for sideload).
