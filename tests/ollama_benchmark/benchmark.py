#!/usr/bin/env python3
# tests/ollama_benchmark/benchmark.py
"""
Benchmark llama3.2:3b / qwen2.5:3b / phi3.5 as candidate PII-verification
judges: given {value, detected_type, context, source_layer}, each model must
return strict JSON {"verdict": "LEGITIMATE"|"FALSE_POSITIVE", "reason": "..."}.

Standalone — makes no changes to the scanning pipeline. Run directly:

    docker compose run --rm securescan-cpu python tests/ollama_benchmark/benchmark.py
    docker compose run --rm securescan-cpu python tests/ollama_benchmark/benchmark.py --models llama3.2:3b

Writes tests/ollama_benchmark/RESULTS.md, driven by tests/ollama_benchmark/cases.json.
Requires a running Ollama server (http://localhost:11434) with llama3.2:3b,
qwen2.5:3b, and phi3.5 pulled. Models are run one at a time and unloaded
(keep_alive=0) before the next model starts, so RAM measurements are
per-model clean.

Every (model, case, run) result is appended to raw_results.jsonl as soon as
it completes, and re-runs skip combinations already present there — safe to
Ctrl-C or kill mid-run (e.g. after a slow/frozen call) and resume later
without losing completed work or reloading models that already finished.
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests

OLLAMA_HOST = "http://localhost:11434"
REQUESTED_MODELS = ["llama3.2:3b", "qwen2.5:3b", "phi3.5"]
RUNS_PER_CASE = 2
NUM_PREDICT = 150
TEMPERATURE = 0

# Generous read timeout: this box has intermittently frozen background
# processes for tens of seconds to hours (environment/sandbox scheduling
# artifact, not real model slowness — see RESULTS.md caveats). A call that
# A request that never returns still needs a finite timeout, while
# rather wait a long time than corrupt data with a premature cutoff.
REQUEST_TIMEOUT = (10, 600)  # (connect, read) seconds

# Any successfully-measured latency above this is treated as an
# environment-affected outlier and excluded from median/p95 (but the call's
# verdict/JSON-parse outcome still counts normally).
LATENCY_OUTLIER_THRESHOLD_S = 300

HERE = os.path.dirname(os.path.abspath(__file__))
CASES_PATH = os.path.join(HERE, "cases.json")
RESULTS_PATH = os.path.join(HERE, "RESULTS.md")
RAW_RESULTS_PATH = os.path.join(HERE, "raw_results.jsonl")

SYSTEM_PROMPT = """You are a PII verification judge for an automated PII/sensitive-data scanner.

You will be given a JSON object describing one detection the scanner made:
- "value": the exact string that was flagged
- "detected_type": the taxonomy label the scanner assigned it (e.g. identifier.government.passport_ca, entity.person, contact.phone)
- "context": up to ~200 characters of surrounding text
- "source_layer": which detection layer produced the match (regex, keyword_context, gliner, secrets, health_card, passport, or drivers_license)

Decide whether this is a LEGITIMATE detection (the value really is what it
claims to be, in context) or a FALSE_POSITIVE (the detector matched
something that is not actually that PII type in reality — e.g. a foreign
document's ID number that happens to fit a different country's format, an
OCR misread, a bare digit run swept up by keyword proximity, or a common
English word mistagged as a name).

Base your decision only on the value and context given. Do not invent facts
(e.g. do not claim to have computed a checksum) beyond what is stated.

Respond with STRICT JSON only, no markdown fences, no extra commentary,
in exactly this shape:
{"verdict": "LEGITIMATE", "reason": "<one line>"}
or
{"verdict": "FALSE_POSITIVE", "reason": "<one line>"}
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ============================================================
#  PREFLIGHT
# ============================================================

def preflight(requested_models):
    try:
        requests.get(f"{OLLAMA_HOST}/api/version", timeout=10).raise_for_status()
    except requests.exceptions.RequestException as e:
        sys.exit(f"ABORT: Ollama server not reachable at {OLLAMA_HOST} ({e}). Start it with `ollama serve`.")

    try:
        tags = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=10).json()
    except requests.exceptions.RequestException as e:
        sys.exit(f"ABORT: /api/tags failed ({e}).")

    installed = [m["name"] for m in tags.get("models", [])]
    resolved = {}
    for requested in requested_models:
        match = None
        if requested in installed:
            match = requested
        else:
            for name in installed:
                if name == requested or name.split(":")[0] == requested.split(":")[0]:
                    match = name
                    break
        if match is None:
            sys.exit(
                f"ABORT: model '{requested}' not found in `ollama list` output {installed}. "
                f"Pull it with `ollama pull {requested}` first."
            )
        resolved[requested] = match

    print("Preflight OK. Resolved tags:")
    for requested, actual in resolved.items():
        print(f"  {requested} -> {actual}")
    return resolved


# ============================================================
#  MODEL CALLS
# ============================================================

def build_user_content(case):
    return json.dumps(
        {
            "value": case["value"],
            "detected_type": case["detected_type"],
            "context": case["context"],
            "source_layer": case["source_layer"],
        }
    )


def extract_json(text):
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def call_model_once(model_tag, case):
    """One HTTP round trip.

    Returns (parsed_or_none, raw_text, latency_seconds, error_type). error_type
    is None on a clean HTTP round trip (JSON parse may still have failed),
    or "timeout"/"connection_error"/"http_error" if the request itself blew up.
    """
    payload = {
        "model": model_tag,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_content(case)},
        ],
        "stream": False,
        "options": {"temperature": TEMPERATURE, "num_predict": NUM_PREDICT, "num_thread": 6},
    }
    start = time.perf_counter()
    try:
        resp = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        return None, "", time.perf_counter() - start, "timeout"
    except requests.exceptions.ConnectionError:
        return None, "", time.perf_counter() - start, "connection_error"
    except requests.exceptions.RequestException as e:
        return None, str(e), time.perf_counter() - start, "http_error"

    latency = time.perf_counter() - start
    raw_text = resp.json().get("message", {}).get("content", "")
    parsed = extract_json(raw_text)
    if isinstance(parsed, dict) and parsed.get("verdict") in ("LEGITIMATE", "FALSE_POSITIVE"):
        return parsed, raw_text, latency, None
    return None, raw_text, latency, None


def call_model_with_retry(model_tag, case):
    """Up to one retry total, whether the first attempt failed at the
    transport level (timeout/connection) or just produced unparseable JSON.
    Returns a dict describing the final outcome plus what went wrong, if
    anything, distinguishing environment failures from JSON failures.
    """
    parsed, raw, lat1, err1 = call_model_once(model_tag, case)
    latencies = [lat1]
    retried = False
    env_timeout = err1 is not None

    if parsed is None:
        retried = True
        parsed, raw, lat2, err2 = call_model_once(model_tag, case)
        latencies.append(lat2)
        env_timeout = env_timeout or (err2 is not None)

    return {
        "verdict": parsed["verdict"] if parsed else None,
        "reason": parsed.get("reason", "") if parsed else raw[:200],
        "latencies": latencies,
        "retried": retried,
        "parsed_ok": parsed is not None,
        "env_timeout": env_timeout,
    }


def get_ram_mb(model_tag):
    try:
        ps = requests.get(f"{OLLAMA_HOST}/api/ps", timeout=10).json()
    except requests.exceptions.RequestException:
        return None
    for m in ps.get("models", []):
        if m.get("name") == model_tag or m.get("model") == model_tag:
            size = m.get("size")
            return round(size / (1024 * 1024), 1) if size else None
    return None


def unload_model(model_tag):
    try:
        requests.post(
            f"{OLLAMA_HOST}/api/chat", json={"model": model_tag, "messages": [], "keep_alive": 0}, timeout=30
        )
    except requests.exceptions.RequestException as e:
        print(f"  (warning: API unload for {model_tag} failed: {e}, trying `ollama stop`)")
        subprocess.run(["ollama", "stop", model_tag], check=False)


# ============================================================
#  RESUMABLE RAW RESULTS LOG
# ============================================================

def load_raw_results():
    """Returns (case_runs, ram_samples). case_runs keyed by (model_tag, case_id, run_idx)."""
    case_runs = {}
    ram_samples = {}
    if not os.path.exists(RAW_RESULTS_PATH):
        return case_runs, ram_samples
    with open(RAW_RESULTS_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec["type"] == "case_run":
                case_runs[(rec["model_tag"], rec["case_id"], rec["run_idx"])] = rec
            elif rec["type"] == "ram_sample":
                ram_samples[rec["model_tag"]] = rec["ram_mb"]
    return case_runs, ram_samples


def append_raw_result(rec):
    with open(RAW_RESULTS_PATH, "a") as f:
        f.write(json.dumps(rec) + "\n")
        f.flush()


# ============================================================
#  BENCHMARK LOOP
# ============================================================

def run_model(model_tag, cases, existing_case_runs, existing_ram):
    already_done = all((model_tag, c["id"], run_idx) in existing_case_runs for c in cases for run_idx in range(RUNS_PER_CASE))

    print(f"\n=== {model_tag} ===")
    if already_done:
        print("  all cases already in raw_results.jsonl — skipping model load entirely")

    all_latencies = []
    parse_attempts = 0
    parse_successes = 0
    retries_used = 0
    env_timeouts = 0
    hallucinations = []
    per_case = {}
    ram_mb = existing_ram.get(model_tag)

    for case in cases:
        runs = []
        for run_idx in range(RUNS_PER_CASE):
            key = (model_tag, case["id"], run_idx)
            if key in existing_case_runs:
                result = existing_case_runs[key]
            else:
                result = call_model_with_retry(model_tag, case)
                rec = {
                    "type": "case_run",
                    "model_tag": model_tag,
                    "case_id": case["id"],
                    "run_idx": run_idx,
                    "ts": now_iso(),
                    **result,
                }
                append_raw_result(rec)
                result = rec

                if ram_mb is None:
                    ram_mb = get_ram_mb(model_tag)
                    if ram_mb is not None:
                        append_raw_result({"type": "ram_sample", "model_tag": model_tag, "ram_mb": ram_mb, "ts": now_iso()})

            all_latencies.extend(result["latencies"])
            parse_attempts += len(result["latencies"])
            if result["parsed_ok"]:
                parse_successes += 1
            if result["retried"]:
                retries_used += 1
            if result.get("env_timeout"):
                env_timeouts += 1
            runs.append(result)

            watch = case.get("hallucination_watch", [])
            if watch and result["reason"]:
                reason_lower = result["reason"].lower()
                for phrase in watch:
                    if phrase.lower() in reason_lower:
                        hallucinations.append((case["id"], run_idx, result["reason"]))
                        break

        per_case[case["id"]] = runs
        status = "+".join(r["verdict"] or "PARSE_FAIL" for r in runs)
        print(f"  case {case['id']:2d} [{case['expected_verdict']:14s}] -> {status}")

    if not already_done:
        unload_model(model_tag)
        print(f"  (unloaded {model_tag})")

    return {
        "model_tag": model_tag,
        "per_case": per_case,
        "all_latencies": all_latencies,
        "parse_attempts": parse_attempts,
        "parse_successes": parse_successes,
        "retries_used": retries_used,
        "env_timeouts": env_timeouts,
        "hallucinations": hallucinations,
        "ram_mb": ram_mb,
    }


def score_model(run_result, cases):
    per_case = run_result["per_case"]
    fp_cases = [c for c in cases if c["expected_verdict"] == "FALSE_POSITIVE"]
    legit_cases = [c for c in cases if c["expected_verdict"] == "LEGITIMATE"]

    def case_correct(case):
        runs = per_case[case["id"]]
        return all(r["verdict"] == case["expected_verdict"] for r in runs)

    fp_correct = sum(1 for c in fp_cases if case_correct(c))
    legit_correct = sum(1 for c in legit_cases if case_correct(c))
    total_correct = fp_correct + legit_correct

    clean_latencies = sorted(t for t in run_result["all_latencies"] if t <= LATENCY_OUTLIER_THRESHOLD_S)
    excluded = len(run_result["all_latencies"]) - len(clean_latencies)
    median_lat = statistics.median(clean_latencies) if clean_latencies else float("nan")
    p95_lat = clean_latencies[max(0, int(len(clean_latencies) * 0.95) - 1)] if clean_latencies else float("nan")

    return {
        "fp_catch_pct": 100 * fp_correct / len(fp_cases),
        "tp_preserve_pct": 100 * legit_correct / len(legit_cases),
        "overall_pct": 100 * total_correct / len(cases),
        "json_reliability_pct": 100 * run_result["parse_successes"] / run_result["parse_attempts"]
        if run_result["parse_attempts"]
        else 0,
        "retries_used": run_result["retries_used"],
        "env_timeouts": run_result["env_timeouts"],
        "median_latency_s": median_lat,
        "p95_latency_s": p95_lat,
        "latency_excluded_count": excluded,
        "ram_mb": run_result["ram_mb"],
        "fp_correct": fp_correct,
        "fp_total": len(fp_cases),
        "legit_correct": legit_correct,
        "legit_total": len(legit_cases),
    }


# ============================================================
#  REPORT
# ============================================================

def build_case_matrix(cases, all_run_results):
    lines = [
        "| Case | Expected | " + " | ".join(all_run_results.keys()) + " |",
        "|---|---|" + "---|" * len(all_run_results),
    ]
    for case in cases:
        row = [f"{case['id']}. {case['description'][:40]}", case["expected_verdict"]]
        for model_tag, run_result in all_run_results.items():
            runs = run_result["per_case"][case["id"]]
            verdicts = [r["verdict"] or "PARSE_FAIL" for r in runs]
            agree = len(set(verdicts)) == 1
            correct = agree and verdicts[0] == case["expected_verdict"]
            mark = "PASS" if correct else "FAIL"
            row.append(f"{mark} ({'/'.join(verdicts)})")
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def write_results_md(cases, all_run_results, all_scores):
    lines = []
    lines.append("# Ollama Verification-Judge Benchmark Results\n")
    lines.append(
        "Benchmarks `llama3.2:3b`, `qwen2.5:3b`, and `phi3.5` as candidate judges for the "
        "planned Ollama verification layer (see CLAUDE.md \"Current phase\"). Each model was "
        f"given the same {len(cases)} cases (8 documented/expected FALSE_POSITIVE, 8 expected "
        f"LEGITIMATE) twice each ({RUNS_PER_CASE} runs/case), one model resident in RAM at a "
        "time, unloaded (`keep_alive: 0`) before the next model started. `format: \"json\"` was "
        "deliberately NOT used so JSON reliability is a real measurement of each model's "
        "instruction-following, not an artifact of a forced grammar; production can layer "
        "`format: \"json\"` on top of whichever model wins as a safety net.\n"
    )
    lines.append(
        "**Environment note:** this box intermittently froze background/foreground processes "
        "for tens of seconds up to several hours during benchmarking (observed via `ollama`'s "
        "own server logs showing multi-hour gaps between prompt processing and completion, "
        "with CPU otherwise idle) — a sandbox/VM scheduling artifact, not real model inference "
        f"speed. Any single measured latency over {LATENCY_OUTLIER_THRESHOLD_S}s is excluded "
        "from the median/p95 figures below as environment noise (the call's verdict/JSON-parse "
        "outcome still counts normally); the exclusion count is reported per model. Treat "
        "latency numbers as directional, not authoritative wall-clock guarantees.\n"
    )

    lines.append("## Results table\n")
    lines.append(
        "| Model | FP-catch % | TP-preserve % | Overall % | JSON reliability % | Retries | Env timeouts | "
        "Median latency (s) | p95 latency (s) | Latency outliers excluded | RAM (MB) |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for model_tag, s in all_scores.items():
        lines.append(
            f"| `{model_tag}` | {s['fp_catch_pct']:.0f}% ({s['fp_correct']}/{s['fp_total']}) | "
            f"{s['tp_preserve_pct']:.0f}% ({s['legit_correct']}/{s['legit_total']}) | "
            f"{s['overall_pct']:.0f}% | {s['json_reliability_pct']:.0f}% | {s['retries_used']} | "
            f"{s['env_timeouts']} | {s['median_latency_s']:.2f} | {s['p95_latency_s']:.2f} | "
            f"{s['latency_excluded_count']} | {s['ram_mb'] if s['ram_mb'] is not None else 'n/a'} |"
        )

    lines.append(
        "\nFP-catch % = fraction of the 8 FALSE_POSITIVE cases where **both** runs correctly "
        "said FALSE_POSITIVE. TP-preserve % = same, for the 8 LEGITIMATE cases — this is the "
        "more important number, since a judge that kills real detections is worse than one "
        "that lets a few false positives through unverified. \"Env timeouts\" = number of "
        "(case, run) calls where at least one attempt hit a transport-level timeout/connection "
        "error (counted separately from JSON parse failures, which fall under JSON reliability).\n"
    )

    lines.append("## Per-case verdict matrix\n")
    lines.append(build_case_matrix(cases, all_run_results))
    lines.append("")

    lines.append("## Hallucinated justifications\n")
    any_halluc = False
    for model_tag, run_result in all_run_results.items():
        if run_result["hallucinations"]:
            any_halluc = True
            lines.append(f"**{model_tag}:**")
            for case_id, run_idx, reason in run_result["hallucinations"]:
                lines.append(f"- case {case_id}, run {run_idx + 1}: \"{reason}\"")
    if not any_halluc:
        lines.append(
            "None detected by the watch-phrase heuristic (cases 1 and 2 were checked for "
            "invented checksum/format claims that don't apply to passport or Aadhaar-collision "
            "matches)."
        )
    lines.append("")

    lines.append("## Recommendation\n")
    lines.append("_(fill in after reviewing the table above)_\n")

    with open(RESULTS_PATH, "w") as f:
        f.write("\n".join(lines))
    print(f"\nWrote {RESULTS_PATH}")


# ============================================================
#  MAIN
# ============================================================

def main():
    global SYSTEM_PROMPT, RAW_RESULTS_PATH, RESULTS_PATH

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models",
        default=",".join(REQUESTED_MODELS),
        help="Comma-separated subset of models to run (default: all three).",
    )
    parser.add_argument(
        "--prompt-from",
        choices=["benchmark", "verifier"],
        default="benchmark",
        help="Which SYSTEM_PROMPT to use: this module's own (default, for "
             "run-1/run-2 comparability) or detectors/llm_verifier.py's "
             "current prompt (for regression-testing prompt changes against "
             "these same cases). Does NOT alter this module's own default.",
    )
    parser.add_argument(
        "--raw-results-path",
        default=None,
        help="Override the resumable raw-results JSONL path (default: "
             "raw_results.jsonl next to this script). Use a different path "
             "for a --prompt-from verifier rerun so it doesn't silently "
             "resume/skip cases already recorded under the default prompt.",
    )
    parser.add_argument(
        "--results-path",
        default=None,
        help="Override the RESULTS.md output path (default: RESULTS.md next "
             "to this script). Use a different path to avoid overwriting "
             "the committed benchmark writeup.",
    )
    args = parser.parse_args()
    requested_models = [m.strip() for m in args.models.split(",") if m.strip()]

    if args.prompt_from == "verifier":
        project_root = os.path.dirname(os.path.dirname(HERE))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from detectors.llm_verifier import SYSTEM_PROMPT as _verifier_prompt
        SYSTEM_PROMPT = _verifier_prompt
        print("[prompt-from=verifier] using detectors/llm_verifier.py's current SYSTEM_PROMPT\n")
    if args.raw_results_path:
        RAW_RESULTS_PATH = args.raw_results_path
    if args.results_path:
        RESULTS_PATH = args.results_path

    resolved = preflight(requested_models)

    with open(CASES_PATH) as f:
        cases = json.load(f)["cases"]

    existing_case_runs, existing_ram = load_raw_results()
    if existing_case_runs:
        print(f"Resuming: {len(existing_case_runs)} (model, case, run) results already in {RAW_RESULTS_PATH}")

    all_run_results = {}
    all_scores = {}
    for requested in requested_models:
        model_tag = resolved[requested]
        run_result = run_model(model_tag, cases, existing_case_runs, existing_ram)
        all_run_results[model_tag] = run_result
        all_scores[model_tag] = score_model(run_result, cases)

    write_results_md(cases, all_run_results, all_scores)

    print("\n=== Summary ===")
    for model_tag, s in all_scores.items():
        print(
            f"{model_tag}: FP-catch {s['fp_catch_pct']:.0f}%  TP-preserve {s['tp_preserve_pct']:.0f}%  "
            f"JSON {s['json_reliability_pct']:.0f}%  env_timeouts {s['env_timeouts']}  "
            f"median {s['median_latency_s']:.2f}s  RAM {s['ram_mb']}MB"
        )


if __name__ == "__main__":
    main()
