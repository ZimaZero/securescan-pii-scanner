#!/usr/bin/env python3
# scanner.py
"""
==============================================================
PII Security Scanner – Command-Line Interface
==============================================================

Purpose:
--------
Non-interactive CLI entry point for running scans via terminal arguments.
Useful for automation, batch jobs, and testing.

This script delegates the full scanning workflow to discovery.scan_path(),
which handles:
  • File discovery + filtering
  • Extraction (including OCR for images/scanned PDFs)
  • Hybrid PII detection (the shared 11-layer engine)
  • Risk scoring + taxonomy classification
  • Report generation (Markdown + JSON + HTML)
"""

import argparse
import os

from config import DEFAULT_MAX_WORKERS
from detectors.hybrid_detector import ALL_LAYERS
from discovery import scan_path
from main import prompt_open_report


def _parse_layers(raw: str) -> frozenset:
    """Parse --layers' comma-separated value into a frozenset of layer names.

    Validated against detectors.hybrid_detector.ALL_LAYERS (the same set
    detect_pii_hybrid() itself uses), so a typo is caught here with a clear
    error rather than silently scanning with fewer layers than intended.
    """
    requested = {name.strip() for name in raw.split(",") if name.strip()}
    unknown = requested - ALL_LAYERS
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown detection layer(s): {', '.join(sorted(unknown))} "
            f"(available: {', '.join(sorted(ALL_LAYERS))})"
        )
    if not requested:
        raise argparse.ArgumentTypeError("--layers requires at least one layer name")
    return frozenset(requested)


def main() -> int:
    """
    CLI entry point for scanning a target file or folder.

    Behaviour:
      - Requires: --path to a file or directory
      - Uses DEFAULT_MAX_WORKERS as the canonical default thread count
      - Delegates orchestration to discovery.scan_path(target_path)
    """
    parser = argparse.ArgumentParser(description="PII Security Scanner (Phase 3) — CLI")
    parser.add_argument(
        "--path",
        required=True,
        help="Path to a file or directory to scan for supported file types",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Never offer to open the HTML report in a browser (just print its path)",
    )
    verify_group = parser.add_mutually_exclusive_group()
    verify_group.add_argument(
        "--verify",
        action="store_true",
        help="Force the LLM (qwen2.5:3b) verification layer ON for this run, "
             "overriding config.LLM_VERIFICATION_ENABLED",
    )
    verify_group.add_argument(
        "--no-verify",
        action="store_true",
        help="Force the LLM verification layer OFF for this run, overriding "
             "config.LLM_VERIFICATION_ENABLED",
    )
    parser.add_argument(
        "--layers",
        type=_parse_layers,
        default=None,
        metavar="LAYER,LAYER,...",
        help="Comma-separated list of detection layers to run for this scan "
             "(default: all). A deselected layer does not run at all, so a "
             "targeted scan is genuinely faster, not just quieter — but "
             "reconciliation between layers (e.g. driver's licence losing a "
             "digit collision to a stronger layer) only sees the layers that "
             "ran, so results are not simply 'the full scan minus that "
             f"layer'. Available layers: {', '.join(sorted(ALL_LAYERS))}.",
    )

    args = parser.parse_args()
    verify_override = True if args.verify else (False if args.no_verify else None)

    # Normalize + validate path
    target_path = os.path.abspath(os.path.expanduser(args.path))

    if not os.path.exists(target_path):
        print(f"[!] Specified path does not exist: {target_path}")
        return 2

    print("=====================================================")
    print("🧩  PII SECURITY SCANNER — Phase 3 CLI")
    print("=====================================================")
    print(f"[+] Starting full scan for: {target_path}")
    print(f"[i] Default worker threads (canonical value): {DEFAULT_MAX_WORKERS}\n")

    try:
        html_path = scan_path(target_path, verify=verify_override, enabled_layers=args.layers)
    except KeyboardInterrupt:
        print("\n[!] Scan interrupted by user (Ctrl+C).")
        return 130
    except Exception as e:
        print(f"[!] An error occurred during scanning: {e}")
        print("[!] Scan aborted prematurely.")
        return 1

    print("\n[✓] Scan completed successfully.")
    print(f"❌ Extraction failures: {getattr(html_path, 'failed_count', 0)}")
    print("[✓] Reports saved in the 'outputs/' directory.")
    if args.no_open:
        if html_path:
            print(f"[i] HTML report: {html_path}")
    else:
        # Prompts only on an interactive terminal; piped/backgrounded runs
        # just get the report path printed.
        prompt_open_report(html_path)
    print("=====================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
