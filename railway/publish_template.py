#!/usr/bin/env python3
"""Create, publish and update the HiveGate Railway template through Railway's public API.

Railway has no "create template from a file" call. A template is generated from a
project, so this script keeps a small *source project* in the owner's workspace that
holds exactly what the template should deploy (Postgres + the HiveGate image with its
variables), generates the template from it, and publishes it.

    export RAILWAY_TOKEN=...            # account token: railway.com/account/tokens
    python railway/publish_template.py workspaces
    python railway/publish_template.py create  --workspace <id> --tag 0.4.1
    python railway/publish_template.py publish --workspace <id> --template <id>
    python railway/publish_template.py update  --workspace <id> --tag 0.4.2

`create` stops before publishing and prints the generated config, so it can be
checked first. `update` points the source project at a new image tag and regenerates.

Stdlib only. See TEMPLATE.md for why each variable is set the way it is.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://backboard.railway.com/graphql/v2"
IMAGE = "ghcr.io/hivegate-ai/hivegate"
SOURCE_PROJECT = "hivegate-template-source"
SERVICE = "hivegate"
POSTGRES_TEMPLATE_CODE = "postgres"  # Railway's official PostgreSQL template
HERE = Path(__file__).resolve().parent

FERNET_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

# The template's variables. `${{ secret(...) }}` is evaluated per deploy, so every
# deployer gets their own admin secret and encryption key.
VARIABLES = {
    "DB_DRIVER": "postgresql+psycopg",
    "DB_HOST": "${{Postgres.PGHOST}}",
    "DB_PORT": "${{Postgres.PGPORT}}",
    "DB_USER": "${{Postgres.PGUSER}}",
    "DB_PASS": "${{Postgres.PGPASSWORD}}",
    "DB_DATABASE": "${{Postgres.PGDATABASE}}",
    "WAIT_FOR_DB": "true",
    "ADMIN_SECRET": "${{ secret(48) }}",
    # A Fernet key is url-safe base64 of 32 bytes: 43 characters plus one "=".
    "SECRET_TOKEN_ENC_KEY": '${{ secret(43, "%s") }}=' % FERNET_ALPHABET,
    "PROMPT_STORAGE_BACKEND": "postgres",
    "GOOGLE_API_KEY": "",
    "DEFAULT_CHAT_MODEL": "gemini-3-flash-preview",
}
HEALTHCHECK_PATH = "/health"

PUBLISH = {
    "category": "AI/ML",
    "description": (
        "Run your Agno agents as a production REST API: per-user sessions and OAuth, "
        "human approval before writes, multi-agent teams."
    ),
    "image": "https://hivegate.dev/assets/favicon.svg",
}


class RailwayError(RuntimeError):
    pass


def gql(query: str, variables: dict | None = None) -> dict:
    token = os.environ.get("RAILWAY_TOKEN") or os.environ.get("RAILWAY_API_TOKEN")
    if not token:
        sys.exit("Set RAILWAY_TOKEN to an account token (railway.com/account/tokens).")
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "hivegate-template-publisher",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        raise RailwayError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:500]}") from e
    if body.get("errors"):
        raise RailwayError("; ".join(err.get("message", str(err)) for err in body["errors"]))
    return body["data"]


# --- lookups -----------------------------------------------------------------

def workspaces() -> list[dict]:
    data = gql("{ me { workspaces { id name } } }")
    return data["me"]["workspaces"]


def find_source_project(workspace_id: str) -> dict | None:
    data = gql(
        """query($ws: String!) { projects(workspaceId: $ws) { edges { node {
             id name
             environments { edges { node { id name } } }
             services { edges { node { id name } } }
           } } } }""",
        {"ws": workspace_id},
    )
    for edge in data["projects"]["edges"]:
        if edge["node"]["name"] == SOURCE_PROJECT:
            return edge["node"]
    return None


def env_id(project: dict) -> str:
    envs = [e["node"] for e in project["environments"]["edges"]]
    prod = [e for e in envs if e["name"] == "production"]
    return (prod or envs)[0]["id"]


def service_id(project: dict, name: str) -> str | None:
    for e in project["services"]["edges"]:
        if e["node"]["name"] == name:
            return e["node"]["id"]
    return None


# --- steps -------------------------------------------------------------------

def create_project(workspace_id: str) -> dict:
    gql(
        """mutation($in: ProjectCreateInput!) { projectCreate(input: $in) { id } }""",
        {"in": {
            "name": SOURCE_PROJECT,
            "workspaceId": workspace_id,
            "description": "Source of the published HiveGate template. Edit with "
                           "hivegate-ai/deploy railway/publish_template.py, not by hand.",
        }},
    )
    project = find_source_project(workspace_id)
    if not project:
        raise RailwayError("project was created but cannot be found")
    return project


def add_postgres(project: dict, workspace_id: str) -> None:
    tpl = gql(
        """query($c: String!) { template(code: $c) { id serializedConfig } }""",
        {"c": POSTGRES_TEMPLATE_CODE},
    )["template"]
    gql(
        """mutation($in: TemplateDeployV2Input!) { templateDeployV2(input: $in) { projectId } }""",
        {"in": {
            "templateId": tpl["id"],
            "serializedConfig": tpl["serializedConfig"],
            "projectId": project["id"],
            "environmentId": env_id(project),
            "workspaceId": workspace_id,
        }},
    )


def add_hivegate(project: dict, tag: str) -> str:
    env = env_id(project)
    sid = gql(
        """mutation($in: ServiceCreateInput!) { serviceCreate(input: $in) { id } }""",
        {"in": {
            "projectId": project["id"],
            "environmentId": env,
            "name": SERVICE,
            "source": {"image": f"{IMAGE}:{tag}"},
            "variables": VARIABLES,
        }},
    )["serviceCreate"]["id"]
    gql(
        """mutation($s: String!, $e: String!, $in: ServiceInstanceUpdateInput!) {
             serviceInstanceUpdate(serviceId: $s, environmentId: $e, input: $in) }""",
        {"s": sid, "e": env, "in": {"healthcheckPath": HEALTHCHECK_PATH}},
    )
    gql(
        """mutation($in: ServiceDomainCreateInput!) { serviceDomainCreate(input: $in) { domain } }""",
        {"in": {"serviceId": sid, "environmentId": env}},
    )
    return sid


def set_tag(project: dict, tag: str) -> None:
    sid = service_id(project, SERVICE)
    if not sid:
        raise RailwayError(f"source project has no '{SERVICE}' service")
    gql(
        """mutation($s: String!, $e: String!, $in: ServiceInstanceUpdateInput!) {
             serviceInstanceUpdate(serviceId: $s, environmentId: $e, input: $in) }""",
        {"s": sid, "e": env_id(project), "in": {"source": {"image": f"{IMAGE}:{tag}"}}},
    )


def generate(project: dict) -> dict:
    return gql(
        """mutation($in: TemplateGenerateInput!) {
             templateGenerate(input: $in) { id code name status serializedConfig } }""",
        {"in": {"projectId": project["id"], "environmentId": env_id(project)}},
    )["templateGenerate"]


def check_config(cfg: dict, tag: str) -> list[str]:
    """What the generated template must contain. Returns the problems found."""
    problems = []
    services = {s["name"]: s for s in (cfg or {}).get("services", {}).values()}
    if set(services) != {"Postgres", SERVICE}:
        problems.append(f"services are {sorted(services)}, expected ['Postgres', '{SERVICE}']")
    hg = services.get(SERVICE)
    if hg:
        image = (hg.get("source") or {}).get("image")
        if image != f"{IMAGE}:{tag}":
            problems.append(f"image is {image}, expected {IMAGE}:{tag}")
        got = {k: (v or {}).get("defaultValue", "") for k, v in (hg.get("variables") or {}).items()}
        for name, want in VARIABLES.items():
            if name not in got:
                problems.append(f"variable {name} is missing")
            elif got[name] != want:
                problems.append(f"variable {name} is {got[name]!r}, expected {want!r}")
        if (hg.get("deploy") or {}).get("healthcheckPath") != HEALTHCHECK_PATH:
            problems.append("healthcheck path is not set")
    return problems


def overview() -> str:
    """The overview block in TEMPLATE.md is the template's readme."""
    text = (HERE / "TEMPLATE.md").read_text()
    m = re.search(r"```markdown\n(.*?)\n```\n", text, re.S)
    if not m:
        raise RailwayError("no ```markdown overview block in TEMPLATE.md")
    return m.group(1)


def publish(template_id: str, workspace_id: str) -> dict:
    return gql(
        """mutation($id: String!, $in: TemplatePublishInput!) {
             templatePublish(id: $id, input: $in) { id code status } }""",
        {"id": template_id, "in": {**PUBLISH, "readme": overview(), "workspaceId": workspace_id}},
    )["templatePublish"]


def report(tpl: dict, tag: str) -> bool:
    problems = check_config(tpl.get("serializedConfig"), tag)
    print(f"template id={tpl['id']} code={tpl['code']} status={tpl.get('status')}")
    print(f"url: https://railway.com/new/template/{tpl['code']}")
    if problems:
        print("\nThe generated config does NOT match TEMPLATE.md:")
        for p in problems:
            print("  -", p)
        print("\nFix it in the composer or in this script before publishing.")
        return False
    print("generated config matches TEMPLATE.md")
    return True


# --- commands ----------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("workspaces", help="list the workspaces this token can see")
    for name in ("create", "update"):
        p = sub.add_parser(name)
        p.add_argument("--workspace", required=True)
        p.add_argument("--tag", required=True, help="release tag of the image, e.g. 0.4.1")
        p.add_argument("--publish", action="store_true", help="publish if the config checks out")
        p.add_argument("--show", action="store_true", help="print the generated config")
    p = sub.add_parser("publish")
    p.add_argument("--workspace", required=True)
    p.add_argument("--template", required=True, help="template id printed by create")
    args = ap.parse_args()

    if getattr(args, "tag", None) and not re.fullmatch(r"\d+\.\d+\.\d+", args.tag):
        sys.exit("--tag must be a release like 0.4.1, not 'latest' (see TEMPLATE.md)")

    if args.cmd == "workspaces":
        for w in workspaces():
            print(w["id"], w["name"])
        return

    if args.cmd == "publish":
        print(json.dumps(publish(args.template, args.workspace), indent=2))
        return

    project = find_source_project(args.workspace)
    if args.cmd == "create":
        if project:
            sys.exit(f"'{SOURCE_PROJECT}' already exists ({project['id']}); use update")
        project = create_project(args.workspace)
        print(f"created project {project['id']}")
        add_postgres(project, args.workspace)
        print("added Postgres")
        project = find_source_project(args.workspace)
        add_hivegate(project, args.tag)
        print(f"added {SERVICE} ({IMAGE}:{args.tag})")
    else:
        if not project:
            sys.exit(f"no '{SOURCE_PROJECT}' project in that workspace; run create first")
        set_tag(project, args.tag)
        print(f"{SERVICE} now uses {IMAGE}:{args.tag}")

    project = find_source_project(args.workspace)
    tpl = generate(project)
    if args.show:
        print(json.dumps(tpl["serializedConfig"], indent=2))
    ok = report(tpl, args.tag)
    if ok and args.publish:
        print(json.dumps(publish(tpl["id"], args.workspace), indent=2))
    elif ok:
        print(f"\nnext: publish_template.py publish --workspace {args.workspace} --template {tpl['id']}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    try:
        main()
    except RailwayError as e:
        sys.exit(f"Railway API: {e}")
