#!/usr/bin/env python3
"""
nim-mcp — a standardized MCP server for NIM Labs (the 5th tracker).

Mirrors shotgrid-mcp / ftrack-mcp / kitsu-mcp / ayon-mcp: generic reads + typed
helpers + a two-level dry_run (plan / preflight) + a normalized `project_summary`
that emits the SAME cross-tracker contract the hub consumes.

Transport: stdio.  Backend: the NIM HTTP API (`/nimAPI.php?q=<function>`), driven
over plain REST (no nim_core SDK dependency).  Auth: per-user headers
`X-NIM-API-USER` / `X-NIM-API-KEY` (sent when set; NIM may also run key-optional).

NIM data model (Job -> Show -> Shot -> Task ; Job -> Asset -> Task):
    Job (the project)  ->  Shows (~sequence/episode tier)  ->  Shots  ->  Tasks
                       ->  Assets                                       ->  Tasks
Quirks handled here (all live-verified against NIM 7.2.9):
  * response envelopes are inconsistent: bare list | {success,error,data} |
    {ID,success,error} (writes) | [{success:false,error}].
  * list endpoints use a generic `ID=<parentID>` (getShots&ID=<showID>,
    getShows&ID=<jobID>, getAssets&ID=<jobID>); there is no getTasks — a parent's
    tasks come from getTaskInfo&class=shot|asset&itemID=<id>.
  * shows carry `showname` (not `name`).

Env: NIM_URL (e.g. http://192.168.122.220), NIM_API_USER, NIM_API_KEY.
"""
import os
import requests
from fastmcp import FastMCP

# load a sibling .env if present (explicit environment still wins) — no dependency
_envf = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_envf):
    for _l in open(_envf):
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            _k, _v = _l.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

NIM_URL = os.environ.get("NIM_URL", "").rstrip("/")
NIM_USER = os.environ.get("NIM_API_USER", "")
NIM_KEY = os.environ.get("NIM_API_KEY", "")

mcp = FastMCP("nim")

# ---------------------------------------------------------------- REST client
def _headers():
    h = {}
    if NIM_USER:
        h["X-NIM-API-USER"] = NIM_USER
    if NIM_KEY:
        h["X-NIM-API-KEY"] = NIM_KEY
    return h


def _clean(params):
    return {k: v for k, v in (params or {}).items() if v is not None}


def _unwrap(j, q):
    """Normalize NIM's inconsistent envelopes to the payload; raise on error."""
    if isinstance(j, dict):
        if j.get("success") is False:
            raise RuntimeError(f"NIM {q}: {j.get('error') or 'error'}")
        if "data" in j:
            return j["data"]
        return j
    if isinstance(j, list):
        if len(j) == 1 and isinstance(j[0], dict) and j[0].get("success") is False:
            raise RuntimeError(f"NIM {q}: {j[0].get('error') or 'error'}")
        return j
    return j


def _api(q, params=None, method="GET", files=None):
    if not NIM_URL:
        raise RuntimeError("NIM_URL is not set (point it at http://<nim-host>).")
    url = f"{NIM_URL}/nimAPI.php"
    p = _clean(params)
    p["q"] = q
    if method == "GET":
        r = requests.get(url, params=p, headers=_headers(), timeout=30)
    else:
        r = requests.post(url, params={"q": q}, data=_clean(params),
                          files=files, headers=_headers(), timeout=180)
    r.raise_for_status()
    try:
        j = r.json()
    except ValueError:
        # NIM returns a JS redirect (not JSON) for some failures, e.g. missing license
        raise RuntimeError(f"NIM {q}: non-JSON response — {r.text[:160]}")
    return _unwrap(j, q)


def _one(rows):
    """NIM *Info calls return a 1-element list; unwrap to the dict."""
    if isinstance(rows, list):
        return rows[0] if rows else {}
    return rows


def _id(res):
    """Pull the new ID out of a write response ({ID,success,...} or [{...}])."""
    res = _one(res) if isinstance(res, list) else res
    return res.get("ID") if isinstance(res, dict) else None


# ---------------------------------------------------------------- status canon
# cross-tracker contract: todo / wip / done / review / approved
_CANON = {
    "NOT STARTED": "todo", "ON HOLD": "todo", "OMIT": "todo",
    "BIDDING": "todo", "NOT AWARDED": "todo", "AWARDED": "todo",
    "IN PROGRESS": "wip", "KICKBACK": "wip", "CBB": "wip", "WIP": "wip",
    "REVIEW": "review", "PENDING REVIEW": "review",
    "APPROVED": "approved", "FINAL": "approved",
    "COMPLETED": "done", "CLOSED": "done", "DONE": "done",
}


def _canon(name):
    if not name:
        return "todo"
    return _CANON.get(str(name).strip().upper(), str(name).strip().lower())


# ---------------------------------------------------------------- helpers
def _uid(user=None):
    """Resolve a user to a NIM userID (accepts an int id, a username, or env default)."""
    val = user if user not in (None, "") else NIM_USER
    if val in (None, ""):
        return 1  # NIM ROOT default
    if str(val).isdigit():
        return int(val)
    for u in _api("getUsers"):
        if str(u.get("username", "")).lower() == str(val).lower():
            return u["ID"]
    return 1


_STATUS_FN = {
    "job": "getJobStatuses", "shot": "getShotStatuses",
    "asset": "getAssetStatuses", "task": "getTaskStatuses",
}
_INFO_FN = {
    "job": "getJobInfo", "show": "getShowInfo", "shot": "getShotInfo",
    "asset": "getAssetInfo", "task": "getTaskInfo",
}


def _task_status_id(name):
    """Map a task-status name to its NIM ID (for updateTask)."""
    want = str(name).strip().upper()
    for s in _api("getTaskStatuses"):
        if str(s.get("status", "")).upper() == want:
            return s["ID"]
    return None


def _task_type_id(name):
    want = str(name).strip().upper()
    for t in _api("getTaskTypes"):
        if str(t.get("name", "")).upper() == want or str(t.get("short_name", "")).upper() == want:
            return t["ID"]
    return None


def _dry(dry_run, func, params, preflight=None):
    """Two-level dry_run: 'plan' echoes the call; 'preflight' resolves+validates."""
    if dry_run == "plan":
        return {"dry_run": "plan", "function": func, "params": _clean(params)}
    if dry_run == "preflight":
        return {"dry_run": "preflight", "function": func,
                "params": _clean(params), "checks": (preflight() if preflight else {})}
    return _api(func, params)


# ================================================================ TOOLS
def whoami():
    """Confirm connectivity + identity: NIM version, key validity, and the resolved API user."""
    test = _one(_api("testAPI"))
    users = _api("getUsers")
    me = next((u for u in users if u["ID"] == _uid()), users[0] if users else {})
    return {"tracker": "nim", "version": test.get("version"),
            "keyValid": test.get("keyValid"), "keyRequired": test.get("keyRequired"),
            "user": me, "url": NIM_URL}


def list_jobs(user: str = None):
    """List jobs (projects). With `user`, returns that user's assigned jobs; otherwise all jobs."""
    if user is not None:
        return _api("getUserJobs", {"u": _uid(user)})
    return _api("findJobs", {"getData": 1})


def get(entity: str, id: int):
    """Read a single entity's full record. entity = job|show|shot|asset|task."""
    fn = _INFO_FN.get(entity)
    if not fn:
        raise ValueError(f"entity must be one of {list(_INFO_FN)}")
    return _one(_api(fn, {"ID": id}))


def list_shows(job: int):
    """List Shows (the sequence/episode tier) under a Job."""
    return _api("getShows", {"ID": job})


def list_shots(show: int):
    """List Shots under a Show."""
    return _api("getShots", {"ID": show})


def list_assets(job: int):
    """List Assets under a Job."""
    return _api("getAssets", {"ID": job})


def list_tasks(parent_class: str, parent_id: int):
    """List Tasks on a shot or asset. parent_class = shot|asset."""
    return _api("getTaskInfo", {"class": parent_class, "itemID": parent_id})


def list_task_types():
    """The studio task-type library (ANIM, COMP, LAYOUT, ...)."""
    return _api("getTaskTypes")


def list_statuses(entity: str = "task"):
    """Status list for an entity. entity = job|shot|asset|task."""
    fn = _STATUS_FN.get(entity)
    if not fn:
        raise ValueError(f"entity must be one of {list(_STATUS_FN)}")
    return _api(fn)


def list_users():
    """All NIM users."""
    return _api("getUsers")


def new_job(name: str, status: str = None, dry_run: str = None):
    """Create a Job (project). Optional job status name (e.g. BIDDING, IN PROGRESS)."""
    params = {"name": name}
    if status:
        params["jobStatus"] = status
    return _dry(dry_run, "addJob", params)


def new_show(job: int, name: str, dry_run: str = None):
    """Create a Show (sequence/episode tier) under a Job."""
    return _dry(dry_run, "addShow", {"jobID": job, "name": name},
                preflight=lambda: {"job_exists": bool(get("job", job))})


def new_shot(show: int, name: str, dry_run: str = None):
    """Create a Shot under a Show."""
    return _dry(dry_run, "addShot", {"showID": show, "name": name},
                preflight=lambda: {"show_exists": bool(_api("getShowInfo", {"ID": show}))})


def new_asset(job: int, name: str, dry_run: str = None):
    """Create an Asset under a Job."""
    return _dry(dry_run, "addAsset", {"jobID": job, "name": name},
                preflight=lambda: {"job_exists": bool(get("job", job))})


def new_task(parent_class: str, parent_id: int, task_type: str,
             user: str = None, dry_run: str = None):
    """Create a Task on a shot or asset. parent_class = shot|asset; task_type by name (ANIM, COMP...)."""
    tt = _task_type_id(task_type)
    key = "shotID" if parent_class == "shot" else "assetID"
    params = {key: parent_id, "taskTypeID": tt, "userID": _uid(user)}
    return _dry(dry_run, "addTask", params,
                preflight=lambda: {"task_type_resolved": tt,
                                   "valid_type": tt is not None})


def update_task(task: int, status: str = None, description: str = None, dry_run: str = None):
    """Patch a Task: status (by name) and/or description."""
    params = {"taskID": task}
    sid = _task_status_id(status) if status else None
    if sid is not None:
        params["taskStatusID"] = sid
    if description is not None:
        params["description"] = description
    return _dry(dry_run, "updateTask", params,
                preflight=lambda: {"status_resolved": sid,
                                   "valid_status": (status is None or sid is not None)})


def set_task_status(task: int, status: str, dry_run: str = None):
    """Set a Task's status by name (validated against the live task-status list)."""
    return update_task(task, status=status, dry_run=dry_run)


def add_render(task: int, name: str, dry_run: str = None):
    """Register a Render under a Task."""
    return _dry(dry_run, "addRender", {"taskID": task, "renderName": name})


def add_element(parent_class: str, parent_id: int, type_id: int,
                path: str, name: str, dry_run: str = None):
    """Register an Element (geometry/texture/...) under a parent (shot|asset|task|render)."""
    return _dry(dry_run, "addElement",
                {"parent": parent_class, "parentID": parent_id,
                 "typeID": type_id, "path": path, "name": name})


def add_file(item_class: str, item_id: int, task_type: str, user: str,
             basename: str, filename: str, path: str, server_id: int = 1, dry_run: str = None):
    """Register a published File/Version. item_class = shot|asset."""
    tt = _task_type_id(task_type)
    folder = next((t.get("folder") for t in _api("getTaskTypes")
                   if t["ID"] == tt), "")
    return _dry(dry_run, "addFile",
                {"class": item_class, "itemID": item_id, "task_type_ID": tt,
                 "task_type_folder": folder, "userID": _uid(user),
                 "basename": basename, "filename": filename,
                 "path": path, "serverID": server_id})


def list_versions(item_class: str, item_id: int, basename: str):
    """List versions for an item (shot|asset) + basename."""
    return _api("getVersions", {"class": item_class, "itemID": item_id, "basename": basename})


def upload_review(item_id: int, item_type: str, name: str, dry_run: str = None):
    """Create a review item (dailies) on a task/shot/asset. item_type = task|shot|asset."""
    return _dry(dry_run, "uploadReviewItem",
                {"itemID": item_id, "itemType": item_type, "name": name})


def log_time(task: int, user: str, hours: float, date: str, dry_run: str = None):
    """Log a timecard against a task (date = YYYY-MM-DD)."""
    return _dry(dry_run, "addTimecards",
                {"taskID": task, "userID": _uid(user), "hours": hours, "date": date})


def nim_call(q: str, params: dict = None):
    """Escape hatch: call any NIM API function directly (q + params)."""
    return _api(q, params or {})


def project_summary(job: int):
    """
    The cross-tracker contract: normalize a NIM Job into the SAME shape every
    tracker MCP emits, so the hub can verify/migrate/rollup across all of them.
      Job->project · Shows->sequences · Shots->shots · Assets->assets · Tasks->tasks
      task status -> canonical todo/wip/done/review/approved
    """
    hdr = get("job", job)
    shows = _api("getShows", {"ID": job})
    assets_raw = _api("getAssets", {"ID": job})

    shots, ntasks = {}, 0
    seqs = []
    for sh in shows:
        sid, sname = sh.get("ID"), sh.get("showname") or sh.get("name")
        seqs.append(sname)
        for shot in _api("getShots", {"ID": sid}):
            tasks = {}
            for t in _api("getTaskInfo", {"class": "shot", "itemID": shot["ID"]}):
                tasks[t.get("taskName")] = _canon(t.get("task_status"))
                ntasks += 1
            shots[shot.get("name")] = {"sequence": sname, "tasks": tasks}

    assets = {}
    for a in assets_raw:
        tasks = {}
        for t in _api("getTaskInfo", {"class": "asset", "itemID": a["ID"]}):
            tasks[t.get("taskName")] = _canon(t.get("task_status"))
            ntasks += 1
        assets[a.get("name")] = {"tasks": tasks}

    return {
        "tracker": "nim",
        "project": {"name": hdr.get("jobname"), "id": hdr.get("ID"),
                    "code": hdr.get("number")},
        "counts": {"sequences": len(shows), "assets": len(assets),
                   "shots": len(shots), "tasks": ntasks},
        "sequences": seqs,
        "shots": shots,
        "assets": assets,
    }


_TOOLS = [
    whoami, list_jobs, get, list_shows, list_shots, list_assets, list_tasks,
    list_task_types, list_statuses, list_users,
    new_job, new_show, new_shot, new_asset, new_task,
    update_task, set_task_status, add_render, add_element, add_file,
    list_versions, upload_review, log_time, nim_call, project_summary,
]
for _fn in _TOOLS:
    mcp.tool(_fn)


if __name__ == "__main__":
    mcp.run()
