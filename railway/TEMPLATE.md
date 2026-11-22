# Publishing HiveGate as a Railway template

The **Deploy on Railway** button deploys straight from this repo
(`railway.app/template?template=https://github.com/hivegate-ai/deploy`). That works, but
it isn't a *published* template. It doesn't appear in Railway's marketplace, users have to
bring their own Postgres and fill in every variable, and it earns no
[kickback](https://docs.railway.com/templates/kickbacks). A published template pays 15% of
the usage it generates, 25% if we answer users' questions in the template queue (or if there
are none).

This page is the recipe. Creating and publishing happen in the Railway dashboard, in the
account that should own the template.

## 1. Create the template

Workspace **Settings → Templates → New Template**
([docs](https://docs.railway.com/templates/create)). Add two services.

### Service 1: Postgres

Add Railway's **PostgreSQL** database and leave its name as **`Postgres`**: the variables
below reference it by that name. HiveGate needs no extensions. It creates its tables on
first start.

### Service 2: HiveGate

| Setting | Value |
|---|---|
| Source | **Docker Image** `ghcr.io/hivegate-ai/hivegate:0.4.1` |
| Name | `hivegate` |
| Public networking | **HTTP**, generate a domain (the app listens on `$PORT`, which Railway sets) |
| Healthcheck path | `/health` |
| Start command | leave empty (the image runs `scripts/start_server.sh`) |

Pin a release tag rather than `latest`. `latest` follows `main`, which carries unreleased
work, and an upgrade between releases may need `hivegate migrate`. When a new release
ships, update the tag in the template.

### HiveGate variables

| Variable | Value | Why |
|---|---|---|
| `DB_DRIVER` | `postgresql+psycopg` | |
| `DB_HOST` | `${{Postgres.PGHOST}}` | private network host |
| `DB_PORT` | `${{Postgres.PGPORT}}` | |
| `DB_USER` | `${{Postgres.PGUSER}}` | |
| `DB_PASS` | `${{Postgres.PGPASSWORD}}` | |
| `DB_DATABASE` | `${{Postgres.PGDATABASE}}` | |
| `WAIT_FOR_DB` | `true` | **Required.** On first start the app creates its tables, but it logs and swallows a connection error. If Postgres isn't up yet, the app starts with no tables. This makes the entrypoint wait for `DB_HOST:DB_PORT` first. |
| `ADMIN_SECRET` | `${{secret(48)}}` | for `/admin/*`, generated per deploy |
| `SECRET_TOKEN_ENC_KEY` | `${{secret(43, "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")}}=` | a Fernet key (url-safe base64 of 32 bytes), needed to store OAuth tokens. Checked: 2,000 keys of this shape all encrypt and decrypt. |
| `PROMPT_STORAGE_BACKEND` | `postgres` | |
| `GOOGLE_API_KEY` | *(empty: the deployer fills it in)* | the default model is Gemini |
| `DEFAULT_CHAT_MODEL` | `gemini-3-flash-preview` | a deployer who uses another provider sets its key in Variables after deploy and changes this, e.g. `anthropic:sonnet-latest` |

The Postgres variable names (`PGHOST`, `PGPORT`, …) are the ones Railway's PostgreSQL
service exposes. Confirm them in the composer's autocomplete when you type `${{Postgres.`.

## 2. Publish

Click **Publish** and fill in the form
([docs](https://docs.railway.com/templates/publish-and-share)). Suggested values:

- **Name:** HiveGate
- **Short description:** Run your Agno agents as a production REST API: per-user sessions
  and OAuth, human approval before writes, multi-agent teams.
- **Category:** AI/ML (or the closest one offered)
- **Icon:** the HiveGate logo from hivegate.dev
- **Overview:** paste the block below.

```markdown
# HiveGate

HiveGate is an open-source runtime that serves your [Agno](https://github.com/agno-agi/agno)
agents as a production REST API. MIT licensed.

- **Per-user sessions and OAuth.** Each of your users gets their own memory and their own
  connected accounts.
- **Human approval.** A tool call that writes can wait for a person to approve it.
- **Multi-agent teams**, MCP servers and usage metering per user and per agent.

## What this template deploys

- **HiveGate**, the prebuilt image `ghcr.io/hivegate-ai/hivegate`, with a public URL.
- **PostgreSQL** for agents, sessions, API keys and usage. HiveGate creates its tables on
  first start.

An admin secret and a token-encryption key are generated for you. You only add a model
key: `GOOGLE_API_KEY` for the default Gemini model. To use OpenAI, Anthropic, xAI, Z.ai or
DeepSeek instead, add that key in Variables and set `DEFAULT_CHAT_MODEL`.

## After deploy

1. Open `https://<your-domain>/docs` for the interactive API.
2. Create an API key with the admin secret (it's in the hivegate service's Variables):

       curl -X POST https://<your-domain>/admin/api-keys \
         -H "X-Admin-Secret: $ADMIN_SECRET" -H "Content-Type: application/json" \
         -d '{"name": "my-app"}'

3. Call the v2 API with `X-API-Key: <the key>`.

Docs: https://hivegate.dev · Source: https://github.com/hivegate-ai/hivegate
```

## 3. Test before announcing it

Deploy the published template into a scratch project and run the
[smoke test](../DEPLOY_TEST_PLAN.md#smoke-test-identical-for-all-three), using the API key
from step 2 of the overview instead of `AUTH_DISABLED`. Delete the project afterwards.

## 4. Point the buttons at it

Publishing gives a template URL of the form `https://railway.com/new/template/<code>`.
Replace the old `railway.app/template?template=https://github.com/hivegate-ai/deploy` link
with it, and the button image with `https://railway.com/button.svg`, in:

- `hivegate-ai/hivegate` `README.md` (the button row and the deploy table)
- this repo's `README.md` and `DEPLOY_TEST_PLAN.md`
- hivegate.dev: `hivegate-ai/site` `_includes/deploy-buttons.html`, plus any links in `deploy.md` and `index.md`

## 5. Keep it earning

The extra 10% comes from answering questions in the template's **Template Queue** on
Railway. Railway pays the full 25% when no questions come in. Check the queue when you check
GitHub issues.
