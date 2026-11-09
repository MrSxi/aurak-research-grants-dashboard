"""Cluster membership for the project-defined research clusters (see app/clusters.py)."""
from collections import Counter

import numpy as np
from sqlalchemy.orm import Session

from app.clusters import CLUSTER_MIN, CLUSTER_SECONDARY_MARGIN, CLUSTERS, GRANT_CLUSTER_MIN
from app.services.matching import engine

_cache: dict = {}


def cluster_text(c: dict) -> str:
    return f"{c['name']}. {c['scope']} Keywords: {', '.join(c['terms'])}."


def _compute(db: Session):
    if not engine.ready:
        engine.rebuild(db)
    key = (id(engine.item_vecs), id(engine.grant_vecs))
    if _cache.get("key") == key:
        return _cache
    C = engine.embed(db, [cluster_text(c) for c in CLUSTERS])
    # person-to-cluster fit = mean of the person's 5 most cluster-relevant evidence items
    # (a plain average of all evidence blurs interdisciplinary researchers)
    item_sims = engine.item_vecs @ C.T if len(engine.item_vecs) else np.zeros((0, len(CLUSTERS)))
    psims = np.zeros((len(engine.people), len(CLUSTERS)), dtype=np.float32)
    for pi in range(len(engine.people)):
        block = item_sims[engine.item_person == pi]
        if len(block):
            k = min(5, len(block))
            psims[pi] = -np.sort(-block, axis=0)[:k].mean(axis=0)
    members = {c["key"]: [] for c in CLUSTERS}
    person_clusters = {}
    for pi, person in enumerate(engine.people):
        row = psims[pi]
        best = int(np.argmax(row))
        mine = []
        for j in np.argsort(-row):
            if row[j] < CLUSTER_MIN or row[j] < row[best] - CLUSTER_SECONDARY_MARGIN:
                break
            mine.append((CLUSTERS[j]["key"], float(row[j]), j == best))
            members[CLUSTERS[j]["key"]].append((person, engine.people_meta[person], float(row[j]), j == best))
        person_clusters[person] = mine
    gsims = engine.grant_vecs @ C.T if len(engine.grant_vecs) else np.zeros((0, len(CLUSTERS)))
    grant_clusters = {}
    for gi, gid in enumerate(engine.grant_ids):
        row = gsims[gi]
        best = int(np.argmax(row))
        grant_clusters[gid] = [(CLUSTERS[j]["key"], float(row[j])) for j in np.argsort(-row)[:2]
                               if row[j] >= GRANT_CLUSTER_MIN and row[j] >= row[best] - CLUSTER_SECONDARY_MARGIN]
    for k in members:
        members[k].sort(key=lambda m: (m[0][0] != "faculty", not m[3], -m[2]))  # faculty first, primary first
    _cache.clear()
    _cache.update(key=key, members=members, person_clusters=person_clusters, grant_clusters=grant_clusters,
                  csims=C)
    return _cache


def by_key(key: str):
    return next((c for c in CLUSTERS if c["key"] == key), None)


def overview(db: Session):
    data = _compute(db)
    out = []
    grant_counts = Counter(k for gc in data["grant_clusters"].values() for k, _ in gc[:1])
    for c in CLUSTERS:
        mem = data["members"][c["key"]]
        fac = [m for m in mem if m[0][0] == "faculty"]
        out.append({**c, "members": mem, "faculty": fac, "students": [m for m in mem if m[0][0] == "student"],
                    "schools": Counter(m[1].get("school") for m in fac if m[1].get("school")),
                    "grants": grant_counts.get(c["key"], 0)})
    return out


def detail(db: Session, key: str):
    data = _compute(db)
    c = by_key(key)
    if not c:
        return None
    grant_ids = [(gid, s) for gid, gc in data["grant_clusters"].items() for k, s in gc if k == key]
    grant_ids.sort(key=lambda x: -x[1])
    mem = data["members"][key]
    return {**c, "members": mem, "grant_ids": grant_ids,
            "schools": Counter(m[1].get("school") for m in mem if m[0][0] == "faculty" and m[1].get("school"))}


def for_grant(db: Session, gid: int):
    data = _compute(db)
    out = []
    for k, s in data["grant_clusters"].get(gid, []):
        fac = [m for m in data["members"][k] if m[0][0] == "faculty"]
        out.append((by_key(k), s, len(fac), len({m[1].get("school") for m in fac if m[1].get("school")})))
    return out


def for_person(db: Session, ptype: str, pid: str):
    data = _compute(db)
    return [(by_key(k), s, primary) for k, s, primary in data["person_clusters"].get((ptype, pid), [])]


def for_text(db: Session, text: str, top: int = 2):
    data = _compute(db)
    q = engine.embed_one(db, text)
    sims = data["csims"] @ q
    return [(CLUSTERS[j], float(sims[j]), data["members"][CLUSTERS[j]["key"]]) for j in np.argsort(-sims)[:top]]
