# Open Weights Ledger

A dated record of model weight hashes, so that a file found anywhere can be checked against what was
originally published, even after a repo is deleted, gated or re-uploaded.

It tracks a watchlist of Hugging Face repos (whole publishers, plus the most downloaded abliterated and
uncensored variants, which are the likeliest to be taken down). For every weight file it records every sha256
that path has ever had, and which other services list the same hash.

**No source is treated as the authority.** Hugging Face is the origin of each hash and never counts as its own
confirmation. A hash is confirmed only when something independent agrees: another service listing the same
hash for the same file. This repo's own history is the timestamp: rows are never
removed, and every run is a commit.

## Dashboard

`docs/` is a static page (no server code, no external scripts): look up a sha256 or file name, see which
services list the same hash, which files have been re-uploaded, and what has changed or disappeared.

## Data

| File | Contents |
|---|---|
| `ledger/LEDGER.tsv` | One row per hash version: repo, path, sha256, size, commit, commit_date, first_seen, validation |
| `ledger/EVENTS.tsv` | What changed and when: NEW-REPO, MAIN-MOVED, REUPLOAD, FILE-REMOVED, HISTORY-REWRITTEN, REPO-UNAVAILABLE, REPO-GATED, REPO-BACK |
| `ledger/REPOS.tsv` | Every tracked repo: uploader, model family, abliterated flag, downloads, status |
| `ledger/PIRATEFACE.tsv` | Torrent infohash and pinned commit per repo, where a torrent is listed |
| `upstream/<org>__<repo>/` | File list at the last seen main commit, the ModelScope listing, and for hand-picked repos a pointer-only git history bundle (no weights) |

`validation` in the ledger:

- `unvalidated`: only Hugging Face lists the hash.
- `modelscope`: ModelScope lists the same hash for the same path.

## Checking a file

    sha256sum <file>
    grep <hash> ledger/LEDGER.tsv

or paste the hash into the dashboard.

## How it works

- `watchlist.txt`: what to track. `org/repo`, `author:NAME`, `author:NAME search:TERM`, or `search:TERM top:N`.
  A rule can end with `label:NAME` to put its repos in a named collection (for example an engine's own model builds).
  A repo, once seen, stays tracked so its disappearance is noticed.
- `tools/scrape-hf.py`: anonymous. Lists each publisher in one call, and for any repo whose main commit moved,
  clones its history without weights and reads every weight hash from it. It also checks that commits recorded
  earlier still exist, and logs `HISTORY-REWRITTEN` if they do not.
- `families.tsv`: name rules for the model-family column. The abliterated flag comes from repo names and tags
  (abliterated, obliterated, uncensored, heretic and similar), so it can miss or mislabel.
- `tools/build-dashboard.py`: writes `docs/data.js` and `docs/hist/`.
- `tools/update.sh`: scrape, rebuild, commit, push. `systemd/` has user units for a daily run and for serving
  `docs/` locally (`tools/serve.py`).

## Limits

- A matching hash proves the bytes are the same ones recorded at that time and place. It does not prove a model
  is safe, or that it is what its publisher intended.
- For anything recorded on the first scrape, the history is Hugging Face's own account. Independent evidence
  starts from when this record began (2026-11-13).
- ModelScope mostly mirrors Hugging Face, so it witnesses what Hugging Face served when the mirror was taken.
  A torrent that carries a file is a second copy of the bytes, not an independent hash.
- Gated repos cannot be read anonymously and are listed as gated.

## Licence

MIT, for the code and the recorded data. See `LICENSE`.
