#!/usr/bin/env python3
"""scrape-hf.py [--add org/repo ...] [--force] [--limit N]

Track Hugging Face repos (anonymous). watchlist.txt lines:
  org/repo                         one repo, pinned: also keeps a pointer-only git history bundle
  author:NAME                      every repo of a publisher
  author:NAME search:TERM          a publisher's repos matching TERM
  search:TERM top:N                the N most downloaded repos matching TERM
Any rule may end with label:NAME, a free-form collection label for the repos it finds (shown as a filter on the dashboard).
A repo, once seen, stays tracked (ledger/state.json) so its disappearance is noticed.

When a repo is new or its main commit moved, its history is cloned without weights and
  ledger/LEDGER.tsv  gets every weight hash version (repo, path, sha256, size, commit, commit_date, first_seen, validation)
  ledger/EVENTS.tsv  gets NEW-REPO, MAIN-MOVED, REUPLOAD, FILE-REMOVED, HISTORY-REWRITTEN, REPO-UNAVAILABLE, REPO-GATED, REPO-BACK
  ledger/REPOS.tsv   lists every tracked repo with uploader, family, abliterated flag, downloads, status
validation: unvalidated (only Hugging Face lists the hash) | modelscope (same hash, same path there)
"""
import csv, glob, json, os, re, shutil, subprocess, sys, tempfile, time, concurrent.futures as cf
sys.path.insert(0, os.path.dirname(__file__)); import hfapi, classify
R = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NOW = lambda: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
LED, EVT, STATE, WATCH, REPOS = f"{R}/ledger/LEDGER.tsv", f"{R}/ledger/EVENTS.tsv", f"{R}/ledger/state.json", f"{R}/watchlist.txt", f"{R}/ledger/REPOS.tsv"
HDR = ["repo", "path", "sha256", "size_bytes", "commit", "commit_date", "first_seen", "validation"]
updir = lambda repo: f"{R}/upstream/{repo.replace('/', '__')}"
GIT_ENV = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1", GIT_TERMINAL_PROMPT="0")

def load_watch():
    """-> pinned repos, {repo: listing entry + via}, ok (False if any listing call failed)."""
    pinned, found, ok = [], {}, True
    for l in open(WATCH):
        l = l.split("#")[0].strip()
        if not l: continue
        kv = dict(t.split(":", 1) for t in l.split() if ":" in t)
        if not kv: pinned.append(l); continue
        res = hfapi.listing(author=kv.get("author"), search=kv.get("search"), top=int(kv["top"]) if "top" in kv else None)
        if res is None: ok = False; print("listing failed:", l, flush=True); continue
        print(f"watch rule '{l}': {len(res)} repos", flush=True)
        for m in res:
            e = found.setdefault(m["id"], dict(m, via=l))
            # a search is fuzzy: only label a search hit when the label word is in the repo name itself
            if kv.get("label") and ("search" not in kv or kv["label"].lower() in m["id"].split("/", 1)[1].lower()): e.setdefault("label", kv["label"].replace("_", " "))
    return list(dict.fromkeys(pinned)), found, ok

def read_pointers(bare, blobs):
    chk = subprocess.run(["git", "-C", bare, "cat-file", "--batch-check"], input="\n".join(blobs) + "\n", capture_output=True, text=True).stdout
    small = [l.split()[0] for l in chk.splitlines() if len(l.split()) == 3 and l.split()[1] == "blob" and int(l.split()[2]) < 400]
    out = subprocess.run(["git", "-C", bare, "cat-file", "--batch"], input=("\n".join(small) + "\n").encode(), capture_output=True).stdout
    ptr, i = {}, 0
    while i < len(out):
        j = out.index(b"\n", i); head = out[i:j].split()
        if len(head) != 3: break
        n = int(head[2]); body = out[j + 1:j + 1 + n].decode("utf-8", "replace"); i = j + 2 + n
        m = re.search(r"oid sha256:([0-9a-f]{64})\s+size (\d+)", body)
        if body.startswith("version https://git-lfs") and m: ptr[head[0].decode()] = (m.group(1), m.group(2))
    return ptr

def clone(repo, keep_bundle, seen_commits=()):
    """-> (main_sha, {path: (sha256, size)} at main, [(path, sha256, size, commit, commit_date)] over all history) or None."""
    tmp = tempfile.mkdtemp(prefix="mh-"); bare = tmp + "/r"
    try:
        r = subprocess.run(["git", "clone", "-q", "--bare", f"https://huggingface.co/{repo}", bare], env=GIT_ENV, capture_output=True, timeout=900)
        if r.returncode != 0: return None
        head = subprocess.run(["git", "-C", bare, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        log = subprocess.run(["git", "-C", bare, "log", "--all", "--reverse", "--format=@%H%x09%cI", "--raw", "--no-abbrev", "--no-renames", "-m", "--first-parent"], capture_output=True, text=True, errors="replace").stdout
        seen, order, cur = {}, [], None
        for line in log.splitlines():
            if line.startswith("@"): cur = line[1:].split("\t")
            elif line.startswith(":") and "\t" in line:
                meta, path = line.split("\t", 1); blob = meta.split()[3]
                if set(blob) != {"0"} and (blob, path) not in seen: seen[(blob, path)] = cur; order.append((blob, path))
        ls = subprocess.run(["git", "-C", bare, "ls-tree", "-r", "HEAD"], capture_output=True, text=True, errors="replace").stdout
        tip = [(l.split("\t")[0].split()[2], l.split("\t", 1)[1]) for l in ls.splitlines() if "\t" in l]
        ptr = read_pointers(bare, sorted({b for b, _ in order} | {b for b, _ in tip}))
        hist = [(p, *ptr[b], seen[(b, p)][0], seen[(b, p)][1]) for b, p in order if b in ptr]
        files = {p: ptr[b] for b, p in tip if b in ptr}
        # commits we recorded earlier must still exist; if not, the repo history was rewritten or squashed
        missing = []
        if seen_commits:
            chk = subprocess.run(["git", "-C", bare, "cat-file", "--batch-check"], input="\n".join(seen_commits) + "\n", capture_output=True, text=True).stdout
            missing = [l.split()[0] for l in chk.splitlines() if l.endswith(" missing")]
        if keep_bundle and not missing:         # never overwrite a saved history with a rewritten one
            os.makedirs(updir(repo), exist_ok=True)
            subprocess.run(["git", "-C", bare, "bundle", "create", "-q", f"{updir(repo)}/history.bundle", "--all"], capture_output=True)
        return head, files, hist, missing
    except Exception as e: print(repo, "clone error:", repr(e)[:200], flush=True); return None
    finally: shutil.rmtree(tmp, ignore_errors=True)

def snapshot(repo, keep_bundle, seen_commits=()):
    c = clone(repo, keep_bundle, seen_commits)
    if c is None: return None
    head, files, hist, missing = c
    dd = updir(repo); os.makedirs(dd, exist_ok=True)
    with open(f"{dd}/files-{head}.tsv", "w") as f:
        f.write("# path\tsize\thash\tkind (sha256 = LFS weight hash)\n")
        for p in sorted(files): f.write(f"{p}\t{files[p][1]}\t{files[p][0]}\tsha256\n")
    open(f"{dd}/SOURCE.txt", "w").write(f"repo: https://huggingface.co/{repo}\nmain commit at snapshot: {head}\nsnapshot taken: {NOW()}\n")
    try:
        ms = hfapi.get(f"https://modelscope.cn/api/v1/models/{repo}/repo/files?Revision=master&Recursive=true", tries=2)[1]
        if ms and (ms.get("Data") or {}).get("Files"): json.dump([dict(Path=e["Path"], Sha256=e["Sha256"], IsLFS=True, CommittedDate=e.get("CommittedDate")) for e in ms["Data"]["Files"] if e.get("IsLFS")], open(f"{dd}/modelscope-files.json", "w"), indent=0)
    except Exception: pass
    return head, files, hist, missing

def validation(ledger):
    ms = set()
    for f in glob.glob(f"{R}/upstream/*/modelscope-files.json"):
        repo = os.path.basename(os.path.dirname(f)).replace("__", "/")
        ms |= {(repo, e["Path"], e["Sha256"]) for e in json.load(open(f)) if e.get("IsLFS")}
    for k, row in ledger.items(): row["validation"] = "modelscope" if k in ms else "unvalidated"

def main():
    args = sys.argv[1:]; force = "--force" in args
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 10**9
    if "--add" in args:
        have = open(WATCH).read()
        with open(WATCH, "a") as f:
            for r in args[args.index("--add") + 1:]:
                if not r.startswith("--") and not re.search(rf"^{re.escape(r)}\s*$", have, re.M): f.write(r + "\n")
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    ledger = {(r["repo"], r["path"], r["sha256"]): r for r in csv.DictReader(open(LED), delimiter="\t")} if os.path.exists(LED) else {}
    events, failed = [], 0
    def ev(repo, kind, detail):
        events.append((NOW(), repo, kind, detail)); print(repo, kind, detail, flush=True)
        new = not os.path.exists(EVT)
        with open(EVT, "a") as f:
            if new: f.write("time\trepo\tevent\tdetail\n")
            f.write("\t".join(events[-1]) + "\n")
    def save(final=False):
        if final: validation(ledger)
        with open(LED + ".tmp", "w") as f:
            w = csv.DictWriter(f, HDR, delimiter="\t", lineterminator="\n"); w.writeheader()
            for k in sorted(ledger, key=lambda k: (k[0].lower(), k[1], ledger[k]["commit_date"])): w.writerow(ledger[k])
        os.replace(LED + ".tmp", LED)
        json.dump(state, open(STATE + ".tmp", "w"), indent=0, sort_keys=True); os.replace(STATE + ".tmp", STATE)
        with open(REPOS + ".tmp", "w") as f:
            f.write("repo\tuploader\tfamily\tabliterated\tlabel\tdownloads\tstatus\tmain\tpinned\tvia\n")
            for r in sorted(state, key=str.lower):
                s = state[r]; f.write("\t".join(str(x) for x in (r, r.split("/")[0], s.get("family", ""), "yes" if s.get("abl") else "no", s.get("label", ""), s.get("downloads", ""), s.get("status", ""), s.get("sha", ""), "yes" if s.get("pinned") else "no", s.get("via", ""))) + "\n")
        os.replace(REPOS + ".tmp", REPOS)

    pinned, found, listed_ok = load_watch()
    todo = []                                   # (repo, expected main sha or None)
    for repo in list(dict.fromkeys(pinned + list(found) + sorted(state))):
        s = state.setdefault(repo, {}); m = found.get(repo)
        s["pinned"] = repo in pinned; s["family"] = classify.family(repo)
        if m:
            s["via"] = m["via"]; s["label"] = m.get("label", ""); s["downloads"] = m.get("downloads", 0); s["abl"] = classify.abliterated(repo, m.get("tags") or [])
            sha, gated, st = m.get("sha"), m.get("gated"), 200
        else:
            s.setdefault("abl", classify.abliterated(repo))
            if not listed_ok and not s["pinned"]: continue      # a listing failed: do not misread absence as removal
            st, info, _ = hfapi.get(f"https://huggingface.co/api/models/{repo}")
            if st == 0: failed += 1; continue
            sha, gated = (info.get("sha"), info.get("gated")) if st == 200 else (None, None)
            if st == 200: s["downloads"] = info.get("downloads", s.get("downloads", 0)); s["abl"] = classify.abliterated(repo, info.get("tags") or [])
        if st != 200:
            if s.get("status") != st: ev(repo, "REPO-UNAVAILABLE", f"http {st} (Hugging Face answers 401 or 404 for a repo that is deleted, private or gated); last main {s.get('sha', 'never seen')}")
            s["status"] = st; continue
        if gated and not s.get("sha"):
            if s.get("status") != "gated": ev(repo, "REPO-GATED", f"gated ({gated}); cannot be read without an account, main {sha}")
            s["status"] = "gated"; continue
        if s.get("status") not in (None, 200, "gated"): ev(repo, "REPO-BACK", f"available again, main {sha}")
        s["status"] = 200
        if sha != s.get("sha") or force: todo.append(repo)
    print(f"tracked {len(state)}  to snapshot {len(todo)}" + (f" (limited to {limit})" if len(todo) > limit else ""), flush=True)
    todo = todo[:limit]; done = 0
    with cf.ThreadPoolExecutor(3) as ex:
        recorded = {}
        for (rp, _, _), row in ledger.items(): recorded.setdefault(rp, set()).add(row["commit"])
        futs = {ex.submit(snapshot, repo, state[repo].get("pinned", False), sorted(recorded.get(repo, set()) | ({state[repo]["sha"]} if state[repo].get("sha") else set()))): repo for repo in todo}
        for fut in cf.as_completed(futs):
            repo = futs[fut]; s = state[repo]
            try: res = fut.result()
            except Exception as e: res = None; print(repo, "ERROR", repr(e)[:160], flush=True)
            if res is None:
                failed += 1; print(repo, "SNAPSHOT-INCOMPLETE (clone failed); will retry next run", flush=True); continue
            head, files, hist, missing = res
            old = {}
            if s.get("sha") and os.path.exists(f"{updir(repo)}/files-{s['sha']}.tsv"):
                old = {r[0]: r[2] for r in csv.reader(open(f"{updir(repo)}/files-{s['sha']}.tsv"), delimiter="\t") if len(r) == 4 and r[3] == "sha256"}
            for p, sha, size, commit, cdate in hist:
                ledger.setdefault((repo, p, sha), dict(repo=repo, path=p, sha256=sha, size_bytes=size, commit=commit, commit_date=cdate, first_seen=NOW(), validation="unvalidated"))
            prev = s.get("sha"); s["sha"] = head; s["snapshot"] = NOW(); done += 1
            if missing: ev(repo, "HISTORY-REWRITTEN", f"{len(missing)} commit(s) we recorded are gone from the repo, e.g. {missing[0]}; previous main {prev}. Recorded hashes are kept in the ledger" + ("; saved history.bundle left untouched" if s.get("pinned") else ""))
            if not prev: ev(repo, "NEW-REPO", f"main {head}, {len(files)} weight files, {len(hist)} hash versions in history")
            elif prev != head:
                ev(repo, "MAIN-MOVED", f"{prev} -> {head}")
                for p, h in old.items():
                    if p not in files: ev(repo, "FILE-REMOVED", f"{p} was {h}")
                    elif files[p][0] != h: ev(repo, "REUPLOAD", f"{p} {h} -> {files[p][0]}")
                if prev and os.path.exists(f"{updir(repo)}/files-{prev}.tsv"): os.remove(f"{updir(repo)}/files-{prev}.tsv")   # the ledger keeps the history
            if done % 40 == 0: save(); print(f"  progress {done}/{len(todo)}  ledger {len(ledger)}", flush=True)
    save(final=True)
    import collections
    print(f"repos {len(state)}  snapshotted {done}  failed {failed}  ledger rows {len(ledger)}  events {len(events)}  validation {dict(collections.Counter(r['validation'] for r in ledger.values()))}")
    return 1 if failed else 0
sys.exit(main())
