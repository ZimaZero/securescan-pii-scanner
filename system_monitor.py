#!/usr/bin/env python3
# system_monitor.py
"""
System Monitor – SecureScan's own resource-use tracker.

Tracks the SecureScan process and live child processes while a scan runs.
Records peak RSS, peak CPU use, and plain periodic samples without collecting
unrelated system-process details.

Sampling is periodic (every `interval` seconds — default 5s), so peak RSS is
a SAMPLED FLOOR, not a guaranteed maximum: real memory can spike and fall
between two samples without ever being observed. A scan that finishes in
under one interval may produce only one sample (taken immediately at
start()) or, in a very short race, zero.
"""

import threading       # For running monitor loop in background
import time            # For sleep intervals and timing
import psutil          # For process statistics (RSS memory, CPU%)
import os              # For file and path operations
from datetime import datetime  # For timestamps in logs


class SystemMonitor:
    """Tracks SecureScan's own peak RSS/CPU while a scan runs."""

    def __init__(self, interval: int = 5):
        """
        Constructor method. Initializes attributes when class is instantiated.

        Args:
            interval (int): how often (in seconds) to sample RSS/CPU.
                Larger intervals mean fewer samples and a coarser peak
                (see the module docstring: peak RSS is a sampled floor).

        """
        self.interval = interval     # Store sampling frequency
        self.running = False         # Flag to control monitoring loop
        self.thread = None           # Will hold background Thread object
        self.log_path = os.path.join("outputs", "logs", "system_monitor.log")
        os.makedirs(os.path.join("outputs", "logs"), exist_ok=True)  # Ensure log directory exists

        self._process = psutil.Process()
        # psutil.Process.cpu_percent()'s first call always returns 0.0 (it
        # measures elapsed CPU time SINCE THE LAST CALL); priming it here
        # means the first sample taken inside _monitor_loop is meaningful.
        try:
            self._process.cpu_percent(interval=None)
        except Exception:
            pass

        # Peak RSS (MB) / process CPU% observed while running, surfaced to
        # callers via get_peaks(). CPU% is per psutil convention: percent of
        # ONE CPU core, so a multi-threaded scan legitimately exceeds 100%
        # on a multi-core machine.
        self._peak_lock = threading.Lock()
        self.peak_rss_mb = 0.0
        self.peak_cpu_percent = 0.0

    def _log(self, message: str):
        """
        Writes a timestamped message into the system monitor log file.

        Args:
            message (str): text message to log (e.g., a resource sample).
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        # Open log file in append mode so data is preserved across runs
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(line)

    def _live_children(self):
        """This process's live child processes, if any were spawned. Never
        raises — a process that exits mid-iteration is simply skipped."""
        try:
            return self._process.children(recursive=True)
        except Exception:
            return []

    def _sample_rss_mb(self) -> float:
        """RSS for this process, plus any live children, in MB."""
        total_bytes = 0
        try:
            total_bytes += self._process.memory_info().rss
        except Exception:
            pass
        for child in self._live_children():
            try:
                total_bytes += child.memory_info().rss
            except Exception:
                continue  # child exited between children() and this call
        return total_bytes / (1024 * 1024)

    def _sample_cpu_percent(self) -> float:
        """This process's own CPU%, summed with any live children's.
        Percent of a single core (psutil convention) — can exceed 100% on a
        multi-core machine once more than one thread/child is active."""
        total = 0.0
        try:
            total += self._process.cpu_percent(interval=None)
        except Exception:
            pass
        for child in self._live_children():
            try:
                total += child.cpu_percent(interval=None)
            except Exception:
                continue
        return total

    def _monitor_loop(self):
        """
        The main monitoring loop that runs in a separate thread.
        Samples this process's RSS/CPU every `interval` seconds and logs
        each sample as a plain line — no third-party process data.
        """
        self._log("SystemMonitor started.")

        while self.running:
            start = time.monotonic()

            rss_mb = self._sample_rss_mb()
            cpu_percent = self._sample_cpu_percent()
            self._log(f"RSS={rss_mb:.1f}MB  ProcessCPU={cpu_percent:.1f}%")
            with self._peak_lock:
                self.peak_rss_mb = max(self.peak_rss_mb, rss_mb)
                self.peak_cpu_percent = max(self.peak_cpu_percent, cpu_percent)

            # Honest cadence: subtract the time already spent sampling this
            # iteration so the loop period matches self.interval. Guard with
            # self.running so stop() stays responsive.
            elapsed = time.monotonic() - start
            if self.running:
                time.sleep(max(0, self.interval - elapsed))

        self._log("SystemMonitor stopped.\n")

    def start(self):
        """
        Starts the monitoring thread.
        Creates a background daemon thread that runs _monitor_loop().
        """
        if not self.running:
            self.running = True
            # Thread target = method to run; daemon=True means thread exits with main program
            self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.thread.start()

    def stop(self):
        """
        Stops monitoring and waits up to 3 seconds for thread to finish.
        """
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)

    def get_peaks(self) -> dict:
        """Peak RSS (MB) / process CPU% observed since start().

        Zero if no sample landed yet (e.g. stop() raced start()'s thread
        launch). Remember this is a SAMPLED FLOOR, not a guaranteed
        maximum — see the module docstring.
        """
        with self._peak_lock:
            return {
                "peak_rss_mb": round(self.peak_rss_mb, 1),
                "peak_cpu_percent": round(self.peak_cpu_percent, 1),
            }
