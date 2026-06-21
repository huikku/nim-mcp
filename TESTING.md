# Testing nim-mcp

How `nim-mcp` was verified against a live **NIM 7.2.9** instance — and how to re-run it. The server was built
by **live-introspecting** the real API (every response shape below is from an actual call), then exercised
end-to-end through the MCP's own tools.

## Prerequisites
- A reachable NIM instance with the API up: `GET <NIM_URL>/nimAPI.php?q=testAPI` returns
  `{"version": "...","keyValid": ...,"keyRequired": ...}`.
- `.env` filled in (`NIM_URL`, `NIM_API_USER`, `NIM_API_KEY` — key may be blank if NIM is key-optional).
- `pip install -r requirements.txt`.

## 1. Connectivity + identity
```python
import server
server.whoami()
# {'tracker':'nim','version':'7.2.9.260608','keyValid':'false','keyRequired':'false',
#  'user':{'ID':1,'username':'nim',...},'url':'http://...'}
```

## 2. Schema reads
```python
server.list_task_types()      # [{'ID':4,'name':'ANIM','short_name':'ANI','folder':'ANIM'}, ...]
server.list_statuses("task")  # NOT STARTED / IN PROGRESS / ON HOLD / REVIEW / KICKBACK / CBB / COMPLETED / OMIT
server.list_users()           # [{'ID':1,'username':'nim',...}]
```

## 3. Seed a project (CRUD) — the NIGHTJAR fixture
```python
job   = server.new_job("NIGHTJAR", status="IN PROGRESS")     # -> {'ID':'1',...}
show  = server.new_show(1, "SEQ010")                          # -> {'ID':'1',...}
shot  = server.new_shot(1, "SH010")                           # -> {'ID':'1',...}
asset = server.new_asset(1, "HERO_CHAR")                      # -> {'ID':'1',...}
task  = server.new_task("shot", 1, "ANIM", user="nim")        # -> {'ID':'1',...}
```

## 4. Two-level dry-run (writes nothing)
```python
server.new_show(1, "SEQ020", dry_run="plan")
#   {'dry_run':'plan','function':'addShow','params':{'jobID':1,'name':'SEQ020'}}
server.new_show(1, "SEQ020", dry_run="preflight")
#   {... 'checks': {'job_exists': True}}
```

## 5. Status round-trip (the canonical-mapping proof)
```python
server.set_task_status(1, "REVIEW")           # {'ID':'1','success':True,'error':''}
server.project_summary(1)["shots"]["SH010"]["tasks"]   # {'ANIM': 'review'}   <- canonical
server.set_task_status(1, "NOT STARTED")      # reset -> 'todo'
```
> This caught a real bug during the build: NIM's status write param is **`taskStatusID`** (camelCase), not
> `task_status_ID` — the wrong name returned `success:true` but silently did nothing. The round-trip through
> `project_summary` is what surfaced it.

## 6. The cross-tracker contract
```python
server.project_summary(1)
# { "tracker":"nim", "project":{"name":"NIGHTJAR","id":1,"code":"26000"},
#   "counts":{"sequences":1,"assets":1,"shots":1,"tasks":1}, ... }
```
This is the identical shape the [hub](https://github.com/huikku/tracker-mcp-hub) `verify` / `migrate` / `rollup`
consume — so a NIM summary diffs against a ShotGrid/Kitsu/AYON one with zero special-casing.

## Status
- ✅ API + data model **live-verified** (NIM 7.2.9): reads, full CRUD, two-level dry_run, status round-trip,
  normalized `project_summary`.
- ⏭ Cross-tracker migration **edges** (NIM ↔ the other four through the hub) are the next step.
