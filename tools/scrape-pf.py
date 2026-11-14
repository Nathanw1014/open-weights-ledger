#!/usr/bin/env python3
"""scrape-pf.py [--all]

Check Pirate Face (pirateface.co, a torrent index of Hugging Face models) for every tracked repo, using its
documented text search (/s/<terms>). Anonymous, one request every ~1.3 s.

Default (daily): repos never checked, repos that have a torrent (to catch removals and updates), and the
oldest-checked seventh of the rest, so every repo is re-checked about once a week.
--all: every repo not checked in the last 20 hours (resumable if interrupted).

Writes ledger/PIRATEFACE.tsv (repo, pinned_commit, infohash, size, seeds, checked_at; an empty infohash means
checked, no torrent) and logs changes to ledger/EVENTS.tsv:
  PF-TORRENT-ADDED, PF-TORRENT-REMOVED, PF-TORRENT-UPDATED (new infohash or pinned to another commit).
A repo's first check logs nothing."""
import csv, json, math, os, re, sys, time, urllib.error, urllib.parse, urllib.request
R = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PF, EVT, STATE = f"{R}/ledger/PIRATEFACE.tsv", f"{R}/ledger/EVENTS.tsv", f"{R}/ledger/state.json"
HDR = ["repo", "pinned_commit", "infohash", "size", "seeds", "checked_at"]
NOW = lambda: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
age_h = lambda ts: (time.time() - time.mktime(time.strptime(ts, '%Y-%m-%dT%H:%M:%SZ')) + time.timezone) / 3600 if ts else 1e9

def fetch(q):
    """-> response text for a Pirate Face search, None on failure."""
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(f"https://pirateface.co/s/{q}", headers={"User-Agent": "curl/8"}), timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500: time.sleep(30 * (i + 1)); continue
            return None
        except Exception: time.sleep(10 * (i + 1))
    return None

def parse(repo, text):
    """-> torrent dict if the repo is listed in this response, {} if the response is a clean no-match, None if unclear."""
    for line in text.splitlines():
        p = line.split()
        if p and p[0] == repo and "magnet:" in line:
            mag = p[-1]; ih = re.findall(r"urn:(btih|btmh):([0-9a-fA-F]+)", mag)
            pin = re.search(r"%2F([0-9a-f]{40})%2F", mag)
            sz = re.search(r"\s(\d[\d.]*\s[KMGT]?B)\s", line); sd = re.search(r"(\d+) seeds", line)
            return dict(pinned_commit=pin.group(1) if pin else "", infohash=" ".join(f"{k}:{v}" for k, v in ih),
                        size=sz.group(1) if sz else "", seeds=sd.group(1) if sd else "")
    return {} if ("No torrents match" in text or all(l.split()[:1] != [repo] for l in text.splitlines() if l.strip())) and text.strip() else None

def lookup(repo):
    """-> dict for the repo's torrent, {} if Pirate Face has none, None on failure.
    The exact org/repo query is tried first: the loose token search sometimes answers "No torrents match" for a
    listed repo, which once logged hundreds of false PF-TORRENT-REMOVED events. {} needs both queries to say no."""
    exact = urllib.parse.quote(repo, safe="")
    tokens = "+".join(urllib.parse.quote(t, safe=".") for t in re.findall(r"[A-Za-z0-9.]+", repo.replace("/", " ")))
    out = {}
    for q in (exact, tokens):
        text = fetch(q)
        res = parse(repo, text) if text is not None else None
        if res: return res
        if res is None: out = None
    return out

def main():
    full = "--all" in sys.argv
    state = json.load(open(STATE))
    rows = {r["repo"]: r for r in csv.DictReader(open(PF), delimiter="\t")} if os.path.exists(PF) else {}
    repos = sorted(state, key=str.lower)
    if full: todo = [r for r in repos if age_h(rows.get(r, {}).get("checked_at", "")) > 20]
    else:
        new = [r for r in repos if r not in rows]
        has = [r for r in repos if r in rows and rows[r]["infohash"]]
        rest = sorted((r for r in repos if r in rows and not rows[r]["infohash"]), key=lambda r: rows[r]["checked_at"])
        todo = new + has + rest[:math.ceil(len(rest) / 7)]
    print(f"pirate face: {len(rows)} on record, checking {len(todo)}", flush=True)
    events, failed = [], 0
    def save():
        with open(PF + ".tmp", "w") as f:
            w = csv.DictWriter(f, HDR, delimiter="\t", lineterminator="\n"); w.writeheader()
            for k in sorted(rows, key=str.lower): w.writerow({h: rows[k].get(h, "") for h in HDR})
        os.replace(PF + ".tmp", PF)
    for n, repo in enumerate(todo, 1):
        res = lookup(repo); time.sleep(1.3)
        if res is None: failed += 1; continue
        old = rows.get(repo); new = dict(repo=repo, checked_at=NOW(), **{h: res.get(h, "") for h in HDR[1:5]})
        if old:
            if not old["infohash"] and new["infohash"]: events.append((NOW(), repo, "PF-TORRENT-ADDED", f"torrent {new['infohash'].split()[0]}, pinned to {new['pinned_commit'] or '?'}, {new['seeds']} seeds"))
            elif old["infohash"] and not new["infohash"]: events.append((NOW(), repo, "PF-TORRENT-REMOVED", f"torrent {old['infohash'].split()[0]} (pinned to {old['pinned_commit'] or '?'}) is no longer listed"))
            elif old["infohash"] and (old["infohash"] != new["infohash"] or old["pinned_commit"] != new["pinned_commit"]):
                events.append((NOW(), repo, "PF-TORRENT-UPDATED", f"pinned {old['pinned_commit'] or '?'} -> {new['pinned_commit'] or '?'}; {old['infohash'].split()[0]} -> {new['infohash'].split()[0]}"))
        rows[repo] = new
        if n % 50 == 0: save(); print(f"  {n}/{len(todo)}  torrents {sum(1 for r in rows.values() if r['infohash'])}", flush=True)
    save()
    if events:
        with open(EVT, "a") as f:
            for e in events: f.write("\t".join(e) + "\n"); print(*e[1:], flush=True)
    print(f"pirate face: checked {len(todo) - failed}, failed {failed}, repos with a torrent {sum(1 for r in rows.values() if r['infohash'])} of {len(rows)}, events {len(events)}")
    return 1 if failed > max(5, len(todo) // 20) else 0
if __name__ == "__main__": sys.exit(main())
