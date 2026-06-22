# Tracker comparison — ShotGrid · ftrack · Kitsu · AYON · NIM

These five MCP servers ([`shotgrid-mcp`](https://github.com/huikku/shotgrid-mcp),
[`ftrack-mcp`](https://github.com/huikku/ftrack-mcp), [`kitsu-mcp`](https://github.com/huikku/kitsu-mcp),
[`ayon-mcp`](https://github.com/huikku/ayon-mcp), [`nim-mcp`](https://github.com/huikku/nim-mcp)) expose the
**same shape** so an agent can read a project from one tracker and recreate it in another. This doc compares
the servers, shows how the platforms' data models line up, and — most importantly — lists the
**incompatibilities you hit when migrating between them**. The three original trackers were exercised in
**live round-trip tests** (all six directions); **AYON** and **NIM** are verified against live installs too
(read + CRUD + normalized `project_summary`). All five are confirmed to emit the **identical contract** —
proven by pulling real `project_summary`s from each (from a 4-shot ftrack demo to a 1,200-shot Kitsu feature)
and normalizing them into one shape with zero per-tracker special-casing.

## 1. The MCP servers
| | shotgrid-mcp | ftrack-mcp | kitsu-mcp | ayon-mcp | nim-mcp |
|---|---|---|---|---|---|
| Platform | ShotGrid / Flow Production Tracking | ftrack Studio | Kitsu (CGWire) | AYON (Ynput) | **NIM (NIM Labs)** |
| Backing SDK | `shotgun_api3` | `ftrack_api` | `gazu` → Zou REST | `ayon-python-api` | **plain REST over `/nimAPI.php`** (no SDK) |
| Auth | script name + API key | API user + API key | email + password | server URL + API key | **per-user API key headers** (`X-NIM-API-USER`/`KEY`), key-optional |
| Tools | 16 | 31 | 30 | 20 | **25** |
| API shape | generic CRUD over a schema | generic query + CRUD | generic REST + typed helpers | generic CRUD over entities | **function-per-entity REST + typed helpers** |
| Write safety | `dry_run` on **every** write | `dry_run` on **every** write | `dry_run` on **every** write | `dry_run` on **every** write | `dry_run` on **every** write |
| Entity ids | **integer** | UUID string | UUID string | UUID string | **integer** |
| Open source | platform: no | platform: no | platform: **yes** | platform: **yes** (AGPL) | platform: **no** (self-hosted VM) |
| License (MCP) | MIT | MIT | MIT | MIT | MIT |

> **Dry-run has two levels** (on `create`/`update`/`set_status`, kubectl-style): `dry_run="plan"`
> = client-side echo (no server contact); `dry_run="preflight"` = a *real* dry run — resolves every
> reference against live data, validates statuses against the schema, returns a before→after diff and an
> `ok`/`would_fail` verdict, **writing nothing**. It's high-confidence preflight, not transactional
> simulation — some errors only surface on the real commit.

## 2. Data-model mapping (how the concepts line up)
| Concept | ShotGrid | ftrack | Kitsu | AYON | NIM |
|---|---|---|---|---|---|
| Project | `Project` | `Project` (needs a **schema**) | `Project` | `Project` (+ **anatomy**) | **`Job`** |
| Sequence | `Sequence` | `Sequence` | `Sequence` | `Folder` (`folder_type=Sequence`) | **`Show`** (an extra tier) |
| Shot | `Shot` | `Shot` (parent = Sequence) | `Shot` | `Folder` (`folder_type=Shot`) | `Shot` (under a Show) |
| Asset | `Asset` (string type) | `AssetBuild` (typed) | `Asset` | `Folder` (`folder_type=Asset`) | `Asset` (under the Job) |
| Task | `Task` (+ **`Step`**) | `Task` (+ **`Type`**) | `Task` (+ `task_type`) | `Task` (+ `task_type`) | `Task` (+ task **type**) |
| Task status | `sg_status_list` | `Status` (schema-scoped) | `task_status` | `Status` (with a `state`) | `task_status` (per-entity configurable) |
| **Publish** | `PublishedFile` + `Version` | `AssetVersion` + `Component` | preview files on tasks | `Product` → `Version` → `Representation` | **`Element` / `File` / `Render`** (typed, under a task) |
| Casting (asset→shot) | `Shot.assets` (multi-entity) | **— none** | **breakdown / casting** (first-class) | **typed links** (`breakdown\|folder\|folder`) | **— none** |
| Custom fields | `sg_*` schema fields | custom attributes | metadata-descriptors | **Attributes** (typed, inheriting) | **Custom Keys** (typed) |
| Hierarchy | flat + links | strict parent tree | project → entity tree | **polymorphic folders** (any type, any nesting) | **Job → Show → Shot** (+ Job → Asset) |

> **Two structural outliers, opposite directions.** **AYON** collapses the hierarchy — a `Folder` with a
> configurable `folder_type` is the only nesting entity (Episode/Sequence/Shot/Asset are just types). **NIM**
> *adds* a tier — it inserts a **`Show`** between the project (`Job`) and the `Shot`, so its hierarchy is
> `Job → Show → Shot`, the only tracker with three fixed levels. A NIM **Job** is the project container;
> Assets hang off the Job, not the Show.

## 3. Status vocabularies — **no 1:1; you must map**
Each platform ships a different status set, so migration has to translate (NIM statuses are per-entity
configurable; the MCP maps by name → canonical):

| Meaning | Kitsu | ShotGrid | ftrack (VFX) | AYON (defaults) | NIM (defaults) |
|---|---|---|---|---|---|
| not started | `todo` | `wtg` | `Not started` | `Not ready` | `NOT STARTED` |
| ready | `ready` | `wtg` | `Ready to start` | `Ready to start` | *(none)* → `NOT STARTED` |
| in progress | `wip` | `ip` | `In progress` | `In progress` | `IN PROGRESS` |
| done / final | `done` | `fin` | *(none)* → `In progress` | *(none)* → `Approved` | `COMPLETED` |
| waiting for approval | `wfa` | `rev` | `Pending Review` | `Pending review` | `REVIEW` |
| approved | `approved` | `apr` | *(none)* → `Pending Review` | `Approved` | *(none)* → `COMPLETED` |
| retake / revise | `retake` | `rev` | `Revise` | *(none)* → `On hold` | `KICKBACK` |

> ⚠️ Like ftrack and AYON, **NIM's default task set has no distinct "Approved"** separate from `COMPLETED`,
> and adds its own states (`CBB` "could be better", `OMIT`). Round-tripping terminal/approval states is lossy
> unless you add matching statuses first. NIM's **job** lifecycle is bidding-first
> (`BIDDING → NOT AWARDED → AWARDED → IN PROGRESS → COMPLETED → CLOSED`).

## 4. Migration incompatibilities & gotchas
- **Casting can't round-trip through ftrack *or* NIM.** First-class in **Kitsu** (breakdown) and **ShotGrid**
  (`Shot.assets`); **AYON** models it as **typed links**; **ftrack and NIM have no asset→shot casting** — it
  drops when either is on the path.
- **NIM's extra Show tier.** A `Project → Sequence → Shot` source maps cleanly to `Job → Show → Shot`
  (Show ≙ Sequence). Going the other way, trackers without a Sequence tier need a synthetic Show. AYON's
  arbitrary folders flatten onto Job/Show/Shot only after choosing which folder level is the "Show".
- **AYON's polymorphic folders cut both ways** — importing fixed hierarchies *into* AYON is clean; exporting
  arbitrary trees *out* to a fixed Seq→Shot (or Job→Show→Shot) tracker can need flattening; Representations
  collapse to one file.
- **Task types aren't universal.** Kitsu's `Storyboard`, NIM's `CONFORM`/`CBB`, etc. have no equivalent in
  ftrack's VFX schema — map or pre-create task types first.
- **Asset types differ.** SG `sg_asset_type` is a free string; ftrack `AssetBuild` requires a type from a
  fixed list; Kitsu asset types are named entities; AYON uses `folder_type` + product types; **NIM assets are
  flat under the Job** (no required type taxonomy).
- **Project creation quirks:**
  - **ShotGrid:** API-created projects have no UI pages unless `layout_project` is set at creation.
  - **ftrack:** a project requires a schema (VFX / Animation / …) at creation.
  - **Kitsu:** a project can only be deleted once *closed*.
  - **AYON:** addon features need a *bundle*; the core tracker works without one.
  - **NIM:** a `Job` needs only a name; **`getUserJobs` lists only *assigned* jobs** — use `findJobs` for all.
    The platform is a **self-hosted VM** (VirtualBox/VMware appliance), licensed per file.
- **Name uniqueness:** ftrack enforces case-insensitive, per-parent uniqueness. SG, Kitsu, AYON, NIM are more permissive.
- **Inconsistent response envelopes (NIM):** the HTTP API mixes bare arrays, `{success,error,data}`, and
  `{ID,success}` write responses — the MCP normalizes them. Status writes use `taskStatusID` (camelCase);
  list endpoints take a generic `ID=<parentID>`.
- **Ids:** ShotGrid and **NIM** use **integer** ids; ftrack, Kitsu, AYON use **UUID strings** — don't assume a type.

## 5. What these migrations carry today — and what they don't
| Data | Migrates? |
|---|---|
| Project / Sequence / Shot / Asset / Task structure | ✅ all directions (SG/ftrack/Kitsu); AYON (folders) & NIM (Job→Show→Shot) via their MCPs |
| Task **statuses** (mapped per §3) | ✅ (lossy into ftrack / AYON / NIM terminal states) |
| **Casting** (asset→shot) | ✅ SG ↔ Kitsu; AYON via links; ❌ via ftrack **or NIM** |
| Frame ranges / cut durations | ✅ (where the field exists; NIM shots carry frames/handles/fps) |
| **Thumbnails / Versions** (review media) | ✅ SG → Kitsu; ftrack via media; AYON via `Version`/`Representation`; NIM via `File`/review items |
| **Notes / comments** | ✅ SG → Kitsu; AYON activity feed; NIM review items / dailies |
| **Custom fields** | ✅ SG → Kitsu; AYON **Attributes** (inheriting); NIM **Custom Keys** |
| **Publishes** | ✅ references (path / version / type / deps); AYON `Product→Version→Representation`; NIM `Element/File/Render` |
| **Bidding / scheduling / timecards** | NIM is the reference here — job `BIDDING` status, schedule events, per-user timecards are first-class |

> **How media moves:** there is no shared storage — a thumbnail/version/movie is **downloaded from the source
> host and re-uploaded to the target**, transcoding as needed. **Heavy publishes** live on studio storage
> referenced by path; migration carries the **reference** (path + version + dependency chain).
>
> **Proven (read-back) on representative *slices*:** SG ↔ Kitsu carries structure + statuses + casting +
> thumbnails + multi-version video + notes + custom fields; ftrack carries structure + statuses + video.
> **AYON** & **NIM**: data models verified on live installs, MCPs tested live (read + CRUD round-trip with
> dry_run + normalized `project_summary`). And the contract holds across all five at once — a single
> contract-driven importer read real `project_summary`s from all five and absorbed them into **one schema**
> (**5 projects · 1,562 shots · 217 assets · 10,326 tasks**), the strongest proof the normalized shape is
> genuinely tracker-agnostic. NIM is the strongest **bidding/scheduling** reference for the
> [estimation work](https://github.com/huikku/tracker-mcp-hub).

---
*Part of the tracker-MCP quintet: [shotgrid-mcp](https://github.com/huikku/shotgrid-mcp) ·
[ftrack-mcp](https://github.com/huikku/ftrack-mcp) · [kitsu-mcp](https://github.com/huikku/kitsu-mcp) ·
[ayon-mcp](https://github.com/huikku/ayon-mcp) · [nim-mcp](https://github.com/huikku/nim-mcp). MIT.*
