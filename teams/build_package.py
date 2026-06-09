#!/usr/bin/env python3
"""Build a sideloadable Microsoft Teams app package (scope 1A).

Substitutes the templated values in ``manifest.template.json`` and zips the
resulting ``manifest.json`` together with the two icons **at the zip root**
(Teams requires a flat archive — no nested folders).

Stdlib only — this is a pure-Python repo (uv) with no Node toolchain.

Usage:
    uv run python teams/build_package.py --hostname my-app.azurecontainerapps.io
    # or via env:
    TEAMS_HOSTNAME=my-app.azurecontainerapps.io uv run python teams/build_package.py

Inputs (CLI flag overrides env var):
    --hostname / TEAMS_HOSTNAME      Required. Bare ACA hostname, no scheme/path/port.
    --version  / TEAMS_APP_VERSION   Optional. Manifest version (default 1.0.0).
    --app-id   / TEAMS_APP_ID        Optional. Stable GUID. Defaults to a deterministic
                                     uuid5 derived from the hostname so rebuilds match.

Output:
    teams/build/avatar-forge-teams.zip
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "manifest.template.json")
ICONS_DIR = os.path.join(HERE, "icons")
BUILD_DIR = os.path.join(HERE, "build")
OUTPUT_ZIP = os.path.join(BUILD_DIR, "avatar-forge-teams.zip")

# A fixed namespace so uuid5(hostname) is stable across machines/runs.
_APP_ID_NAMESPACE = uuid.UUID("6f6c1d2e-7a4b-5c8d-9e0f-1a2b3c4d5e6f")

_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$", re.IGNORECASE)


def _normalize_hostname(raw: str) -> str:
    """Reject scheme/path/port; return a bare, validated hostname for validDomains."""
    host = (raw or "").strip()
    if not host:
        sys.exit("error: hostname is required (pass --hostname or set TEAMS_HOSTNAME)")
    if "://" in host:
        sys.exit(f"error: hostname must not include a scheme: {host!r} (use the bare host, e.g. my-app.azurecontainerapps.io)")
    if "/" in host:
        sys.exit(f"error: hostname must not include a path/slash: {host!r}")
    if ":" in host:
        sys.exit(f"error: hostname must not include a port: {host!r} (Teams validDomains is a bare host)")
    if not _HOSTNAME_RE.match(host):
        sys.exit(f"error: {host!r} does not look like a valid DNS hostname")
    return host.lower()


def _resolve_app_id(raw: str | None, hostname: str) -> str:
    if raw:
        try:
            return str(uuid.UUID(raw))
        except ValueError:
            sys.exit(f"error: --app-id must be a valid GUID, got {raw!r}")
    return str(uuid.uuid5(_APP_ID_NAMESPACE, hostname))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Teams app package (scope 1A).")
    parser.add_argument("--hostname", default=os.getenv("TEAMS_HOSTNAME"))
    parser.add_argument("--version", default=os.getenv("TEAMS_APP_VERSION", "1.0.0"))
    parser.add_argument("--app-id", default=os.getenv("TEAMS_APP_ID"))
    args = parser.parse_args(argv)

    hostname = _normalize_hostname(args.hostname)
    app_id = _resolve_app_id(args.app_id, hostname)
    version = args.version.strip()

    with open(TEMPLATE, "r", encoding="utf-8") as f:
        manifest_text = f.read()

    manifest_text = (
        manifest_text
        .replace("{{HOSTNAME}}", hostname)
        .replace("{{VERSION}}", version)
        .replace("{{APP_ID}}", app_id)
    )

    # Fail fast if any placeholder slipped through or the result is not valid JSON.
    leftover = re.findall(r"\{\{[A-Z_]+\}\}", manifest_text)
    if leftover:
        sys.exit(f"error: unsubstituted placeholders remain: {sorted(set(leftover))}")
    try:
        manifest = json.loads(manifest_text)
    except json.JSONDecodeError as e:
        sys.exit(f"error: rendered manifest is not valid JSON: {e}")

    # Defensive: validDomains entries must stay scheme/path free.
    for d in manifest.get("validDomains", []):
        if "://" in d or "/" in d:
            sys.exit(f"error: validDomains entry must be a bare host: {d!r}")

    color = os.path.join(ICONS_DIR, "color.png")
    outline = os.path.join(ICONS_DIR, "outline.png")
    for p in (color, outline):
        if not os.path.isfile(p):
            sys.exit(f"error: missing icon {p}")

    os.makedirs(BUILD_DIR, exist_ok=True)
    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        # Names are written at the archive root (no folder prefixes).
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.write(color, "color.png")
        zf.write(outline, "outline.png")

    print(f"Built {OUTPUT_ZIP}")
    print(f"  hostname: {hostname}")
    print(f"  version:  {version}")
    print(f"  app id:   {app_id}")
    print("Sideload it in Teams via: Apps -> Manage your apps -> Upload an app -> Upload a custom app")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
