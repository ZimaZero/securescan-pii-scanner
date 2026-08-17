#!/usr/bin/env python3
"""
==============================================================
PII Scanner – Interactive Terminal
==============================================================

Purpose:
--------
Provides a simple text-based interface (TUI/CLI) for running
the PII Security Scanner. Available actions:

  1) Scan an entire folder
  2) Scan a single file
  3) View previously generated reports
  4) Exit the program

This script delegates scanning and reporting logic to the
modular components:
  - discovery.py          → scan orchestration (includes skipped files)
  - report_generator.py   → Markdown output
  - report_json.py        → JSON output

==============================================================
"""

import os
import sys
import time
import webbrowser

# --- internal imports ---
import discovery  # handles file discovery & full scan workflow


# -------------------------------------------------------------------
# HELPER – open an HTML report in the default web browser
# -------------------------------------------------------------------
def open_in_browser(html_path: str) -> None:
    """Open an HTML report in the default browser via a file:// URL."""
    abs_path = os.path.abspath(html_path)
    url = "file://" + abs_path
    print(f"[+] Opening {abs_path} in your browser...")
    webbrowser.open(url)


def prompt_open_report(html_path: str) -> None:
    """Ask the user whether to open the HTML report; default Yes on Enter.

    Only prompts when stdin is an interactive terminal. Non-interactive runs
    (piped, backgrounded, CI) skip the prompt and just print the report path.
    """
    if not html_path or not os.path.isfile(html_path):
        return
    if not sys.stdin.isatty():
        print(f"[i] HTML report: {os.path.abspath(html_path)}")
        return
    try:
        answer = input("Open HTML report in browser? [Y/n] ").strip().lower()
    except EOFError:
        return
    if answer in ("", "y", "yes"):
        open_in_browser(html_path)


# -------------------------------------------------------------------
# MAIN MENU LOOP
# -------------------------------------------------------------------
def main():
    """
    Presents the interactive menu to the user and routes options
    to their corresponding actions.

    Keeps looping until the user chooses to Exit (option 4).
    """

    while True:
        # ------------------------------
        # Display the main menu
        # ------------------------------
        print("\n=========================")
        print("   PII SECURITY SCANNER  ")
        print("=========================")
        print("1) Scan local folder")
        print("2) Scan single file")
        print("3) View previous reports")
        print("4) Exit")

        choice = input("Select an option: ").strip()
        start_time = time.time()

        # ======================================================
        # OPTION 1 – SCAN ENTIRE FOLDER
        # ======================================================
        if choice == "1":
            path = input("Folder to scan: ").strip()
            path = os.path.expanduser(path)

            # --- validation check ---
            if not os.path.isdir(path):
                print("[!] Invalid folder.")
                continue

            print(f"\n[+] Starting scan for: {path}")

            # NOTE:
            # discovery.scan_folder() already performs:
            #   • file discovery
            #   • skipping of oversized files
            #   • extraction + detection + scoring
            #   • Markdown + JSON report generation
            #   • terminal summary
            #
            # The return value is not needed by the interactive wrapper.
            # manually call generate_markdown() again.
            # Doing so would overwrite the proper report and lose
            # skipped file data — the bug you just saw.
            try:
                html_path = discovery.scan_folder(path)
            except Exception as e:
                print(f"[!] Scan failed: {e}")
                continue

            # --- elapsed time display ---
            end_time = time.time()
            print(f"⏱️  Scan completed in {end_time - start_time:.2f} seconds")
            print(f"❌ Extraction failures: {getattr(html_path, 'failed_count', 0)}")

            # --- offer to open the HTML report in a browser ---
            prompt_open_report(html_path)

        # ======================================================
        # OPTION 2 – SCAN SINGLE FILE
        # ======================================================
        elif choice == "2":
            file_path = input("File to scan: ").strip()
            file_path = os.path.expanduser(file_path)

            if not os.path.isfile(file_path):
                print("[!] Invalid file path.")
                continue

            print(f"\n[+] Scanning single file: {file_path}")

            try:
                html_path = discovery.scan_path(file_path)
            except Exception as e:
                print(f"[!] Scan failed: {e}")
                continue

            end_time = time.time()
            print(f"⏱️  Scan completed in {end_time - start_time:.2f} seconds")
            print(f"❌ Extraction failures: {getattr(html_path, 'failed_count', 0)}")
            prompt_open_report(html_path)

        # ======================================================
        # OPTION 3 – VIEW PREVIOUS REPORTS
        # ======================================================
        elif choice == "3":
            if not os.path.exists("outputs"):
                print("[i] No reports found yet. Run a scan first.")
                continue

            # Reports live under outputs/YYYY-MM-DD/ (dated subdirectories),
            # plus logs/ and the latest.html pointer at the top level — walk
            # the tree rather than assuming a flat outputs/ directory.
            report_paths = []
            for dirpath, dirnames, filenames in os.walk("outputs"):
                if os.path.basename(dirpath) == "logs":
                    dirnames[:] = []
                    continue
                for name in filenames:
                    if name == "latest.html" or name == ".gui_state.json":
                        continue
                    report_paths.append(os.path.join(dirpath, name))

            # Sort reports by modification time, so newest are last.
            # Using alphabetical sorting can lead to older reports
            # appearing “later” because timestamps differ by minute.
            report_paths.sort(key=lambda p: os.path.getmtime(p))

            if not report_paths:
                print("[i] No reports found.")
                continue

            # FIX: Show only last 5, but index into the same sliced list
            displayed_reports = report_paths[-5:]  # ← NEW: Save the sliced list

            print("\nAvailable reports (latest last):")
            for i, r in enumerate(displayed_reports, 1):
                print(f"{i}) {os.path.relpath(r, 'outputs')}")

            idx = input("Open which file (Enter to cancel)? ").strip()
            if not idx.isdigit():
                continue

            idx = int(idx) - 1
            if 0 <= idx < len(displayed_reports):  # ← Use displayed list
                path = displayed_reports[idx]  # ← Use displayed list
                # HTML reports are markup — open them in the browser instead of
                # dumping raw tags to the terminal. Text-based reports
                # (.md, .json, .log) still print inline.
                if path.lower().endswith(".html"):
                    open_in_browser(path)
                else:
                    try:
                        with open(path, "r", encoding="utf-8", errors="replace") as f:
                            print("\n" + f.read())
                    except Exception as e:
                        print(f"[!] Could not open {path}: {e}")
            else:
                print("[!] Invalid number selection.")

        # ======================================================
        # OPTION 4 – EXIT
        # ======================================================
        elif choice == "4":
            print("Bye 👋")
            sys.exit(0)

        # ======================================================
        # INVALID INPUT
        # ======================================================
        else:
            print("[!] Invalid option, please select 1–4.")


# -------------------------------------------------------------------
# ENTRY GUARD
# -------------------------------------------------------------------
if __name__ == "__main__":
    # Run only when executed directly.
    main()
