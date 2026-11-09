"""Matching engine shared by every view and the chatbot.

People (faculty, and students with approved evidence) are represented by many small evidence
items (each interest, topic, keyword, publication and past grant/project), each embedded with a
local sentence-transformers model. A grant is embedded once. For a grant and a person the score
per component is the best item similarity (publications: mean of the top 3), and the final match
score is a weighted mean over the components the person has, with weights from config.
Every score comes with the evidence items that produced it.
"""
import logging
import re
import threading
from collections import defaultdict
from datetime import datetime

import numpy as np
from sqlalchemy import delete, false, select, true
from sqlalchemy.orm import Session

from app import config
from app.models import Award, Embedding, Faculty, Grant, Interest, Match, Project, Publication, Student
from app.util import norm_text, sha

log = logging.getLogger("matching")

COMPONENTS = {
    "interests": ("Research interests", lambda: config.MATCH_WEIGHT_INTERESTS),
    "publications": ("Publications", lambda: config.MATCH_WEIGHT_PUBLICATIONS),
    "topics": ("OpenAlex research topics", lambda: config.MATCH_WEIGHT_TOPICS),
    "past_grants": ("Past grants & projects", lambda: config.MATCH_WEIGHT_PAST_GRANTS),
    "keywords": ("Keywords", lambda: config.MATCH_WEIGHT_KEYWORDS),
}
COMP_KEYS = list(COMPONENTS)


LEX_SCORE = 0.62  # similarity credited when a person's interest/keyword phrase appears verbatim in the grant text
GENERIC = {"management", "leadership", "education", "modeling", "modelling", "security", "development", "innovation",
           "technology", "engineering", "research", "analysis", "assessment", "communication", "performance",
           "evaluation", "simulation", "optimization", "optimisation", "materials", "systems", "design", "teaching",
           "learning", "health", "energy", "data", "policy", "economics", "marketing", "finance", "planning", "networks"}
LEAD_INS = re.compile(r"^.*?(research (interests?|focus(es)?|centers on|areas?)( are| is| include| includes| on| in)?|"
                      r"interests? (include|are|in)|focus(es|ed)? on|specializ\w+ in)\s*:?\s*", re.I)


def split_phrases(text: str) -> list[str]:
    """Long interest statements (whole sentences in the dataset) become short phrases for matching."""
    text = (text or "").strip()
    if len(text) <= 60:
        return [text] if text else []
    out = []
    for sentence in re.split(r"(?<=[.;:])\s+|\n", text):
        sentence = LEAD_INS.sub("", sentence)
        for frag in re.split(r",|;|\band\b|\bincluding\b|\bsuch as\b|\bwith particular interests in\b|\(|\)", sentence):
            frag = frag.strip(" .:-–\t")
            if 3 <= len(frag) <= 70 and not re.match(r"^(dr|prof|he|she|his|her|my|i|there|previously)\b", frag, re.I):
                out.append(frag)
    return list(dict.fromkeys(out))[:20] or [text[:200]]


def lexical_ok(phrase: str) -> bool:
    words = phrase.split()
    if len(words) >= 2:
        return len(words) <= 6 and not all(w in GENERIC for w in words)
    return len(phrase) >= 7 and phrase not in GENERIC


def ngrams(text: str, n: int = 4) -> set:
    toks = norm_text(text).split()
    return {" ".join(toks[i:i + k]) for k in range(1, n + 1) for i in range(len(toks) - k + 1)}


def grant_text(g: Grant) -> str:
    parts = [g.title or "", " ".join(g.research_areas or []), " ".join(g.keywords or []), (g.description or "")[:1500]]
    return ". ".join(p for p in parts if p).strip()


class Engine:
    def __init__(self):
        self._model = None
        self._lock = threading.RLock()
        self.people: list[tuple[str, str]] = []      # (type, id)
        self.people_meta: dict = {}
        self.item_vecs = np.zeros((0, 384), dtype=np.float32)
        self.item_person = np.zeros(0, dtype=np.int32)
        self.item_comp = np.zeros(0, dtype=np.int8)
        self.item_label: list[str] = []
        self.item_extra: list[dict] = []
        self.grant_ids: list[int] = []
        self.grant_vecs = np.zeros((0, 384), dtype=np.float32)
        self.ready = False

    # ---------- embeddings ----------
    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            log.info("Loading embedding model %s", config.EMBEDDING_MODEL)
            self._model = SentenceTransformer(config.EMBEDDING_MODEL, device="cpu")
        return self._model

    def embed(self, db: Session, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        keys = [sha(config.EMBEDDING_MODEL + "|" + t) for t in texts]
        cached = {}
        uniq = list(dict.fromkeys(keys))
        for i in range(0, len(uniq), 500):
            for e in db.scalars(select(Embedding).where(Embedding.text_hash.in_(uniq[i:i + 500]))):
                cached[e.text_hash] = np.frombuffer(e.vector, dtype=np.float32)
        missing = {k: t for k, t in zip(keys, texts) if k not in cached}
        if missing:
            log.info("Embedding %d new texts", len(missing))
            vecs = self.model.encode(list(missing.values()), batch_size=64, normalize_embeddings=True,
                                     show_progress_bar=False).astype(np.float32)
            for k, v in zip(missing.keys(), vecs):
                cached[k] = v
                db.merge(Embedding(text_hash=k, model=config.EMBEDDING_MODEL, vector=v.tobytes()))
            db.commit()
        return np.stack([cached[k] for k in keys])

    def embed_one(self, db: Session, text: str) -> np.ndarray:
        return self.embed(db, [text])[0]

    # ---------- index ----------
    def rebuild(self, db: Session):
        with self._lock:
            self._build_people(db)
            self._build_grants(db)
            self.ready = True

    def _build_people(self, db: Session):
        items = []  # (person_idx, comp, text_to_embed, label, extra)
        people, meta = [], {}

        def add_person(ptype, pid, name, school, dept):
            people.append((ptype, pid))
            meta[(ptype, pid)] = {"name": name, "school": school, "department": dept}
            return len(people) - 1

        interests = defaultdict(list)
        for i in db.scalars(select(Interest).where(Interest.status == "approved")):
            interests[(i.owner_type, i.owner_id)].append(i.text)
        pubs = defaultdict(list)
        for p in db.scalars(select(Publication).where(Publication.status == "approved")):
            pubs[(p.owner_type, p.owner_id)].append(p)
        awards = defaultdict(list)
        for a in db.scalars(select(Award)):
            awards[a.faculty_id].append(a)
        projects = defaultdict(list)
        for p in db.scalars(select(Project)):
            projects[p.faculty_id].append(p)

        def add_common(idx, key, extra_keywords=(), topics=()):
            seen = set()
            for t in interests.get(key, []):
                for frag in split_phrases(t):
                    seen.add(norm_text(frag))
                    items.append((idx, "interests", frag, frag, {"phrase": norm_text(frag)}))
            for t in topics:
                items.append((idx, "topics", t, t, {"phrase": norm_text(t)}))
            kw_seen = set(seen)
            pub_kws = []
            for p in pubs.get(key, []):
                text = p.title + (". " + p.abstract[:700] if p.abstract else "")
                label = f"{p.title}" + (f" ({p.year})" if p.year else "")
                items.append((idx, "publications", text, label, {"pub_id": p.id, "url": p.url}))
                pub_kws += p.keywords or []
            for raw in list(extra_keywords) + pub_kws:
                for k in split_phrases(raw):
                    n = norm_text(k)
                    if n and n not in kw_seen:
                        kw_seen.add(n)
                        items.append((idx, "keywords", k, k, {"phrase": n}))

        for f in db.scalars(select(Faculty).where(Faculty.visible == true()).order_by(Faculty.id)):
            idx = add_person("faculty", f.id, f.full_name, f.school, f.department)
            add_common(idx, ("faculty", f.id), f.research_keywords or [], f.openalex_topics or [])
            for a in awards.get(f.id, []):
                text = f"{a.title}. {a.funder or ''}. {a.description or ''}"[:800]
                items.append((idx, "past_grants", text, f"{a.title}" + (f" — {a.funder}" if a.funder else ""),
                              {"funder": a.funder, "kind": "grant"}))
            for p in projects.get(f.id, []):
                items.append((idx, "past_grants", f"{p.title}. {p.description or ''}"[:800], f"Project: {p.title}",
                              {"kind": "project"}))
        for s in db.scalars(select(Student).where(Student.visible == true()).order_by(Student.id)):
            key = ("student", str(s.id))
            if not interests.get(key) and not pubs.get(key):
                continue  # only students with approved evidence are matched
            idx = add_person("student", str(s.id), s.full_name, s.school, s.major)
            add_common(idx, key)

        vecs = self.embed(db, [t for _, _, t, _, _ in items])
        order = sorted(range(len(items)), key=lambda i: (items[i][0], COMP_KEYS.index(items[i][1])))
        self.people, self.people_meta = people, meta
        self.item_vecs = vecs[order] if len(order) else np.zeros((0, 384), dtype=np.float32)
        self.item_person = np.array([items[i][0] for i in order], dtype=np.int32)
        self.item_comp = np.array([COMP_KEYS.index(items[i][1]) for i in order], dtype=np.int8)
        self.item_label = [items[i][3] for i in order]
        self.item_extra = [items[i][4] for i in order]
        self.phrase_index = defaultdict(list)
        for pos, ex in enumerate(self.item_extra):
            ph = ex.get("phrase")
            if ph and lexical_ok(ph):
                self.phrase_index[ph].append(pos)
        # contiguous (person, comp) groups for vectorised aggregation
        keys = self.item_person.astype(np.int64) * 10 + self.item_comp
        self.group_starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]]) if len(keys) else np.zeros(0, int)
        self.group_person = self.item_person[self.group_starts] if len(keys) else np.zeros(0, int)
        self.group_comp = self.item_comp[self.group_starts] if len(keys) else np.zeros(0, int)
        self.group_ends = np.r_[self.group_starts[1:], len(keys)] if len(keys) else np.zeros(0, int)
        log.info("Index: %d people, %d evidence items", len(people), len(items))

    def _build_grants(self, db: Session):
        grants = list(db.scalars(select(Grant).where(Grant.hidden == false()).order_by(Grant.id)))
        self.grant_ids = [g.id for g in grants]
        texts = [grant_text(g) for g in grants]
        self.grant_vecs = self.embed(db, texts)
        self.grant_ngrams = [ngrams(t) for t in texts]

    def _lexical(self, sims: np.ndarray, ngram_sets: list) -> list[set]:
        """Credit exact phrase matches: an interest/keyword phrase printed in the grant text."""
        hits = []
        for qi, grams in enumerate(ngram_sets):
            h = set()
            for gram in grams:
                h.update(self.phrase_index.get(gram, ()))
            if h:
                idx = np.fromiter(h, dtype=np.int64)
                sims[qi, idx] = np.maximum(sims[qi, idx], LEX_SCORE)
            hits.append(h)
        return hits

    # ---------- scoring ----------
    def _component_scores(self, sims: np.ndarray) -> np.ndarray:
        """sims: (Q, N_items) -> (Q, n_people, n_comps) with NaN where a person lacks a component."""
        q = sims.shape[0]
        out = np.full((q, len(self.people), len(COMP_KEYS)), np.nan, dtype=np.float32)
        if not len(self.group_starts):
            return out
        maxes = np.maximum.reduceat(sims, self.group_starts, axis=1)
        out[:, self.group_person, self.group_comp] = maxes
        pub = COMP_KEYS.index("publications")
        for gi in np.flatnonzero(self.group_comp == pub):
            s, e = self.group_starts[gi], self.group_ends[gi]
            block = sims[:, s:e]
            k = min(3, e - s)
            top = -np.partition(-block, k - 1, axis=1)[:, :k]
            out[:, self.group_person[gi], pub] = top.mean(axis=1)
        return out

    def _final(self, comp: np.ndarray) -> np.ndarray:
        w = np.array([COMPONENTS[k][1]() for k in COMP_KEYS], dtype=np.float32)
        have = ~np.isnan(comp)
        num = np.nansum(comp * w, axis=-1)
        den = (have * w).sum(axis=-1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(den > 0, num / den, 0.0)

    def evidence(self, sims_row: np.ndarray, person_idx: int, comp_row: np.ndarray, funder: str | None = None,
                 exact: set | None = None) -> dict:
        idx = np.flatnonzero(self.item_person == person_idx)
        ev = {}
        for ci, ck in enumerate(COMP_KEYS):
            ids = idx[self.item_comp[idx] == ci]
            if not len(ids):
                continue
            ranked = ids[np.argsort(-sims_row[ids])][:3]
            hits = [{"label": self.item_label[i], "similarity": round(float(sims_row[i]), 3),
                     **({"exact": True} if exact and i in exact else {}),
                     **{k: v for k, v in self.item_extra[i].items() if k in ("url", "pub_id", "kind")}}
                    for i in ranked if sims_row[i] >= config.EVIDENCE_MIN]
            ev[ck] = {"score": None if np.isnan(comp_row[ci]) else round(float(comp_row[ci]), 3), "items": hits}
        if funder:
            same = [self.item_label[i] for i in idx
                    if self.item_extra[i].get("funder") and _same_funder(self.item_extra[i]["funder"], funder)]
            if same:
                ev["same_funder"] = same[:3]
        return ev

    def score_text(self, db: Session, text: str):
        """Rank people for arbitrary text. Returns list of (person_key, score, evidence)."""
        if not self.ready or not len(self.item_vecs):
            return []
        q = self.embed_one(db, text)[None, :]
        sims = q @ self.item_vecs.T
        self._lexical(sims, [ngrams(text)])
        return self._rank(sims, None)[0]

    def _rank(self, sims: np.ndarray, funders: list | None):
        comp = self._component_scores(sims)
        final = self._final(comp)
        results = []
        for qi in range(sims.shape[0]):
            row = []
            for pi in np.argsort(-final[qi]):
                score = float(final[qi, pi])
                if score < config.MATCH_MIN:
                    break
                row.append((pi, score))
            results.append([(self.people[pi], score, (qi, pi)) for pi, score in row])
        self._last = (sims, comp, funders)
        return results

    def match_grants(self, db: Session, grant_ids: list[int] | None = None, person_type: str | None = None):
        """Recompute cached matches for the given grants (all when None). Optionally only one person type."""
        with self._lock:
            if not self.ready:
                self.rebuild(db)
            gidx = {g: i for i, g in enumerate(self.grant_ids)}
            targets = [g for g in (grant_ids or self.grant_ids) if g in gidx]
            if not targets or not len(self.item_vecs):
                return 0
            funders = dict(db.execute(select(Grant.id, Grant.funder).where(Grant.id.in_(targets))).all()) \
                if len(targets) < 900 else dict(db.execute(select(Grant.id, Grant.funder)).all())
            written = 0
            for start in range(0, len(targets), 200):
                chunk = targets[start:start + 200]
                G = self.grant_vecs[[gidx[g] for g in chunk]]
                sims = G @ self.item_vecs.T
                exact = self._lexical(sims, [self.grant_ngrams[gidx[g]] for g in chunk])
                comp = self._component_scores(sims)
                final = self._final(comp)
                q = delete(Match).where(Match.grant_id.in_(chunk))
                if person_type:
                    q = q.where(Match.person_type == person_type)
                db.execute(q)
                for qi, gid in enumerate(chunk):
                    for ptype, top_n in (("faculty", config.MATCH_TOP_FACULTY), ("student", config.MATCH_TOP_STUDENTS)):
                        if person_type and ptype != person_type:
                            continue
                        cand = [pi for pi in np.argsort(-final[qi]) if self.people[pi][0] == ptype
                                and final[qi, pi] >= config.MATCH_MIN][:top_n]
                        for rank, pi in enumerate(cand, 1):
                            ev = self.evidence(sims[qi], pi, comp[qi, pi], funders.get(gid), exact[qi])
                            db.add(Match(grant_id=gid, person_type=ptype, person_id=self.people[pi][1],
                                         score=round(float(final[qi, pi]), 4), rank=rank, evidence=ev,
                                         computed_at=datetime.utcnow()))
                            written += 1
                db.commit()
            return written

    def grants_for_person(self, db: Session, ptype: str, pid: str, limit: int = 20):
        """Reverse matching: grants that fit one person, with evidence. Computed on the fly."""
        if not self.ready:
            self.rebuild(db)
        try:
            pi = self.people.index((ptype, pid))
        except ValueError:
            return []
        mask = self.item_person == pi
        if not mask.any() or not len(self.grant_vecs):
            return []
        sims = self.grant_vecs @ self.item_vecs.T  # (grants, items) — fine at this scale
        exact = self._lexical(sims, self.grant_ngrams)
        comp = self._component_scores(np.where(mask[None, :], sims, -1.0).astype(np.float32))[:, pi, :]
        final = self._final(comp)
        order = np.argsort(-final)
        out = []
        for gi in order:
            if final[gi] < config.MATCH_MIN or len(out) >= limit * 3:
                break
            ev = self.evidence(sims[gi], pi, comp[gi], exact=exact[gi])
            out.append((self.grant_ids[gi], float(final[gi]), ev))
        return out

    def search_grants(self, db: Session, text: str, limit: int = 8):
        if not self.ready or not len(self.grant_vecs):
            return []
        q = self.embed_one(db, text)
        sims = self.grant_vecs @ q
        order = np.argsort(-sims)[:limit * 4]
        return [(self.grant_ids[i], float(sims[i])) for i in order]

    def search_items(self, db: Session, text: str, limit: int = 10):
        """Evidence items (publications, interests...) most similar to text."""
        if not self.ready or not len(self.item_vecs):
            return []
        q = self.embed_one(db, text)
        sims = self.item_vecs @ q
        order = np.argsort(-sims)[:limit]
        return [(self.people[self.item_person[i]], COMP_KEYS[self.item_comp[i]], self.item_label[i], float(sims[i]))
                for i in order]

    def person_vectors(self):
        """Mean evidence vector per person (for AI-suggested clusters)."""
        vecs = np.zeros((len(self.people), self.item_vecs.shape[1]), dtype=np.float32)
        counts = np.bincount(self.item_person, minlength=len(self.people)).astype(np.float32)
        np.add.at(vecs, self.item_person, self.item_vecs)
        vecs /= np.maximum(counts, 1)[:, None]
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.maximum(norms, 1e-6)


def _same_funder(a: str, b: str) -> bool:
    a, b = set(norm_text(a).split()) - {"the", "for", "of", "and"}, set(norm_text(b).split()) - {"the", "for", "of", "and"}
    return bool(a and b) and len(a & b) / min(len(a), len(b)) >= 0.6


engine = Engine()


def label(score: float) -> str:
    if score >= config.MATCH_STRONG:
        return "Strong match"
    return "Possible match"
