#!/usr/bin/env python3
# tests/ner_benchmark/benchmark.py
"""
Standalone NER benchmark: Presidio (spaCy) vs GLiNER (small/medium/large), each
detecting EVERYTHING it can — full entity coverage, no suppression. NOT wired
into hybrid_detector.

Run:
    docker compose run --rm securescan-cpu python tests/ner_benchmark/benchmark.py

Presidio is built fresh here (NOT the project's suppressed wrapper): all
supported entities, score_threshold=0.0. GLiNER runs the broad label set from
dataset.py at a modest threshold. Both thresholds are printed. Each model is run
over every document; scores are micro-averaged across documents.

Set BENCH_GLINER_SIZES=small,medium (env) to limit which GLiNER sizes run.
"""

import os
import re
import gc
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dataset import (
    DOCUMENTS, CANONICAL, STRUCTURED,
    PRESIDIO_MAP, GLINER_LABELS, GLINER_MAP,
)

PRESIDIO_THRESHOLD = 0.0
GLINER_THRESHOLD = 0.30
SPEED_ITERS = 3

GLINER_SIZES = os.environ.get("BENCH_GLINER_SIZES", "small,medium,large").split(",")
GLINER_MODELS = {
    "small":  "urchade/gliner_small-v2.1",
    "medium": "urchade/gliner_medium-v2.1",
    "large":  "urchade/gliner_large-v2.1",
}


# ============================================================
#  MATCHING / METRICS
# ============================================================

def _norm_text(s):
    return re.sub(r"\s+", " ", s.strip().lower())


def _norm_num(s):
    return re.sub(r"[^0-9a-z]", "", s.lower())


def _overlap(det_val, gt_val, kind):
    a, b = (_norm_num(det_val), _norm_num(gt_val)) if kind == "numeric" \
        else (_norm_text(det_val), _norm_text(gt_val))
    if not a or not b:
        return 0
    if a in b or b in a:
        return min(len(a), len(b))
    return 0


def dedup(dets):
    best = {}
    for d in dets:
        key = (d["canonical"], _norm_text(d["value"]))
        if key not in best or d["score"] > best[key]["score"]:
            best[key] = d
    return list(best.values())


def greedy_match(dets, gts):
    """1-to-1 greedy match by overlap; tie-break prefers correct-type then score."""
    pairs = []
    for di, d in enumerate(dets):
        for gi, g in enumerate(gts):
            s = _overlap(d["value"], g["value"], g["kind"])
            if s > 0:
                correct = 1 if d["canonical"] == g["category"] else 0
                pairs.append((s, correct, d["score"], di, gi))
    pairs.sort(reverse=True)
    d_used, g_used, match = set(), set(), {}
    for s, correct, score, di, gi in pairs:
        if di in d_used or gi in g_used:
            continue
        d_used.add(di); g_used.add(gi); match[gi] = di
    unmatched = [di for di in range(len(dets)) if di not in d_used]
    return match, unmatched


def prf(t, f, n):
    p = t / (t + f) if (t + f) else 0.0
    r = t / (t + n) if (t + n) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f1


def score_model(dets, gts):
    match, unmatched = greedy_match(dets, gts)
    per_cat = {c: {"tp": 0, "fp": 0, "fn": 0} for c in CANONICAL}
    tp = fp = fn = 0
    item_rows, false_positives = [], []

    for gi, g in enumerate(gts):
        if gi in match:
            d = dets[match[gi]]
            correct = (d["canonical"] == g["category"])
            if correct:
                tp += 1; per_cat[g["category"]]["tp"] += 1
            else:
                fn += 1; fp += 1
                per_cat[g["category"]]["fn"] += 1
                if d["canonical"] in per_cat:
                    per_cat[d["canonical"]]["fp"] += 1
                false_positives.append({"value": d["value"], "as": d["type_raw"],
                                        "reason": f"mistype (true {g['category']})", "score": d["score"]})
            item_rows.append({"gt": g, "detected": True, "correct": correct,
                              "as_canonical": d["canonical"], "as_type": d["type_raw"]})
        else:
            fn += 1; per_cat[g["category"]]["fn"] += 1
            item_rows.append({"gt": g, "detected": False, "correct": False,
                              "as_canonical": "-", "as_type": "-"})

    for di in unmatched:
        d = dets[di]; fp += 1
        if d["canonical"] in per_cat:
            per_cat[d["canonical"]]["fp"] += 1
        false_positives.append({"value": d["value"], "as": d["type_raw"],
                                "reason": "spurious (no GT match)", "score": d["score"]})

    p, r, f1 = prf(tp, fp, fn)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1,
            "per_cat": per_cat, "item_rows": item_rows, "false_positives": false_positives}


# ============================================================
#  MODEL RUNNERS  (predict_fn(text) -> list of raw detection dicts)
# ============================================================

def make_presidio():
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    config = {"nlp_engine_name": "spacy",
              "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}]}
    engine = NlpEngineProvider(nlp_configuration=config).create_engine()
    analyzer = AnalyzerEngine(nlp_engine=engine)

    def predict(text):
        res = analyzer.analyze(text=text, language="en", entities=None,
                               score_threshold=PRESIDIO_THRESHOLD)
        return [{"type_raw": r.entity_type,
                 "canonical": PRESIDIO_MAP.get(r.entity_type, r.entity_type),
                 "value": text[r.start:r.end], "score": float(r.score)} for r in res]
    return predict, None


def make_gliner(repo):
    from gliner import GLiNER
    model = GLiNER.from_pretrained(repo)

    def predict(text):
        res = model.predict_entities(text, GLINER_LABELS, threshold=GLINER_THRESHOLD)
        return [{"type_raw": e["label"],
                 "canonical": GLINER_MAP.get(e["label"], e["label"]),
                 "value": e["text"], "score": float(e["score"])} for e in res]
    return predict, model


def run_model_over_docs(predict):
    """Returns {doc_name: {"dets":deduped, "score":..., "ms":...}} + aggregate."""
    out = {}
    for doc in DOCUMENTS:
        dets = dedup(predict(doc["text"]))
        s = score_model(dets, doc["ground_truth"])
        t0 = time.perf_counter()
        for _ in range(SPEED_ITERS):
            predict(doc["text"])
        ms = (time.perf_counter() - t0) / SPEED_ITERS * 1000
        out[doc["name"]] = {"dets": dets, "score": s, "ms": ms}
    return out


def aggregate(per_doc):
    tp = sum(d["score"]["tp"] for d in per_doc.values())
    fp = sum(d["score"]["fp"] for d in per_doc.values())
    fn = sum(d["score"]["fn"] for d in per_doc.values())
    ndet = sum(len(d["dets"]) for d in per_doc.values())
    ms = sum(d["ms"] for d in per_doc.values()) / len(per_doc)
    per_cat = {c: {"tp": 0, "fp": 0, "fn": 0} for c in CANONICAL}
    for d in per_doc.values():
        for c in CANONICAL:
            for k in ("tp", "fp", "fn"):
                per_cat[c][k] += d["score"]["per_cat"][c][k]
    p, r, f1 = prf(tp, fp, fn)
    return {"tp": tp, "fp": fp, "fn": fn, "ndet": ndet, "ms": ms,
            "precision": p, "recall": r, "f1": f1, "per_cat": per_cat}


# ============================================================
#  REPORT
# ============================================================

def hr(c="="):
    print(c * 92)


def type_breakdown(dets):
    counts = {}
    for d in dets:
        counts[d["type_raw"]] = counts.get(d["type_raw"], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def cell(r):
    if not r["detected"]:
        return "MISS"
    return ("OK:" if r["correct"] else "MIS:") + r["as_canonical"]


def per_item_table(doc, model_names, results):
    gts = doc["ground_truth"]
    hr()
    print(f"  PER-ITEM — {doc['name']}   ({len(gts)} ground-truth items)")
    hr()
    header = f"{'#':>2} {'category':11} {'value':38} " + \
        " ".join(f"{m:>13}" for m in model_names)
    print(header)
    print("-" * len(header))
    rows_by_model = {m: results[m][doc["name"]]["score"]["item_rows"] for m in model_names}
    for i, g in enumerate(gts):
        val = g["value"] if len(g["value"]) <= 36 else g["value"][:35] + "…"
        cells = " ".join(f"{cell(rows_by_model[m][i]):>13}" for m in model_names)
        print(f"{i+1:>2} {g['category']:11} {val:38} {cells}")


def main():
    results = {}       # model_name -> per_doc dict
    agg = {}           # model_name -> aggregate
    order = ["Presidio"] + [f"GLiNER-{s}" for s in GLINER_SIZES]

    print("Loading + running Presidio…")
    predict, _ = make_presidio()
    results["Presidio"] = run_model_over_docs(predict)
    agg["Presidio"] = aggregate(results["Presidio"])
    del predict; gc.collect()

    for size in GLINER_SIZES:
        repo = GLINER_MODELS[size]
        name = f"GLiNER-{size}"
        print(f"Loading + running {name} ({repo})…")
        try:
            predict, model = make_gliner(repo)
        except Exception as e:
            print(f"  !! {name} unavailable: {type(e).__name__}: {str(e)[:120]}")
            order.remove(name)
            continue
        results[name] = run_model_over_docs(predict)
        agg[name] = aggregate(results[name])
        del predict, model; gc.collect()

    # ---- raw detections per model per doc ----
    for m in order:
        for doc in DOCUMENTS:
            dets = results[m][doc["name"]]["dets"]
            hr("-")
            print(f"{m}  |  {doc['name']}  |  {len(dets)} unique detections  "
                  f"|  {results[m][doc['name']]['ms']:.1f} ms/doc")
            print("  by type:", type_breakdown(dets))
            for d in sorted(dets, key=lambda d: -d["score"]):
                print(f"    {d['type_raw']:22} {d['value']!r:52} {d['score']:.3f}")

    # ---- per-item tables ----
    for doc in DOCUMENTS:
        per_item_table(doc, order, results)
    print("\nlegend: OK:<type>=correct | MIS:<type>=detected but mistyped | MISS=missed")

    # ---- aggregate headline ----
    hr()
    print("  HEADLINE — micro-averaged across BOTH documents")
    hr()
    print(f"{'model':12}{'dets':>6}{'TP':>5}{'FP':>5}{'FN':>5}{'P':>8}{'R':>8}{'F1':>8}{'ms/doc':>9}")
    for m in order:
        a = agg[m]
        print(f"{m:12}{a['ndet']:>6}{a['tp']:>5}{a['fp']:>5}{a['fn']:>5}"
              f"{a['precision']:>8.3f}{a['recall']:>8.3f}{a['f1']:>8.3f}{a['ms']:>9.1f}")

    # ---- per-category F1 side by side ----
    hr()
    print("  PER-CATEGORY F1 — micro-averaged across both docs")
    hr()
    print(f"{'category':12}" + "".join(f"{m:>13}" for m in order))
    for c in CANONICAL:
        gt_n = sum(1 for doc in DOCUMENTS for g in doc["ground_truth"] if g["category"] == c)
        line = f"{c:12}"
        for m in order:
            pc = agg[m]["per_cat"][c]
            _, _, f1 = prf(pc["tp"], pc["fp"], pc["fn"])
            line += f"{f1:>13.2f}"
        print(line + f"   (gt={gt_n})")

    # ---- GLiNER size sweep on STRUCTURED PII (the gap-closing question) ----
    hr()
    print("  GLiNER SIZE SWEEP — recall on STRUCTURED PII (does bigger close the gap?)")
    hr()
    gliner_names = [m for m in order if m.startswith("GLiNER")]
    print(f"{'category':12}{'Presidio':>10}" + "".join(f"{m:>13}" for m in gliner_names))
    for c in STRUCTURED:
        gt_n = sum(1 for doc in DOCUMENTS for g in doc["ground_truth"] if g["category"] == c)
        pc = agg["Presidio"]["per_cat"][c]
        _, pr, _ = prf(pc["tp"], pc["fp"], pc["fn"])
        line = f"{c:12}{pr:>10.2f}"
        for m in gliner_names:
            pcg = agg[m]["per_cat"][c]
            _, rr, _ = prf(pcg["tp"], pcg["fp"], pcg["fn"])
            line += f"{rr:>13.2f}"
        print(line + f"   (gt={gt_n}, recall)")

    # ---- false positive counts ----
    hr()
    print("  FALSE POSITIVES (count across both docs)")
    hr()
    for m in order:
        n = sum(len(results[m][doc["name"]]["score"]["false_positives"]) for doc in DOCUMENTS)
        print(f"  {m:12} {n} FPs")

    # ---- machine-readable dump ----
    dump = {}
    for m in order:
        dump[m] = {"aggregate": {k: agg[m][k] for k in
                                 ("ndet", "tp", "fp", "fn", "precision", "recall", "f1", "ms")},
                   "per_doc": {doc["name"]: {
                       "detections": results[m][doc["name"]]["dets"],
                       "false_positives": results[m][doc["name"]]["score"]["false_positives"],
                       "tp": results[m][doc["name"]]["score"]["tp"],
                       "fp": results[m][doc["name"]]["score"]["fp"],
                       "fn": results[m][doc["name"]]["score"]["fn"],
                   } for doc in DOCUMENTS}}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.json")
    with open(path, "w") as fh:
        json.dump(dump, fh, indent=2, default=str)
    print(f"\n[raw results written to {path}]")


if __name__ == "__main__":
    main()
