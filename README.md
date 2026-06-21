# nim-mcp

A standardized **MCP server for [NIM Labs](https://nim-labs.com/)** — the 5th tracker in the
[tracker-MCP](https://github.com/huikku/tracker-mcp-hub) family. It exposes NIM through the **same normalized
contract** as [`shotgrid-mcp`](https://github.com/huikku/shotgrid-mcp),
[`ftrack-mcp`](https://github.com/huikku/ftrack-mcp), [`kitsu-mcp`](https://github.com/huikku/kitsu-mcp) and
[`ayon-mcp`](https://github.com/huikku/ayon-mcp), so an agent (or the [hub](https://github.com/huikku/tracker-mcp-hub))
can read, verify, migrate and roll up NIM projects alongside the other four.

No `nim_core` SDK dependency — it drives the **NIM HTTP API** (`/nimAPI.php`) directly over REST.

## NIM data model
```
Job (the project)  ──►  Shows (~sequence/episode tier)  ──►  Shots  ──►  Tasks ──► Files/Versions · Elements · Renders · Review items
                   └─►  Assets                                        └─►  Tasks
```
NIM is the only tracker with a fixed **three-level** hierarchy (`Job → Show → Shot`); a **Job** is the
project container, Assets hang off the Job. It's also the strongest reference for **bidding / scheduling /
timecards** (the job lifecycle literally starts at `BIDDING`). See [COMPARISON.md](COMPARISON.md) for the
full 5-way mapping.

## Install
```bash
pip install -r requirements.txt     # fastmcp + requests
cp .env.example .env                 # then fill in the values
```

## Configure (`.env`)
```ini
NIM_URL=http://your-nim-host          # the NIM VM/app host; the API is <NIM_URL>/nimAPI.php
NIM_API_USER=yourbot                  # a NIM username (or numeric user id)
NIM_API_KEY=...                       # per-user key (Admin ▸ Security ▸ Options); may be blank if key-optional
```
Auth is sent as `X-NIM-API-USER` / `X-NIM-API-KEY` headers. NIM can run **key-optional** (the API is open) —
in that case reads work with a blank key; enabling keys is recommended for anything networked.

### Register with Claude Code
```bash
claude mcp add nim -- python3 /path/to/nim-mcp/server.py
```

## Tools (25)
| Tool | NIM function(s) |
|---|---|
| `whoami` | `testAPI` + `getUsers` — version, key validity, resolved user |
| `list_jobs` | `findJobs` (all) / `getUserJobs` (a user's assigned) |
| `get` | `get{Job,Show,Shot,Asset,Task}Info` |
| `list_shows` · `list_shots` · `list_assets` | `getShows` / `getShots` / `getAssets` (parent `ID=`) |
| `list_tasks` | `getTaskInfo&class=shot\|asset&itemID=` |
| `list_task_types` · `list_statuses` · `list_users` | `getTaskTypes` / `get*Statuses` / `getUsers` |
| `new_job` · `new_show` · `new_shot` · `new_asset` · `new_task` | `addJob` / `addShow` / `addShot` / `addAsset` / `addTask` |
| `update_task` · `set_task_status` | `updateTask` (`taskStatusID`, validated by name) |
| `add_render` · `add_element` · `add_file` · `list_versions` | `addRender` / `addElement` / `addFile` / `getVersions` |
| `upload_review` · `log_time` | `uploadReviewItem` / `addTimecards` |
| `nim_call` | escape hatch — any `q=` function + params |
| `project_summary` | the normalized cross-tracker snapshot (below) |

Every `new_*` / `update_*` / `set_*` tool takes **`dry_run`**: `"plan"` (echo the call, no server contact) or
`"preflight"` (resolve parents + validate the status against the live list, write nothing).

## The cross-tracker contract — `project_summary`
Identical shape to the other four servers (this is what the hub consumes):
```jsonc
{
  "tracker": "nim",
  "project": { "name": "NIGHTJAR", "id": 1, "code": "26000" },
  "counts":  { "sequences": 1, "assets": 1, "shots": 1, "tasks": 1 },
  "sequences": ["SEQ010"],
  "shots":  { "SH010": { "sequence": "SEQ010", "tasks": { "ANIM": "review" } } },
  "assets": { "HERO_CHAR": { "tasks": {} } }
}
```
NIM's per-entity statuses map to the canonical `todo / wip / done / review / approved`.

## NIM quirks this server normalizes
- **Inconsistent envelopes** — bare array · `{success,error,data}` · `{ID,success}` (writes) · `[{success:false,error}]`.
- **Generic list params** — `getShots&ID=<showID>`, `getShows&ID=<jobID>`, `getAssets&ID=<jobID>`; there is no
  `getTasks` (a parent's tasks come from `getTaskInfo&class=…&itemID=…`).
- **Field-name gotchas** — Shows carry `showname`; task-status writes use `taskStatusID` (camelCase).
- **`getUserJobs` lists only *assigned* jobs** — `list_jobs` uses `findJobs` for the full list.

All verified live against **NIM 7.2.9**.

---
*Part of the tracker-MCP quintet — [hub](https://github.com/huikku/tracker-mcp-hub) ·
[shotgrid](https://github.com/huikku/shotgrid-mcp) · [ftrack](https://github.com/huikku/ftrack-mcp) ·
[kitsu](https://github.com/huikku/kitsu-mcp) · [ayon](https://github.com/huikku/ayon-mcp). MIT ·
[John Huikku](https://alienrobot.com).*
