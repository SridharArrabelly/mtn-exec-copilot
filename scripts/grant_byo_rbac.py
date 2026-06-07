"""Idempotently grant runtime RBAC on BYO (brownfield) Foundry and AI Search resources.

Runs from the azd `postprovision` hook AFTER Bicep finishes. Bicep cannot create role
assignments on resources it doesn't own without hitting `RoleAssignmentExists` on every
re-deploy (the deterministic guid() collides with any pre-existing assignment that grants
the same principal+role+scope, regardless of who created it). So we do it here with `az`
and treat duplicate-assignment errors as success.

Grants (only when the relevant BYO vars are set):
    UAMI -> Cognitive Services User on BYO Foundry account
    UAMI -> Azure AI Developer on BYO Foundry account
    UAMI -> Search Index Data Reader on BYO Search service
    UAMI -> Search Service Contributor on BYO Search service
    Foundry project SMI -> Search Index Data Contributor on BYO Search service (both BYO)
    Foundry project SMI -> Search Service Contributor on BYO Search service (both BYO)

Required env vars (set by Bicep outputs via azd):
    AZURE_SUBSCRIPTION_ID, SERVICE_APP_IDENTITY_PRINCIPAL_ID
    FOUNDRY_ACCOUNT_NAME, FOUNDRY_RESOURCE_GROUP   (for Foundry grants)
    SEARCH_SERVICE_NAME, SEARCH_RESOURCE_GROUP     (for Search grants)
    AGENT_PROJECT_NAME                             (for the both-BYO project SMI lookup)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys


ROLES = {
    "cognitive_services_user": "a97b65f3-24c7-4388-baec-2e87135dc908",
    "azure_ai_developer": "64702f94-c441-49e6-a78b-ef80e0188fee",
    "search_index_data_reader": "1407120a-92aa-4202-b7e9-c0e197c71c8f",
    "search_index_data_contributor": "8ebe5a00-799e-43f5-93ac-243d3dce84a7",
    "search_service_contributor": "7ca78c08-252a-4471-8644-bb5ff32d4ba0",
}


def _run(cmd: list[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def _grant(label: str, principal_id: str, role_id: str, scope: str) -> None:
    """Create a role assignment and swallow 'already exists' as success."""
    print(f"  -> {label}", flush=True)
    cmd = [
        "az", "role", "assignment", "create",
        "--assignee-object-id", principal_id,
        "--assignee-principal-type", "ServicePrincipal",
        "--role", role_id,
        "--scope", scope,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0:
        return
    err = (proc.stderr or "") + (proc.stdout or "")
    # Idempotency: treat duplicates as success. Az CLI returns various phrasings here.
    if "RoleAssignmentExists" in err or "already exists" in err.lower():
        print("     (already exists - ok)", flush=True)
        return
    print(f"     FAILED: {err.strip()}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def _lookup_foundry_project_principal_id(
    account_name: str, rg: str, project_name: str, sub_id: str
) -> str | None:
    """Read the system-assigned identity of an existing Foundry project."""
    try:
        proc = _run([
            "az", "cognitiveservices", "account", "project", "show",
            "--name", account_name,
            "--project-name", project_name,
            "--resource-group", rg,
            "--subscription", sub_id,
            "-o", "json",
        ], check=False)
    except FileNotFoundError:
        print("ERROR: az CLI not found on PATH - cannot grant BYO RBAC.", file=sys.stderr)
        raise SystemExit(1)
    if proc.returncode != 0:
        print(
            "WARN: could not look up existing Foundry project SMI (project '"
            f"{project_name}' in account '{account_name}' / RG '{rg}').\n"
            f"      {proc.stderr.strip()}\n"
            "      Skipping project->search RBAC grant. The agents azure_ai_search tool may not work\n"
            "      until you grant the project's managed identity Search Index Data Contributor manually.",
            file=sys.stderr,
        )
        return None
    data = json.loads(proc.stdout)
    return (data.get("identity") or {}).get("principalId")


def main() -> int:
    if shutil.which("az") is None:
        print("ERROR: az CLI not found on PATH.", file=sys.stderr)
        return 1

    sub_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "").strip()
    uami_pid = os.environ.get("SERVICE_APP_IDENTITY_PRINCIPAL_ID", "").strip()
    if not sub_id or not uami_pid:
        print(
            "Skipping BYO RBAC: AZURE_SUBSCRIPTION_ID or SERVICE_APP_IDENTITY_PRINCIPAL_ID not set.\n"
            "(Provision may not have completed - re-run `azd provision` once the underlying issue is fixed.)",
            file=sys.stderr,
        )
        return 0

    foundry_account = os.environ.get("FOUNDRY_ACCOUNT_NAME", "").strip()
    foundry_rg = os.environ.get("FOUNDRY_RESOURCE_GROUP", "").strip()
    search_name = os.environ.get("SEARCH_SERVICE_NAME", "").strip()
    search_rg = os.environ.get("SEARCH_RESOURCE_GROUP", "").strip()
    agent_project = os.environ.get("AGENT_PROJECT_NAME", "").strip()

    byo_foundry = bool(foundry_account and foundry_rg)
    byo_search = bool(search_name and search_rg)

    if not byo_foundry and not byo_search:
        print("No BYO resources configured (FOUNDRY_* / SEARCH_* empty) - nothing to grant.")
        return 0

    if byo_foundry:
        foundry_scope = (
            f"/subscriptions/{sub_id}/resourceGroups/{foundry_rg}"
            f"/providers/Microsoft.CognitiveServices/accounts/{foundry_account}"
        )
        print(f"Granting UAMI ({uami_pid}) runtime roles on BYO Foundry '{foundry_account}':")
        _grant("Cognitive Services User", uami_pid, ROLES["cognitive_services_user"], foundry_scope)
        _grant("Azure AI Developer",      uami_pid, ROLES["azure_ai_developer"],      foundry_scope)

    if byo_search:
        search_scope = (
            f"/subscriptions/{sub_id}/resourceGroups/{search_rg}"
            f"/providers/Microsoft.Search/searchServices/{search_name}"
        )
        print(f"Granting UAMI ({uami_pid}) runtime roles on BYO Search '{search_name}':")
        _grant("Search Index Data Reader",  uami_pid, ROLES["search_index_data_reader"],  search_scope)
        _grant("Search Service Contributor", uami_pid, ROLES["search_service_contributor"], search_scope)

    # Both-BYO symmetry: Foundry project SMI also needs access to the BYO Search index
    # so the agents `azure_ai_search` tool can read at runtime. (Greenfield handles this
    # in-Bicep via searchRoleForProject.)
    if byo_foundry and byo_search and agent_project:
        project_pid = _lookup_foundry_project_principal_id(foundry_account, foundry_rg, agent_project, sub_id)
        if project_pid:
            print(f"Granting Foundry project SMI ({project_pid}) Search roles on BYO Search:")
            _grant("Search Index Data Contributor", project_pid, ROLES["search_index_data_contributor"], search_scope)
            _grant("Search Service Contributor",    project_pid, ROLES["search_service_contributor"],    search_scope)

    print("BYO RBAC complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
