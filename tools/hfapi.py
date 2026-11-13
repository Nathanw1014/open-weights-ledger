"""Anonymous Hugging Face API helpers (no token, generic user agent, throttled)."""
import json, re, time, urllib.request, urllib.parse, urllib.error
UA = {"User-Agent": "curl/8"}
_last = [0.0]
def get(url, tries=5):
    for i in range(tries):
        if "huggingface.co" in url:
            w = 0.6 - (time.time() - _last[0])
            if w > 0: time.sleep(w)
            _last[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return r.status, json.load(r), r.headers.get("Link", "")
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(60 * (i + 1)); continue
            if e.code >= 500: time.sleep(5 * (i + 1)); continue
            return e.code, None, ""
        except Exception:
            time.sleep(3 * (i + 1))
    return 0, None, ""
def tree(repo, rev):
    """Full recursive file list of repo at rev, or None if rev/repo absent."""
    url = f"https://huggingface.co/api/models/{repo}/tree/{urllib.parse.quote(rev, safe='')}?recursive=true&limit=1000"
    out = []
    while url:
        st, js, link = get(url)
        if st != 200: return None
        out += js
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = m.group(1) if m else None
    return out
def search(term, limit=60):
    st, js, _ = get("https://huggingface.co/api/models?" + urllib.parse.urlencode({"search": term, "limit": limit, "sort": "downloads"}))
    return [m["id"] for m in js] if st == 200 else []
def listing(author=None, search=None, top=None):
    """Repos of an author and/or matching a search, most downloaded first: [{id, sha, gated, downloads, tags}] or None on failure."""
    p = {"sort": "downloads", "limit": min(top or 1000, 1000)}
    if author: p["author"] = author
    if search: p["search"] = search
    url = "https://huggingface.co/api/models?" + urllib.parse.urlencode(p) + "&expand[]=sha&expand[]=gated&expand[]=downloads&expand[]=tags"
    out = []
    while url and (not top or len(out) < top):
        st, js, link = get(url)
        if st != 200: return None
        out += js
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = m.group(1) if m else None
    return out[:top] if top else out
def sha_of(entry):
    return entry["lfs"]["oid"] if entry.get("lfs") else entry.get("oid")
