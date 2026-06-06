"""Pre-deployment region & capability checks for avatar-forge.

Run BEFORE `azd up` to catch the silent failures we have hit before:

1. Voice Live (preview) is only available in a small set of regions. Deploying
   the Foundry account elsewhere connects fine but the WebSocket closes ~2s
   later with no error event -> "SESSION_UPDATED event not received".
2. Foundry / Cognitive Services AIServices kind must be allowed in the target
   region.
3. Avatar voices need a TTS-with-avatar region.
4. The user must be logged in to `az` so the script can ask ARM what is
   available.

Usage:
    uv run python scripts/preflight.py --location southafricanorth
    uv run python scripts/preflight.py --location eastus2 --voicelive-location eastus2
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass

# Voice Live (preview) supported regions as of 2026-06.
# Keep in sync with:
# https://learn.microsoft.com/azure/ai-services/speech-service/regions#voice-live
VOICELIVE_REGIONS = {
    "eastus2",
    "swedencentral",
    "southeastasia",
    "centralindia",
    "westus2",
}

# Avatar (TTS avatar / video sync) regions.
# https://learn.microsoft.com/azure/ai-services/speech-service/regions#text-to-speech
AVATAR_REGIONS = {
    "westus2",
    "westeurope",
    "southeastasia",
    "northeurope",
    "swedencentral",
    "eastus2",
}

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _az() -> str:
    exe = shutil.which("az") or shutil.which("az.cmd")
    if not exe:
        print(f"{RED}FAIL{RESET}  Azure CLI (`az`) not found on PATH.")
        sys.exit(2)
    return exe


def _run(args: list[str]) -> tuple[int, str, str]:
    res = subprocess.run([_az(), *args], capture_output=True, text=True, check=False)
    return res.returncode, res.stdout, res.stderr


def check_login() -> CheckResult:
    code, out, err = _run(["account", "show", "-o", "json"])
    if code != 0 or not out.strip():
        return CheckResult("az login", False, err.strip() or "not signed in; run `az login`")
    acct = json.loads(out)
    return CheckResult("az login", True, f"{acct.get('user', {}).get('name', '?')} / sub {acct.get('name')}")


def check_voicelive(location: str) -> CheckResult:
    ok = location in VOICELIVE_REGIONS
    detail = (
        f"`{location}` is supported"
        if ok
        else f"`{location}` is NOT a Voice Live region. Supported: {sorted(VOICELIVE_REGIONS)}"
    )
    return CheckResult("Voice Live region", ok, detail)


def check_avatar(location: str) -> CheckResult:
    ok = location in AVATAR_REGIONS
    detail = (
        f"`{location}` supports TTS avatar"
        if ok
        else f"`{location}` does NOT support TTS avatar. Supported: {sorted(AVATAR_REGIONS)}"
    )
    return CheckResult("Avatar region", ok, detail)


def check_aiservices(location: str) -> CheckResult:
    """Confirm AIServices kind is offered in the region."""
    code, out, err = _run([
        "cognitiveservices", "account", "list-skus",
        "--location", location, "--kind", "AIServices", "-o", "json",
    ])
    if code != 0 or not out.strip():
        return CheckResult("Foundry AIServices SKU", False, err.strip() or "no AIServices SKUs returned")
    skus = json.loads(out)
    s0 = [s for s in skus if s.get("name") == "S0"]
    if not s0:
        return CheckResult("Foundry AIServices SKU", False, f"no S0 SKU in {location}")
    return CheckResult("Foundry AIServices SKU", True, f"S0 available in {location}")


def check_provider_registered(provider: str) -> CheckResult:
    code, out, _ = _run(["provider", "show", "-n", provider, "--query", "registrationState", "-o", "tsv"])
    state = out.strip()
    ok = state == "Registered"
    return CheckResult(f"Provider {provider}", ok, state or "unknown")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--location", required=True, help="Main azd location (e.g. southafricanorth)")
    p.add_argument(
        "--voicelive-location",
        default=None,
        help="Foundry/Voice Live region. Defaults to --location. Set this when splitting regions.",
    )
    args = p.parse_args()

    voicelive_loc = args.voicelive_location or args.location

    checks: list[CheckResult] = [
        check_login(),
        check_provider_registered("Microsoft.CognitiveServices"),
        check_provider_registered("Microsoft.App"),
        check_provider_registered("Microsoft.Search"),
        check_aiservices(voicelive_loc),
        check_voicelive(voicelive_loc),
        check_avatar(voicelive_loc),
    ]

    failed = 0
    for c in checks:
        tag = f"{GREEN}OK  {RESET}" if c.ok else f"{RED}FAIL{RESET}"
        print(f"{tag}  {c.name}: {c.detail}")
        if not c.ok:
            failed += 1

    if failed:
        print(f"\n{RED}{failed} check(s) failed.{RESET} Fix before running `azd up`.")
        if voicelive_loc not in VOICELIVE_REGIONS:
            print(
                f"{YELLOW}Hint:{RESET} pick a Voice Live region for the Foundry account, e.g.\n"
                f"  azd env set FOUNDRY_LOCATION eastus2\n"
                f"and keep AZURE_LOCATION wherever you want the rest of the stack."
            )
        return 1

    print(f"\n{GREEN}All preflight checks passed.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
