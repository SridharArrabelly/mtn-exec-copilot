# Avatar-Forge Teams meeting media bot (.NET / Windows)

> **Phase 2b, issue #27 — Slice 1 (audio).** This is the thin .NET/Windows media
> relay described in [`docs/teams-meeting-bot.md`](../docs/teams-meeting-bot.md).
> It joins a Teams meeting, captures the **mixed participant audio**, and forwards
> raw PCM16 over a WebSocket to the **unchanged** Python backend
> (`backend/acs/bridge.py::AcsVoiceBridge`). All answering / RAG / turn-taking
> stays in Python.

## Why this exists (the one-paragraph version)

A browser/Teams-tab client can only ever hear **its own mic** (Teams client
isolation), and ACS Call Automation server-side media does **not** carry Teams
*meeting* audio — both proven live. The **only** way for Nuru to hear everyone in
the room is the Graph **Real-Time Media Platform** (`Microsoft.Skype.Bots.Media`),
which is **.NET + Windows-only**. So this is a small, separate Windows service that
acts as a dumb media pump into the existing Python brain.

## ⚠️ Platform reality — must build AND run on Windows

`Microsoft.Graph.Communications.Calls.Media` carries a native Windows media stack.
The bot **must run on a Windows Server guest OS** (Cloud Service, Service Fabric +
VMSS, IaaS VM, or AKS Windows node pool). It **cannot** run on Linux/macOS or an
Azure Web App / Linux Container App. You may *edit* the C# anywhere; you must
*build & host* on Windows (`RuntimeIdentifier=win-x64`).

## Architecture

```
Teams meeting ──mixed audio──▶ [Skype.Bots.Media AudioSocket]
                                        │ PCM16 16 kHz
                                        ▼
                               [VoiceLiveBridgeClient]  ──WSS──▶  Python /ws/acs/audio
                                        ▲                          (AcsVoiceBridge ▸ VoiceSessionHandler
                                        │ PCM16 (Nuru's answer)     ▸ Voice Live ▸ Foundry RAG+news)
                               [AudioSocket.Send] ──audio──▶ Teams meeting
```

The seam is the **already-built** `/ws/acs/audio` endpoint. The bot just speaks its
wire protocol (`AudioMetadata` → base64-PCM16 `AudioData` frames; inbound
`AudioData` to play, `StopAudio` for barge-in). The only Python-side requirement for
Slice 1 is two env flags: `MEETING_BOT_ENABLED=true` (serves `/ws/acs/audio` without an
ACS resource) and `ACS_AUDIO_SAMPLE_RATE=16000` (matches the media platform). Both are
already set on the deployed app and wired through bicep.

## Project layout

| Path | Role |
| --- | --- |
| `Program.cs` | ASP.NET host; binds config, starts the bot, maps controllers. |
| `Configuration/BotOptions.cs` | Strongly-typed config (`Bot:*`). |
| `Bot/MeetingBot.cs` | Owns the `ICommunicationsClient`; `JoinMeetingAsync` / `LeaveAsync`. |
| `Bot/CallHandler.cs` | Per-call media plumbing: AudioSocket ⇄ bridge. |
| `Bot/AuthenticationProvider.cs` | App-only Graph token (MSAL) + inbound validation. |
| `Bridge/VoiceLiveBridgeClient.cs` | **The Python contract.** WS client speaking the AcsVoiceBridge protocol. Unit-tested, no media-SDK deps. |
| `Http/JoinController.cs` | Operator API: `POST /api/join`, `POST /api/leave`. |
| `Http/CallingController.cs` | Bot Framework calling webhook (`POST /api/calling`). |
| `infra/host.bicep` | **Standalone** Windows VM + NSG + calling-bot registration. |

## Configuration

Set via `appsettings.json` or environment (`Bot__*`). **Never commit the secret.**

| Key | Value (MngEnv) |
| --- | --- |
| `Bot:AppId` | `860ecee0-c226-4930-8c00-e37bae4a3ae5` (`avatar-forge-meeting-bot`) |
| `Bot:TenantId` | `349b3dac-8649-4410-acdc-ef8bbcb7a46f` |
| `Bot:AppSecret` | from env `BOT_CLIENT_SECRET` (stored in azd env, git-ignored) |
| `Bot:ServiceFqdn` | `avatar-meetingbot-mngenv.swedencentral.cloudapp.azure.com` (`host.bicep` output) |
| `Bot:CertificateThumbprint` | a publicly-trusted cert in `LocalMachine\My` matching the FQDN |
| `Bot:BridgeWebSocketUrl` | `wss://ca-avatar-mngenv-ha2avgzxshnbo.orangepebble-e59f7bd5.swedencentral.azurecontainerapps.io/ws/acs/audio` |
| `Bot:BridgeSampleRate` | `16000` |

## Deployed host (MngEnv, rg-avatar-mngenv) — already provisioned

`host.bicep` is **deployed**. Live resources:

| Resource | Value |
| --- | --- |
| Windows VM | `avatar-meetingbot-vm` (running, `Standard_D2s_v5`, swedencentral) |
| Public FQDN | `avatar-meetingbot-mngenv.swedencentral.cloudapp.azure.com` |
| Signaling endpoint | `https://<fqdn>:9441/api/calling` |
| Operator API | `https://<fqdn>:9441/api/join` |
| Calling-bot registration | `avatar-meetingbot-registration` (Teams channel, `callingWebhook` on) |
| NSG | `avatar-meetingbot-nsg` — 9441 (signaling), 8445 (media), 80 (ACME), 3389 (RDP) |

The Python side is **already live**: the container app has `MEETING_BOT_ENABLED=true`,
`ACS_AUDIO_SAMPLE_RATE=16000`, `ACS_REQUIRE_WAKE_PHRASE=true`, and `/ws/acs/audio`
accepts the bot's handshake (verified with a websockets probe). `MEETING_BOT_ENABLED`
makes the bridge serve the bot **without** provisioning an ACS resource.

## Host setup — `scripts/setup-host.ps1`

A 4-stage helper drives the Windows host. **Stage Prep is already done** on the
deployed VM (firewall rules + .NET 8 SDK / ASP.NET runtime, via `az vm run-command`).
The remaining stages are operator-only (need the private repo on the VM + interactive
cert issuance + a real meeting):

```pwsh
# On the VM (RDP in), from a clone of this repo:
.\meeting-bot\scripts\setup-host.ps1 -Stage Cert  -Email you@example.com   # win-acme Let's Encrypt (HTTP-01, port 80)
.\meeting-bot\scripts\setup-host.ps1 -Stage Build                          # git clone + dotnet publish -r win-x64
.\meeting-bot\scripts\setup-host.ps1 -Stage Run   -Thumbprint <cert-tp> `
    -BridgeUrl wss://ca-avatar-mngenv-ha2avgzxshnbo.orangepebble-e59f7bd5.swedencentral.azurecontainerapps.io/ws/acs/audio `
    -BotSecret <BOT_CLIENT_SECRET>                                         # set Bot__* + install/start the Windows service
```

> Note: this is a **private** repo, so the Build stage needs git auth on the VM
> (e.g. a PAT or `gh auth login`).

## Runbook (operator — Windows host required)

1. ✅ **Host + calling registration** — already deployed (`host.bicep`). To
   re-deploy/update:
   ```pwsh
   az deployment group create -g rg-avatar-mngenv `
     -f meeting-bot/infra/host.bicep `
     -p botAppId=860ecee0-c226-4930-8c00-e37bae4a3ae5 `
        botAppTenantId=349b3dac-8649-4410-acdc-ef8bbcb7a46f `
        adminPassword='<strong-password>' dnsLabel=avatar-meetingbot-mngenv
   ```
2. ✅ **Python side** — already live: `MEETING_BOT_ENABLED=true`,
   `ACS_AUDIO_SAMPLE_RATE=16000`, `ACS_REQUIRE_WAKE_PHRASE=true` on the container app
   (and persisted in the azd env, wired through bicep so a full `azd up` keeps them).
3. ✅ **Prep stage** — firewall + .NET 8 SDK/ASP.NET runtime installed on the VM.
4. **Install a publicly-trusted TLS cert** — `setup-host.ps1 -Stage Cert` (win-acme
   issues a Let's Encrypt cert via HTTP-01 on port 80, already open). Record the
   thumbprint.
5. **Build & publish the bot on the VM** — `setup-host.ps1 -Stage Build` (clones the
   repo, `dotnet publish -r win-x64`).
6. **Run** — `setup-host.ps1 -Stage Run -Thumbprint <tp> -BridgeUrl <wss .../ws/acs/audio> -BotSecret <BOT_CLIENT_SECRET>`
   installs/starts the `AvatarForgeMeetingBot` Windows service.
7. **Teams manifest:** build with `python teams/build_package.py --enable-calling`
   (sets `supportsCalling: true`), upload. Requires a tenant **custom-app** policy + a
   **meeting policy allowing bots** (you are global admin in MngEnv, so self-serviceable).
8. **Test:** start a Teams meeting in the MngEnv tenant, then
   `POST https://avatar-meetingbot-mngenv.swedencentral.cloudapp.azure.com:9441/api/join { "joinUrl": "<meeting link>" }`.
   Nuru should appear in the roster, hear the room, and answer aloud on the wake phrase.

## What is verified vs. pending

- ✅ **`VoiceLiveBridgeClient` — the Python contract — is unit-tested** (metadata,
  outbound `AudioData`, inbound `AudioData` dispatch, `StopAudio` barge-in all pass
  a round-trip against a mock server).
- ✅ `infra/host.bicep` compiles clean (`az bicep build`).
- ⏳ The media-SDK code (`MeetingBot.cs`, `CallHandler.cs`) can only be built/run on
  a Windows host with the Graph media packages restored and a real meeting. Points
  needing live confirmation are marked `TODO(prod...)` in the source: HTTPS/cert
  binding in `Program.cs`, inbound notification validation in `AuthenticationProvider`,
  and exact `AudioSocket`/`AudioSendBuffer` API shapes against the restored SDK
  version.

## Cost / honesty note

Per the ADR in `docs/teams-meeting-bot.md`, this breaks the pure-Python / Linux-ACA
guardrail **only** for the media leg, because no alternative can hear the room. The
brain stays Python; this service stays a dumb pump. The real tax is the Windows host
+ certs + one extra PCM hop — not the language.
