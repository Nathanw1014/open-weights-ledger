#!/usr/bin/env python3
"""Build the dashboard data from ledger/ and upstream/.
docs/data.js      tracked repos (uploader, family, abliterated), every CURRENT hash version, events.
docs/hist/XX.js   older versions (superseded / removed), split by the first two hex digits of the sha256; loaded on demand."""
import csv, glob, json, os, time, collections, shutil
R = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
rd = lambda p: list(csv.DictReader(open(p), delimiter="\t")) if os.path.exists(p) else []
led, evs, pfr, meta = rd(f"{R}/ledger/LEDGER.tsv"), rd(f"{R}/ledger/EVENTS.tsv"), rd(f"{R}/ledger/PIRATEFACE.tsv"), rd(f"{R}/ledger/REPOS.tsv")
state = json.load(open(f"{R}/ledger/state.json"))
updir = lambda repo: f"{R}/upstream/{repo.replace('/', '__')}"
ms, has_ms, main = {}, set(), {}
for f in glob.glob(f"{R}/upstream/*/modelscope-files.json"):
    repo = os.path.basename(os.path.dirname(f)).replace("__", "/"); has_ms.add(repo)
    for e in json.load(open(f)):
        if e.get("IsLFS"): ms[(repo, e["Path"])] = e["Sha256"]
for repo, s in state.items():
    f = f"{updir(repo)}/files-{s.get('sha')}.tsv"
    if os.path.exists(f):
        for r in csv.reader(open(f), delimiter="\t"):
            if len(r) == 4 and r[3] == "sha256": main[(repo, r[0])] = r[2]
pf = {r["repo"]: dict(commit=r["pinned_commit"], infohash=r["infohash"], size=r["size"], seeds=r["seeds"], checked=r["checked_at"]) for r in pfr if r["infohash"]}
pf_checked = {r["repo"] for r in pfr}
order = collections.defaultdict(list)           # (repo, path) -> [(commit_date, sha256)] oldest first
for r in led: order[(r["repo"], r["path"])].append((r["commit_date"], r["sha256"]))
vernum = {}
for k, v in order.items():
    # oldest first; on equal dates the hash at main today sorts last
    for n, (_, _, sha) in enumerate(sorted((d, main.get(k) == sha, sha) for d, sha in v), 1): vernum[(k, sha)] = (n, len(v))
known = collections.defaultdict(set)            # (repo, path) -> every sha256 that path has had on Hugging Face
for r in led: known[(r["repo"], r["path"])].add(r["sha256"])
names = sorted(state, key=str.lower); rid = {r: i for i, r in enumerate(names)}
ASSET = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".mp4", ".mov", ".webm", ".pdf", ".ico")
COLS = ["repo", "path", "sha256", "size", "commit", "committed", "state", "modelscope", "pirateface", "ver", "vers"]
agg = collections.defaultdict(lambda: collections.Counter()); last = {}
cur, hist = [], collections.defaultdict(list)
for r in led:
    repo = r["repo"]; k = (repo, r["path"]); sha = r["sha256"]
    if repo not in rid or r["path"].lower().endswith(ASSET): continue   # pictures and docs stay in the ledger but are not weights
    st = "current" if main.get(k) == sha else ("superseded" if k in main else "removed")
    m = "same" if ms.get(k) == sha else ("differs" if k in ms else ("absent" if repo in has_ms else "no-repo"))
    if m == "differs" and st == "current" and ms[k] in known[k]: m = "older"   # stale mirror: ModelScope still has a hash Hugging Face replaced
    p = ("pinned" if st == "current" and pf[repo]["commit"] == state[repo].get("sha") else "torrent-other-commit") if repo in pf else ("none" if repo in pf_checked else "unchecked")
    row = [rid[repo], r["path"], sha, int(r["size_bytes"] or 0), r["commit"][:12], r["commit_date"][:10], st, m, p, *vernum[(k, sha)]]
    a = agg[repo]; a[st] += 1; a["versions"] += 1; a["confirmed"] += (st == "current" and (m == "same"))
    last[repo] = max(last.get(repo, ""), r["commit_date"][:10])
    (cur if st == "current" else hist[sha[:2]]).append(row)
mrow = {r["repo"]: r for r in meta}
repos = []
for k in names:
    v, a, mr = state[k], agg[k], mrow.get(k, {})
    repos.append(dict(repo=k, uploader=k.split("/")[0], family=v.get("family") or mr.get("family", "Other"), abliterated=bool(v.get("abl")), label=v.get("label", ""), downloads=v.get("downloads", 0), pinned=bool(v.get("pinned")),
                      status=v.get("status", 200), main=v.get("sha", ""), checked=v.get("snapshot", ""), current=a["current"], confirmed=a["confirmed"], versions=a["versions"], superseded=a["superseded"], removed=a["removed"],
                      modelscope=k in has_ms, pirateface=pf.get(k), pirateface_checked=k in pf_checked, last=last.get(k, "")))
ev = [dict(time=e["time"], repo=e["repo"], event=e["event"], detail=e["detail"]) for e in evs]
quiet = [e for e in ev if e["event"] == "NEW-REPO"]; loud = [e for e in ev if e["event"] != "NEW-REPO"]
tot = collections.Counter(); [tot.update({s: a[s]}) for a in agg.values() for s in ("current", "superseded", "removed")]
data = dict(generated=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), cols=COLS, files=cur, repos=repos,
            totals=dict(current=tot["current"], superseded=tot["superseded"], removed=tot["removed"], history_rows=sum(len(v) for v in hist.values())),
            hist_shards=sorted(hist), events=loud[-2000:], new_repo_events=len(quiet), new_repo_recent=quiet[-50:])
open(f"{R}/docs/data.js", "w").write("window.DATA=" + json.dumps(data, separators=(",", ":")) + ";\n")
shutil.rmtree(f"{R}/docs/hist", ignore_errors=True); os.makedirs(f"{R}/docs/hist")
for pre, rows in hist.items():
    open(f"{R}/docs/hist/{pre}.js", "w").write(f"window.HIST_LOADED&&window.HIST_LOADED({json.dumps(pre)}," + json.dumps(rows, separators=(",", ":")) + ");\n")
with open(f"{R}/docs/hist/all.js", "w") as f:      # everything in one request, for the page's "load all older versions"
    for pre in sorted(hist): f.write(open(f"{R}/docs/hist/{pre}.js").read())
mb = lambda p: os.path.getsize(p) / 2**20
print(f"dashboard: {len(repos)} repos | data.js {len(cur)} rows {mb(f'{R}/docs/data.js'):.1f} MB | history {data['totals']['history_rows']} rows in {len(hist)} shards {sum(mb(f) for f in glob.glob(f'{R}/docs/hist/??.js')):.1f} MB | abliterated repos {sum(r['abliterated'] for r in repos)} | families {len({r['family'] for r in repos})}")
