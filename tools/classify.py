"""Derive uploader, model family and the abliterated flag from a repo id (+ tags). Rules live in families.tsv."""
import os, re
R = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RULES = [(re.compile(l.split("\t")[0], re.I), l.rstrip("\n").split("\t")[1]) for l in open(f"{R}/families.tsv") if l.strip() and not l.startswith("#")]
ABL = re.compile(r"abliterat|obliterat|uncensor|heretic|decensor|derestrict|lorablat|unfiltered|jailbr", re.I)
def family(repo):
    name = repo.split("/", 1)[1]
    for rx, fam in RULES:
        m = rx.search(name)
        if m: return m.expand(fam) if "\\" in fam else fam
    return "Other"
def abliterated(repo, tags=()):
    return bool(ABL.search(repo.split("/", 1)[1]) or any(ABL.search(t) for t in tags))
