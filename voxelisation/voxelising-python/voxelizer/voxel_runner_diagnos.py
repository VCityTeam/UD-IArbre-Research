r"""
Standalone run-time diagnostics for the voxelizer.
@ingroup t3_orchestr


This module is deliberately **decoupled from every visualisation file**
(visualizer3d.py, viz3d_cli.py, visualization.py).
It imports nothing from the ``voxelizer`` package. You can drop it anywhere and
it will still run; it treats the thing it is monitoring as a black box.

What it records
---------------
For the process being watched (and its whole child-process tree) it samples,
on a background thread at a fixed interval:

    * CPU  - process CPU %, and system-wide CPU %
    * threads - total live threads across the process tree
    * RAM  - process resident set size (RSS) + system RAM used / total / %
    * disk - (a) bytes read/written by the process (+ instantaneous rate)
             (b) free / total space on the drive holding the run directory
    * process count - how many processes are in the tree right now

...plus, when a crash happens, **the exact file and line it happened on**
(parsed from the Python traceback), the exception type/message, the process
exit code / terminating signal, and the last resource snapshot before death
(so an out-of-memory kill is obvious even when there is no Python traceback).

Where it writes
---------------
Everything goes under ``<run_dir>/diagnosis/``:

    diagnosis/
        environment.txt     one-shot: python, OS, CPU count, total RAM, argv...
        resources.jsonl      one JSON object per sample, flushed immediately
        run.log              tee of the watched process's stdout+stderr
        faulthandler.log     low-level dump on fatal (C-level) crashes
        summary.txt          peaks + totals + duration + exit status
        summary.json         same, machine-readable
        crash.txt            ONLY if it crashed: traceback + file:line + last sample

``resources.jsonl`` is append-only and flushed every sample, which is what
makes the three real-time viewing options below possible.

Real-time viewing - three tiers
--------------------------------
1. Console (zero setup). While a run is monitored, a compact one-liner is
   printed every ``--log-every`` seconds, e.g.
       [diag t=12.5s] CPU 143%/38% sys  RAM 2.1 GB proc, 6.3/16.0 GB (39%)  thr 9  disk r/w 30.0 MB/12.4 MB
2. Tail the JSONL from a second terminal (zero setup, works while running):
       PowerShell : ``Get-Content -Wait -Tail 20 <run_dir>\diagnosis\resources.jsonl``
       bash       : ``tail -f <run_dir>/diagnosis/resources.jsonl``
3. Live browser dashboard (stdlib only, no internet needed):
       add  --serve  to the command below, or attach to a run already in
       progress with:
           ``python voxel_runner_diagnos.py --view <run_dir>/diagnosis --serve``

Docker / container notes
------------------------
The wrapper, crash capture (Python tracebacks, faulthandler dumps, and
negative-signal exit decoding: -11 = SIGSEGV, -9 = SIGKILL/OOM kill) and all
per-process metrics work unchanged inside Linux containers. Four things to
know when dockerising:

* Dashboard: publish the port (``docker run -p 8770:8770 ...``). The image
  sets ``VOXELIZER_BIND=0.0.0.0``, which this module's ``--host`` default
  honours, so ``--serve`` alone is reachable from the host browser; outside
  a container the default stays loopback-only (or pass ``--host 0.0.0.0``
  explicitly). ``--no-open`` (or ``VOXELIZER_NO_BROWSER=1``) silences the
  (already harmless) headless browser attempt.
* System-wide numbers (``sys_ram_*``, ``sys_cpu_percent``) come from psutil
  and describe the HOST, not the container. The container's own budget is
  sampled separately per tick as ``cgroup_ram_used_bytes`` /
  ``cgroup_ram_limit_bytes`` (cgroup v2, with v1 and the hybrid mount read as
  fallbacks - see ``_cgroup_mem``). An OOM-killed child shows as
  exit ``signal 9`` with ``cgroup_ram_used`` approaching the limit in the
  last samples - exactly what crash.txt's last-sample section is for.
* Run under an init process (``docker run --init``, or tini) so terminated
  children are reaped and Stop/Ctrl-C propagate; as PID 1 without an init,
  the wrapped process tree can linger as zombies.
* Install ``psutil`` in the image. Without it the tool degrades to the
  /proc/meminfo fallback (present in Linux containers), losing per-process
  CPU/RSS/threads and io_counters.

Usage
-----
Wrap any command (recommended - fully decoupled):

    python voxel_runner_diagnos.py -- python -m voxelizer single tile.laz ^
        --output-dir outputs/Run5/single

The run directory is auto-detected from the wrapped command's -o/--output-dir;
override it with --run-dir. Add --serve for the live dashboard.

In-process (if you'd rather instrument from inside your own script):

    from voxel_runner_diagnos import DiagnosticsMonitor
    with DiagnosticsMonitor(run_dir="outputs/Run5/single"):
        process_single_tile(...)          # crash line is captured automatically

Attach a viewer to a run that is already being monitored elsewhere:

    python voxel_runner_diagnos.py --view outputs/Run5/single/diagnosis

Dependencies
------------
``psutil`` is strongly recommended and does all the heavy lifting. If it is
missing the tool still runs, degrades the metrics it can't get to ``null``,
and prints how to install it:  pip install psutil
"""

from __future__ import annotations
import argparse
import faulthandler
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import traceback as _tb
from datetime import datetime
from pathlib import Path

# --- psutil is optional: import it, but never hard-fail without it ----------
try:
    import psutil  # type: ignore
    _HAVE_PSUTIL = True
except ImportError:  # pragma: no cover - environment dependent
    psutil = None  # type: ignore
    _HAVE_PSUTIL = False

DEFAULT_INTERVAL = 0.25        # seconds between resource samples
DEFAULT_LOG_EVERY = 1      # seconds between console diag lines
DEFAULT_PORT = 8770           # live dashboard port

# Mirror of cli_common.default_bind(), inlined because this module stays
# package-import-free: $VOXELIZER_BIND if set (the Docker image sets it to
# 0.0.0.0 so its published ports reach anything), else loopback. An explicit
# --host always wins; empty/whitespace counts as unset.
DEFAULT_HOST = os.environ.get("VOXELIZER_BIND", "").strip() or "127.0.0.1"
_DIAG_SUBDIR = "diagnosis"


# ---------------------------------------------------------------------------
# small human-readable formatters
# ---------------------------------------------------------------------------
def _human_bytes(n: float | None) -> str:
    """Format a byte count with one decimal and a binary unit (B .. EB);
    "n/a" for None."""
    if n is None:
        return "n/a"
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < step:
            return f"{n:.1f} {unit}"
        n /= step
    return f"{n:.1f} EB"


def _gb(n: float | None) -> float | None:
    """Bytes -> GiB rounded to two decimals, passing None through."""
    return None if n is None else round(n / (1024.0 ** 3), 2)


# ---------------------------------------------------------------------------
# environment snapshot (written once at start)
# ---------------------------------------------------------------------------
def _write_environment(diag_dir: Path, *, root_pid: int, command: str) -> None:
    """Write ``environment.txt`` once: timestamp, command, watched pid,
    Python and platform strings, CPU count, total RAM (when psutil is
    available), working directory and argv."""
    total_ram = None
    if _HAVE_PSUTIL:
        try:
            total_ram = psutil.virtual_memory().total
        except Exception:  # noqa: BLE001
            pass
    lines = [
        f"Captured           : {datetime.now().isoformat(timespec='seconds')}",
        f"Command / target   : {command}",
        f"Root PID watched   : {root_pid}",
        f"Python             : {platform.python_version()} ({sys.executable})",
        f"Platform           : {platform.platform()}",
        f"Machine / proc     : {platform.machine()} / {platform.processor()}",
        f"Logical CPUs       : {os.cpu_count()}",
        f"Total RAM          : {_human_bytes(total_ram)}",
        f"psutil available   : {_HAVE_PSUTIL}"
        + ("" if _HAVE_PSUTIL else "   (install with:  pip install psutil)"),
        f"Working directory  : {os.getcwd()}",
        f"argv               : {sys.argv}",
    ]
    (diag_dir / "environment.txt").write_text("\n".join(lines) + "\n",
                                               encoding="utf-8")


# ---------------------------------------------------------------------------
# no-psutil fallbacks (best effort; fields we cannot get become None)
# ---------------------------------------------------------------------------
def _fallback_system_ram() -> tuple[int | None, int | None]:
    """Return (total, used) bytes without psutil, or (None, None)."""
    # Linux
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        try:
            info = {}
            for line in meminfo.read_text().splitlines():
                key, _, rest = line.partition(":")
                info[key.strip()] = int(rest.strip().split()[0]) * 1024
            total = info.get("MemTotal")
            avail = info.get("MemAvailable")
            if total is not None and avail is not None:
                return total, total - avail
        except Exception:  # noqa: BLE001
            pass
    # Windows via ctypes
    if os.name == "nt":
        try:
            import ctypes

            class _MEMSTAT(ctypes.Structure):
                """ctypes mirror of the Win32 MEMORYSTATUSEX structure filled
                by ``GlobalMemoryStatusEx``."""
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMSTAT()
            stat.dwLength = ctypes.sizeof(_MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return int(stat.ullTotalPhys), int(stat.ullTotalPhys - stat.ullAvailPhys)
        except Exception:  # noqa: BLE001
            pass
    return None, None


def _cgroup_mem() -> tuple[int | None, int | None]:
    """(used, limit) bytes from the cgroup, or (None, None) outside containers.

    Inside Docker, psutil's system-wide numbers describe the HOST; the cgroup
    files describe the container's own memory budget - the one the kernel's
    OOM killer enforces. Handles cgroup v2 (memory.current / memory.max),
    the hybrid mount (/sys/fs/cgroup/unified), and cgroup v1
    (memory/memory.usage_in_bytes / memory.limit_in_bytes). limit is None
    when unlimited ('max' on v2; the ~2^63 sentinel on v1).
    """
    try:
        for base in (Path("/sys/fs/cgroup"), Path("/sys/fs/cgroup/unified")):
            cur = base / "memory.current"
            if cur.exists():
                used = int(cur.read_text().strip())
                raw = (base / "memory.max").read_text().strip()
                return used, (None if raw == "max" else int(raw))
        v1 = Path("/sys/fs/cgroup/memory")
        cur = v1 / "memory.usage_in_bytes"
        if cur.exists():
            used = int(cur.read_text().strip())
            limit = int((v1 / "memory.limit_in_bytes").read_text().strip())
            return used, (None if limit >= (1 << 60) else limit)
    except Exception:  # noqa: BLE001 - odd mounts / permissions
        pass
    return None, None


# ---------------------------------------------------------------------------
# the sampler / monitor
# ---------------------------------------------------------------------------
class DiagnosticsMonitor:
    """
    Samples resources on a daemon thread and streams them to disk.

    Can be used three ways:
      * as a context manager around in-process work (crash line auto-captured);
      * driven manually with .start() / .stop();
      * as the engine behind the subprocess wrapper.

    That third way does NOT pass ``root_pid`` to the constructor - the child
    does not exist yet when the monitor is built. ``run_command_with_diagnostics``
    assigns ``monitor.root_pid = proc.pid`` afterwards, and the samples then
    follow the child. ``environment.txt`` does not: it is written by
    ``__init__``, before the reassignment, so its "Root PID watched" line
    holds the WRAPPER's pid. The per-sample records in ``resources.jsonl``
    carry the pid actually watched.
    """

    def __init__(
        self,
        run_dir: Path | str,
        *,
        root_pid: int | None = None,
        interval: float = DEFAULT_INTERVAL,
        log_every: float = DEFAULT_LOG_EVERY,
        command: str = "",
        install_excepthook: bool = True,
        console: bool = True,
    ) -> None:
        """Create ``<run_dir>/diagnosis/``, open ``resources.jsonl`` for
        line-buffered append, enable faulthandler on ``faulthandler.log``
        and write ``environment.txt``. *root_pid* defaults to this process;
        *interval* is clamped to >= 0.05 s and *log_every* to >= interval.
        Nothing is sampled until start()."""
        self.run_dir = Path(run_dir)
        self.diag_dir = self.run_dir / _DIAG_SUBDIR
        self.diag_dir.mkdir(parents=True, exist_ok=True)
        self.root_pid = root_pid if root_pid is not None else os.getpid()
        self.interval = max(0.05, float(interval))
        self.log_every = max(self.interval, float(log_every))
        self.command = command or f"pid={self.root_pid}"
        self.console = console
        self._install_excepthook = install_excepthook

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0 = time.time()
        self._last_console = 0.0

        # persistent per-pid Process handles so cpu_percent() deltas are valid
        self._procs: dict[int, "psutil.Process"] = {}
        # disk I/O rate bookkeeping
        self._prev_io: tuple[float, int, int] | None = None  # (t, read, write)

        # running peaks / totals for the summary
        self.peak = {
            "proc_cpu_percent": 0.0,
            "sys_cpu_percent": 0.0,
            "proc_rss_bytes": 0,
            "sys_ram_percent": 0.0,
            "threads": 0,
            "procs": 0,
        }
        self.total_disk_read = 0
        self.total_disk_write = 0
        self.n_samples = 0
        self.last_sample: dict | None = None

        # crash record (filled in by note_crash / excepthook)
        self.crash: dict | None = None

        # open the streaming files
        self._jsonl = open(self.diag_dir / "resources.jsonl", "a",
                           encoding="utf-8", buffering=1)  # line-buffered
        faulthandler.enable(open(self.diag_dir / "faulthandler.log", "w"))

        _write_environment(self.diag_dir, root_pid=self.root_pid,
                           command=self.command)

        self._prev_excepthook = None

    # -- sampling -----------------------------------------------------------
    def _tree_procs(self) -> list["psutil.Process"]:
        """Refresh the cached Process tree, priming cpu_percent on new pids."""
        if not _HAVE_PSUTIL:
            return []
        alive: list[psutil.Process] = []
        # Historical note (bug FIXED below): root.children(recursive=True)
        # builds fresh psutil.Process instances on every call, and
        # cpu_percent() keeps its timing state on the instance, not per PID.
        # An earlier version primed each fresh instance and never reused it,
        # so every sample acted like a first call and reported 0% CPU. The
        # loop now swaps every candidate for its cached instance in
        # self._procs (priming only genuinely new PIDs), so cpu_percent
        # deltas span samples as intended.
        try:
            root = self._procs.get(self.root_pid) or psutil.Process(self.root_pid)
            self._procs[self.root_pid] = root
            candidates = [root] + root.children(recursive=True)
        except Exception:  # noqa: BLE001 - process already gone
            candidates = list(self._procs.values())
        for p in candidates:
            # This loop is the fix described above: reuse the cached instance
            # when there is one, prime only genuinely new PIDs.
            try:
                if p.pid in self._procs:
                    p = self._procs[p.pid]  # reuse cached instance
                else:
                    self._procs[p.pid] = p
                    p.cpu_percent(None)  # prime new instance
                if p.is_running():
                    alive.append(p)
            except Exception:  # noqa: BLE001
                continue
        return alive

    def _snapshot(self) -> dict:
        """Take one sample: CPU, RSS, threads, process count and cumulative
        disk I/O summed over the process tree (with I/O rates from the
        previous sample), system CPU and RAM, cgroup memory, and free/total
        space on the run drive. Without psutil only the system RAM
        fallback, the drive space and (for the own process) the thread
        count are filled; every other field stays None."""
        now = time.time()
        elapsed = now - self._t0
        s: dict = {
            "t": round(elapsed, 3),
            "iso": datetime.now().isoformat(timespec="milliseconds"),
            "proc_cpu_percent": None,
            "sys_cpu_percent": None,
            "proc_rss_bytes": None,
            "sys_ram_used_bytes": None,
            "sys_ram_total_bytes": None,
            "sys_ram_percent": None,
            "threads": None,
            "procs": None,
            "disk_read_bytes": None,
            "disk_write_bytes": None,
            "disk_read_rate_bps": None,
            "disk_write_rate_bps": None,
            "run_drive_free_bytes": None,
            "run_drive_total_bytes": None,
        }

        if _HAVE_PSUTIL:
            alive = self._tree_procs()
            cpu = rss = threads = 0.0
            read_b = write_b = 0
            got_io = False
            for p in alive:
                try:
                    cpu += p.cpu_percent(None)
                    rss += p.memory_info().rss
                    threads += p.num_threads()
                except Exception:  # noqa: BLE001
                    continue
                try:
                    io = p.io_counters()
                    read_b += io.read_bytes
                    write_b += io.write_bytes
                    got_io = True
                except Exception:  # noqa: BLE001 - not available on all OSes
                    pass
            s["proc_cpu_percent"] = round(cpu, 1)
            s["proc_rss_bytes"] = int(rss)
            s["threads"] = int(threads)
            s["procs"] = len(alive)

            try:
                s["sys_cpu_percent"] = psutil.cpu_percent(None)
            except Exception:  # noqa: BLE001
                pass
            try:
                vm = psutil.virtual_memory()
                s["sys_ram_used_bytes"] = int(vm.used)
                s["sys_ram_total_bytes"] = int(vm.total)
                s["sys_ram_percent"] = float(vm.percent)
            except Exception:  # noqa: BLE001
                pass

            if got_io:
                s["disk_read_bytes"] = read_b
                s["disk_write_bytes"] = write_b
                if self._prev_io is not None:
                    pt, pr, pw = self._prev_io
                    dt = max(now - pt, 1e-6)
                    s["disk_read_rate_bps"] = max(0.0, (read_b - pr) / dt)
                    s["disk_write_rate_bps"] = max(0.0, (write_b - pw) / dt)
                    self.total_disk_read = max(self.total_disk_read, read_b)
                    self.total_disk_write = max(self.total_disk_write, write_b)
                self._prev_io = (now, read_b, write_b)
        else:
            # degraded metrics without psutil
            total, used = _fallback_system_ram()
            s["sys_ram_total_bytes"] = total
            s["sys_ram_used_bytes"] = used
            if total and used is not None:
                s["sys_ram_percent"] = round(100.0 * used / total, 1)
            if self.root_pid == os.getpid():
                s["threads"] = threading.active_count()

        # container memory accounting (cgroup v2, v1 or hybrid) - None
        # outside containers.
        cg_used, cg_limit = _cgroup_mem()
        s["cgroup_ram_used_bytes"] = cg_used
        s["cgroup_ram_limit_bytes"] = cg_limit

        # disk *space* on the run drive (works with or without psutil)
        try:
            du = shutil.disk_usage(self.diag_dir)
            s["run_drive_free_bytes"] = int(du.free)
            s["run_drive_total_bytes"] = int(du.total)
        except Exception:  # noqa: BLE001
            pass

        return s

    def _update_peaks(self, s: dict) -> None:
        """Raise the running maxima in ``self.peak`` from one sample,
        ignoring None fields."""
        for k in ("proc_cpu_percent", "sys_cpu_percent",
                  "proc_rss_bytes", "sys_ram_percent", "threads", "procs"):
            v = s.get(k)
            if v is not None and v > self.peak[k]:
                self.peak[k] = v

    def _console_line(self, s: dict) -> str:
        """The one-line ``[diag t=..]`` console summary of a sample (CPU,
        RAM, threads, disk read/write), with "?" for missing fields."""
        rss = _gb(s["proc_rss_bytes"])
        ram_used = _gb(s["sys_ram_used_bytes"])
        ram_total = _gb(s["sys_ram_total_bytes"])
        ram_pct = s["sys_ram_percent"]
        pcpu = s["proc_cpu_percent"]
        scpu = s["sys_cpu_percent"]
        thr = s["threads"]
        dr = _human_bytes(s["disk_read_bytes"])
        dw = _human_bytes(s["disk_write_bytes"])
        return (
            f"[diag t={s['t']:.1f}s] "
            f"CPU {pcpu if pcpu is not None else '?'}%"
            f"/{scpu if scpu is not None else '?'}% sys  "
            f"RAM {rss if rss is not None else '?'} GB proc, "
            f"{ram_used if ram_used is not None else '?'}/"
            f"{ram_total if ram_total is not None else '?'} GB "
            f"({ram_pct if ram_pct is not None else '?'}%)  "
            f"thr {thr if thr is not None else '?'}  "
            f"disk r/w {dr}/{dw}"
        )

    def _loop(self) -> None:
        """Sampler thread body: every ``interval`` seconds take a snapshot,
        append it to ``resources.jsonl``, update the peaks and print the
        console line when ``log_every`` has elapsed, until the stop event
        is set. A failing sample is reported on stderr and the loop goes
        on."""
        # prime system cpu_percent so the first real reading is meaningful
        if _HAVE_PSUTIL:
            try:
                psutil.cpu_percent(None)
            except Exception:  # noqa: BLE001
                pass
        while not self._stop.is_set():
            try:
                s = self._snapshot()
                self._jsonl.write(json.dumps(s) + "\n")
                self.n_samples += 1
                self.last_sample = s
                self._update_peaks(s)
                now = time.time()
                if self.console and (now - self._last_console) >= self.log_every:
                    print(self._console_line(s), file=sys.stderr, flush=True)
                    self._last_console = now
            except Exception as exc:  # noqa: BLE001 - never let the monitor die quietly
                print(f"[diag] sampling error: {exc}", file=sys.stderr, flush=True)
            self._stop.wait(self.interval)

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> "DiagnosticsMonitor":
        """Install the crash-capturing excepthook (when enabled), launch the
        daemon sampler thread and return self."""
        if self._install_excepthook:
            self._prev_excepthook = sys.excepthook
            sys.excepthook = self._excepthook
        self._thread = threading.Thread(target=self._loop, name="diag-sampler",
                                        daemon=True)
        self._thread.start()
        if self.console:
            print(f"[diag] monitoring pid {self.root_pid}; "
                  f"streaming to {self.diag_dir / 'resources.jsonl'}",
                  file=sys.stderr, flush=True)
        return self

    def stop(self, *, exit_status: dict | None = None) -> dict:
        """Stop the sampler, close the JSONL stream, restore the previous
        excepthook and write the summary files; *exit_status* fields (exit
        code, signal, crashed) are merged into the summary, which is
        returned."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval * 4 + 1.0)
        try:
            self._jsonl.flush()
            self._jsonl.close()
        except Exception:  # noqa: BLE001
            pass
        if self._install_excepthook and self._prev_excepthook is not None:
            sys.excepthook = self._prev_excepthook
        summary = self._write_summary(exit_status or {})
        return summary

    # -- crash capture ------------------------------------------------------
    def note_crash(self, exc: BaseException) -> None:
        """Record a Python exception and pinpoint the crashing file:line."""
        tb = exc.__traceback__
        frames = _tb.extract_tb(tb)
        last = frames[-1] if frames else None
        self.crash = {
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "crash_file": last.filename if last else None,
            "crash_line": last.lineno if last else None,
            "crash_func": last.name if last else None,
            "crash_source": last.line if last else None,
            "traceback": "".join(_tb.format_exception(type(exc), exc, tb)),
        }

    def _excepthook(self, etype, value, tb):
        """``sys.excepthook`` replacement: record the uncaught exception via
        note_crash(), then chain to the previous hook."""
        try:
            value.__traceback__ = tb
            self.note_crash(value)
        finally:
            if self._prev_excepthook is not None:
                self._prev_excepthook(etype, value, tb)

    # -- summary / crash files ---------------------------------------------
    def _write_summary(self, exit_status: dict) -> dict:
        """Build the summary dict (duration, sample count, peaks, disk
        totals, crash flag and location, *exit_status*, and the stage
        isolation block when a ``stages.json`` is found), write it as
        ``summary.json`` and ``summary.txt``, write ``crash.txt`` when the
        run crashed, and return it."""
        duration = time.time() - self._t0
        summary = {
            "command": self.command,
            "root_pid": self.root_pid,
            "duration_s": round(duration, 2),
            "samples": self.n_samples,
            "sample_interval_s": self.interval,
            "psutil": _HAVE_PSUTIL,
            "peak_proc_cpu_percent": round(self.peak["proc_cpu_percent"], 1),
            "peak_sys_cpu_percent": round(self.peak["sys_cpu_percent"], 1),
            "peak_proc_rss_bytes": int(self.peak["proc_rss_bytes"]),
            "peak_proc_rss_gb": _gb(self.peak["proc_rss_bytes"]),
            "peak_sys_ram_percent": round(self.peak["sys_ram_percent"], 1),
            "peak_threads": int(self.peak["threads"]),
            "peak_procs": int(self.peak["procs"]),
            "total_disk_read_bytes": int(self.total_disk_read),
            "total_disk_write_bytes": int(self.total_disk_write),
            "crashed": self.crash is not None or bool(exit_status.get("crashed")),
        }
        summary.update(exit_status)
        if self.crash is not None:
            summary["crash"] = {k: self.crash[k] for k in
                                ("exception_type", "exception_message",
                                 "crash_file", "crash_line", "crash_func")}

        # Pull in per-stage isolation manifest (if --isolate-stages was used).
        self._merge_stage_manifest(summary)

        (self.diag_dir / "summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8")

        lines = [
            "Voxelizer run diagnostics - summary",
            "=" * 40,
            f"Command            : {self.command}",
            f"Duration           : {summary['duration_s']} s",
            f"Samples            : {summary['samples']} @ {self.interval}s",
            f"psutil available   : {_HAVE_PSUTIL}",
            "",
            "Peaks",
            "-" * 40,
            f"Process CPU        : {summary['peak_proc_cpu_percent']} %"
            f"   (of {os.cpu_count()} cores -> {os.cpu_count() * 100}% max)",
            f"System CPU         : {summary['peak_sys_cpu_percent']} %",
            f"Process RSS        : {_human_bytes(summary['peak_proc_rss_bytes'])}",
            f"System RAM         : {summary['peak_sys_ram_percent']} %",
            f"Threads (tree)     : {summary['peak_threads']}",
            f"Processes (tree)   : {summary['peak_procs']}",
            "",
            "Disk I/O (cumulative by the process tree)",
            "-" * 40,
            f"Read               : {_human_bytes(summary['total_disk_read_bytes'])}",
            f"Written            : {_human_bytes(summary['total_disk_write_bytes'])}",
        ]
        if "exit_code" in summary:
            lines += ["", f"Exit code          : {summary['exit_code']}"]
        if "signal" in summary and summary["signal"]:
            lines += [f"Terminated by sig  : {summary['signal']}"]
        lines += ["", f"Crashed            : {summary['crashed']}"]
        if summary["crashed"]:
            lines += ["  (see crash.txt for the exact file / line)"]
        elif summary.get("stages_crashed"):
            lines += ["  (parent OK, but isolated stage(s) failed - see "
                      "'Stage isolation' below and stages.json)"]

        # Per-stage isolation summary (only present when --isolate-stages was used)
        si = summary.get("stage_isolation")
        if si is not None:
            lines += [
                "", "Stage isolation (--isolate-stages)", "-" * 40,
                f"Stages OK          : {si['stages_ok']}",
                f"Stages failed      : {si['stages_failed']}",
                f"Total wall time    : {si['total_wall_s']} s",
            ]
            sm = self._read_stage_manifest()
            if sm is not None:
                for name in si["stage_details"]:
                    rec = sm["stages"].get(name, {})
                    status = rec.get("final_status", "?")
                    wall = sum(a.get("wall_s", 0) for a in rec.get("attempts", []))
                    lines.append(f"  {name:<16s} {status}  ({wall:.1f}s)")
            if si.get("failed_stages"):
                lines += ["", "Failed stages:"]
                for fs in si["failed_stages"]:
                    lines.append(f"  - {fs}")
                lines += ["  (see stages.json + stages/<name>.faultlog for details)"]
            lines.append("")

        (self.diag_dir / "summary.txt").write_text("\n".join(lines) + "\n",
                                                    encoding="utf-8")

        # dedicated crash.txt - the "which line did it crash on" answer
        if summary["crashed"]:
            self._write_crash(summary)

        if self.console:
            print(f"[diag] summary written to {self.diag_dir / 'summary.txt'}",
                  file=sys.stderr, flush=True)
        return summary

    def _write_crash(self, summary: dict) -> None:
        """Write ``crash.txt``: the crash file/line/function and full
        traceback when a Python exception was captured, otherwise the exit
        code and signal with a pointer to ``faulthandler.log``; in both
        cases the last resource sample before exit."""
        out = ["Voxelizer run - CRASH report",
               "=" * 40,
               f"Time               : {datetime.now().isoformat(timespec='seconds')}",
               f"Command            : {self.command}", ""]
        if self.crash is not None:
            c = self.crash
            out += [
                "CRASH LOCATION",
                "-" * 40,
                f"  File   : {c['crash_file']}",
                f"  Line   : {c['crash_line']}",
                f"  In     : {c['crash_func']}()",
                f"  Source : {c['crash_source']}",
                f"  Error  : {c['exception_type']}: {c['exception_message']}",
                "",
                "FULL TRACEBACK",
                "-" * 40,
                c["traceback"],
            ]
        else:
            out += [
                "No Python traceback was captured.",
                "This usually means the process was killed by the OS "
                "(e.g. out-of-memory) or crashed at the C level.",
                f"  Exit code : {summary.get('exit_code')}",
                f"  Signal    : {summary.get('signal')}",
                "  Check faulthandler.log for a low-level dump, and the last",
                "  resource sample below - a RAM spike points to an OOM kill.",
            ]
        if self.last_sample is not None:
            out += ["", "LAST RESOURCE SAMPLE BEFORE EXIT",
                    "-" * 40, json.dumps(self.last_sample, indent=2)]
        (self.diag_dir / "crash.txt").write_text("\n".join(out) + "\n",
                                                  encoding="utf-8")

    # -- stage isolation manifest (complements the diag summary) ------------
    def _read_stage_manifest(self) -> dict | None:
        """Read ``stages.json`` from the output directory (if present).

        ``--isolate-stages`` writes per-stage exit status into
        ``<out_dir>/stages.json``.  The diag monitor detects the *run*
        directory from the wrapped command's ``-o/--output-dir`` (stored in
        ``self.run_dir``), which may be the parent of the actual output dir
        (e.g. ``outputs/Run1`` vs ``outputs/Run1/area_output``).  This method
        checks the run dir itself and one level of subdirectories for
        ``stages.json``.
        """
        # Direct hit in run_dir
        stages_file = self.run_dir / "stages.json"
        if not stages_file.exists():
            # Search one level of subdirectories (area_output/, single/, ...)
            for child in self.run_dir.iterdir():
                candidate = child / "stages.json"
                if candidate.exists():
                    stages_file = candidate
                    break
            else:
                return None
        try:
            manifest = json.loads(stages_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(manifest, list):
            return None
        stages = {r["stage"]: r for r in manifest}
        n_ok = sum(1 for r in manifest if r.get("ok"))
        n_fail = len(manifest) - n_ok
        total_wall = sum(a.get("wall_s", 0) for r in manifest
                         for a in r.get("attempts", []))
        return {
            "stages": stages,
            "stage_names": [r["stage"] for r in manifest],
            "stages_ok": n_ok,
            "stages_failed": n_fail,
            "stage_total_wall_s": round(total_wall, 2),
            "stage_manifest": manifest,
        }

    def _merge_stage_manifest(self, summary: dict) -> None:
        """Merge stage isolation data into *summary* (mutates in place)."""
        sm = self._read_stage_manifest()
        if sm is None:
            return
        summary["stage_isolation"] = {
            "stages_ok": sm["stages_ok"],
            "stages_failed": sm["stages_failed"],
            "total_wall_s": sm["stage_total_wall_s"],
            "failed_stages": [s for s, r in sm["stages"].items()
                              if not r.get("ok")],
            "stage_details": sm["stage_names"],
        }
        # "crashed" keeps meaning the PARENT's fate. Stage failures under
        # isolation are recorded as a separate "stages_crashed" flag so a
        # summary reader can tell "the run died" from "the run survived but
        # a stage did not" - summary.txt prints both, and the process exit
        # code already reflects stage failures via stages.json.
        if not summary.get("crashed") and sm["stages_failed"]:
            summary["stages_crashed"] = True

    # -- context manager sugar ---------------------------------------------
    def __enter__(self) -> "DiagnosticsMonitor":
        """Start monitoring on entry to the ``with`` block."""
        return self.start()

    def __exit__(self, etype, value, tb) -> bool:
        """Stop monitoring; an exception leaving the block is recorded as
        the crash and the summary marked crashed. Always returns False so
        the exception propagates."""
        if value is not None:
            value.__traceback__ = tb
            self.note_crash(value)
            self.stop(exit_status={"crashed": True})
            return False  # re-raise
        self.stop()
        return False


# ---------------------------------------------------------------------------
# subprocess wrapper (the decoupled, recommended path)
# ---------------------------------------------------------------------------
def _detect_run_dir(cmd: list[str]) -> Path | None:
    """Pull the run dir out of a wrapped command's -o / --output-dir.

    If the output directory has a parent whose name starts with *Run*
    (e.g. ``outputs/Run1/area_output``), the *parent* is returned so that
    ``diagnosis/`` lands beside the mode subdirectory, not inside it.
    """
    for i, tok in enumerate(cmd):
        if tok in ("-o", "--output-dir") and i + 1 < len(cmd):
            path = Path(cmd[i + 1])
            return path.parent if path.parent.name.startswith("Run") else path
        if tok.startswith("--output-dir="):
            path = Path(tok.split("=", 1)[1])
            return path.parent if path.parent.name.startswith("Run") else path
    return None


def _tee_reader(pipe, sink, echo_stream, ring: list | None) -> None:
    """Read a subprocess pipe line by line: echo to console, append to run.log,
    and (for stderr) keep the last lines in a ring buffer for crash parsing."""
    for raw in iter(pipe.readline, ""):
        sink.write(raw)
        sink.flush()
        echo_stream.write(raw)
        echo_stream.flush()
        if ring is not None:
            ring.append(raw)
            if len(ring) > 500:
                del ring[0]
    pipe.close()


def _parse_crash_from_stderr(lines: list[str]) -> dict | None:
    """Find the last 'File "...", line N, in func' frame and the error line.

    Native-fault (faulthandler) output is recognized FIRST: its frames read
    'File "X", line N in func' (no comma before 'in', so the Python-
    traceback branch below never matched them), and its last stdout line
    is arbitrary - one run's summary recorded exception_type='wrote D'
    from colon-splitting a progress line.
    """
    fatal = None
    for ln in lines:
        s = ln.strip()
        if s.startswith("Windows fatal exception:") or \
           s.startswith("Fatal Python error:"):
            fatal = s
    if fatal is not None:
        frame = None
        seen_thread = False
        for ln in lines:
            s = ln.strip()
            if s.startswith("Current thread") or s.startswith("Thread 0x"):
                seen_thread = True
                continue
            if seen_thread and s.startswith('File "'):
                try:
                    after = s[len('File "'):]
                    fname, rest = after.split('", line ', 1)
                    num, _, func = rest.partition(" in ")
                    frame = (fname, int(num.strip().rstrip(",")),
                             func.strip())
                except Exception:  # noqa: BLE001
                    pass
                break  # first frame after the thread header = innermost
        fname, lineno, func = frame or (None, None, None)
        return {
            "exception_type": fatal,
            "exception_message": "",
            "crash_file": fname,
            "crash_line": lineno,
            "crash_func": func,
            "crash_source": None,
            "traceback": "".join(lines),
        }
    file_line_func = None
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith('File "') and ", line " in stripped:
            try:
                after = stripped[len('File "'):]
                fname, rest = after.split('", line ', 1)
                num_part, _, func_part = rest.partition(", in ")
                file_line_func = (fname, int(num_part.strip().rstrip(",")),
                                  func_part.strip())
            except Exception:  # noqa: BLE001
                pass
    # the exception message is usually the last non-empty stderr line
    err_msg = ""
    for ln in reversed(lines):
        if ln.strip():
            err_msg = ln.strip()
            break
    if file_line_func is None and not err_msg:
        return None
    fname, lineno, func = file_line_func or (None, None, None)
    if ":" in err_msg:
        etype, _, emsg = err_msg.partition(":")
        etype, emsg = etype.strip(), emsg.strip()
    else:
        etype, emsg = err_msg, ""
    return {
        "exception_type": etype,
        "exception_message": emsg,
        "crash_file": fname,
        "crash_line": lineno,
        "crash_func": func,
        "crash_source": None,
        "traceback": "".join(lines),
    }


def run_command_with_diagnostics(
    cmd: list[str],
    *,
    run_dir: Path | str | None = None,
    interval: float = DEFAULT_INTERVAL,
    log_every: float = DEFAULT_LOG_EVERY,
    serve: bool = False,
    port: int = DEFAULT_PORT,
    host: str = DEFAULT_HOST,
    open_browser: bool = True,
) -> int:
    """Launch ``cmd`` as a child, monitor it, and write diagnostics. Returns
    the child's exit code."""
    if run_dir is None:
        run_dir = _detect_run_dir(cmd)
    if run_dir is None:
        run_dir = Path.cwd() / f"RunDiag_{datetime.now():%Y%m%d_%H%M%S}"
        print(f"[diag] no --output-dir found in command; "
              f"diagnostics will go to {run_dir}", file=sys.stderr)

    monitor = DiagnosticsMonitor(
        run_dir, interval=interval, log_every=log_every,
        command=" ".join(cmd), install_excepthook=False, console=True,
    )
    run_log = open(monitor.diag_dir / "run.log", "w", encoding="utf-8",
                   buffering=1)

    dash = None
    if serve:
        dash = _start_dashboard(monitor.diag_dir, port, host=host,
                                open_browser=open_browser)

    # make the child dump a low-level traceback on fatal errors too
    child_env = dict(os.environ, PYTHONFAULTHANDLER="1", PYTHONUNBUFFERED="1")

    stderr_ring: list[str] = []
    exit_code = -1
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, env=child_env,
        )
    except FileNotFoundError as exc:
        print(f"[diag] cannot launch command: {exc}", file=sys.stderr)
        run_log.close()
        monitor.stop(exit_status={"crashed": True, "exit_code": 127})
        return 127

    # root_pid is the child; re-point the monitor at it
    monitor.root_pid = proc.pid
    monitor.start()

    t_out = threading.Thread(target=_tee_reader,
                             args=(proc.stdout, run_log, sys.stdout, None),
                             daemon=True)
    t_err = threading.Thread(target=_tee_reader,
                             args=(proc.stderr, run_log, sys.stderr, stderr_ring),
                             daemon=True)
    t_out.start()
    t_err.start()

    try:
        exit_code = proc.wait()
    except KeyboardInterrupt:
        print("[diag] interrupted - terminating child", file=sys.stderr)
        proc.terminate()
        exit_code = proc.wait()
    t_out.join(timeout=2)
    t_err.join(timeout=2)
    run_log.close()

    status: dict = {"exit_code": exit_code}
    if exit_code != 0:
        status["crashed"] = True
        if os.name != "nt" and exit_code < 0:
            status["signal"] = -exit_code
        parsed = _parse_crash_from_stderr(stderr_ring)
        if parsed is not None:
            monitor.crash = parsed

    summary = monitor.stop(exit_status=status)

    if summary["crashed"]:
        loc = summary.get("crash", {})
        if loc.get("crash_file"):
            print(f"[diag] CRASH at {loc['crash_file']}:{loc['crash_line']} "
                  f" -> see {monitor.diag_dir / 'crash.txt'}", file=sys.stderr)

    # Stage isolation: report per-stage failures even when the parent survived.
    si = summary.get("stage_isolation")
    if si and si.get("stages_failed"):
        print(f"[diag] {si['stages_failed']} stage(s) failed (parent survived): "
              f"{', '.join(si['failed_stages'])}  -> see stages.json",
              file=sys.stderr)

    # If a live dashboard was requested, keep it up after the run finishes so
    # short runs are still viewable. The daemon server thread would otherwise
    # die the instant this process exits.
    if dash is not None:
        print(f"[diag] run finished (exit {exit_code}). Dashboard live at "
              f"http://localhost:{port} - press Ctrl-C here to close it.",
              file=sys.stderr, flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        try:
            dash.shutdown()
        except Exception:  # noqa: BLE001
            pass
        print("[diag] dashboard closed.", file=sys.stderr)

    return exit_code


# ---------------------------------------------------------------------------
# live browser dashboard (stdlib only)
# ---------------------------------------------------------------------------
_DASH_HTML = """<!doctype html><meta charset=utf-8>
<title>voxelizer diagnostics</title>
<style>
 body{font:14px system-ui,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
 header{padding:12px 18px;background:#161a22;border-bottom:1px solid #262c38}
 h1{font-size:15px;margin:0;font-weight:600;letter-spacing:.02em}
 #status{color:#7aa2f7;font-size:12px;margin-top:3px}
 .grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;padding:18px}
 .card{background:#161a22;border:1px solid #262c38;border-radius:10px;padding:14px}
 .lbl{font-size:11px;color:#8b93a7;text-transform:uppercase;letter-spacing:.06em}
 .val{font-size:22px;font-weight:600;margin-top:4px}
 canvas{width:100%;height:60px;margin-top:8px;display:block}
</style>
<header><h1>voxelizer run diagnostics</h1><div id=status>connecting...</div></header>
<div class=grid>
 <div class=card><div class=lbl>Process CPU %</div><div class=val id=v_cpu>-</div><canvas id=c_cpu></canvas></div>
 <div class=card><div class=lbl>Process RAM (GB)</div><div class=val id=v_rss>-</div><canvas id=c_rss></canvas></div>
 <div class=card><div class=lbl>System RAM %</div><div class=val id=v_ram>-</div><canvas id=c_ram></canvas></div>
 <div class=card><div class=lbl>Threads</div><div class=val id=v_thr>-</div><canvas id=c_thr></canvas></div>
 <div class=card><div class=lbl>Disk write rate (MB/s)</div><div class=val id=v_dw>-</div><canvas id=c_dw></canvas></div>
 <div class=card><div class=lbl>Run drive free (GB)</div><div class=val id=v_df>-</div><canvas id=c_df></canvas></div>
</div>
<script>
function spark(id,data,color){var c=document.getElementById(id);var w=c.width=c.clientWidth*2,h=c.height=120;
 var x=c.getContext('2d');x.clearRect(0,0,w,h);if(!data.length)return;var mx=Math.max.apply(null,data.concat([1e-9]));
 x.strokeStyle=color;x.lineWidth=2;x.beginPath();data.forEach(function(v,i){var px=i/(data.length-1||1)*w,py=h-(v/mx)*(h-6)-3;
 i?x.lineTo(px,py):x.moveTo(px,py)});x.stroke();}
function num(v,d){return v==null?'-':(+v).toFixed(d==null?0:d);}
async function tick(){try{var r=await fetch('/data');var j=await r.json();var s=j[j.length-1]||{};
 document.getElementById('status').textContent=j.length+' samples | t='+num(s.t,1)+'s | updated '+new Date().toLocaleTimeString();
 document.getElementById('v_cpu').textContent=num(s.proc_cpu_percent,0)+' %';
 document.getElementById('v_rss').textContent=num((s.proc_rss_bytes||0)/1073741824,2);
 document.getElementById('v_ram').textContent=num(s.sys_ram_percent,0)+' %';
 document.getElementById('v_thr').textContent=num(s.threads,0);
 document.getElementById('v_dw').textContent=num((s.disk_write_rate_bps||0)/1048576,2);
 document.getElementById('v_df').textContent=num((s.run_drive_free_bytes||0)/1073741824,1);
 spark('c_cpu',j.map(d=>d.proc_cpu_percent||0),'#7aa2f7');
 spark('c_rss',j.map(d=>(d.proc_rss_bytes||0)/1073741824),'#9ece6a');
 spark('c_ram',j.map(d=>d.sys_ram_percent||0),'#e0af68');
 spark('c_thr',j.map(d=>d.threads||0),'#bb9af7');
 spark('c_dw',j.map(d=>(d.disk_write_rate_bps||0)/1048576),'#f7768e');
 spark('c_df',j.map(d=>(d.run_drive_free_bytes||0)/1073741824),'#73daca');
 }catch(e){document.getElementById('status').textContent='waiting for data...';}}
setInterval(tick,1000);tick();
</script>"""


def _start_dashboard(diag_dir: Path, port: int, *, host: str = DEFAULT_HOST,
                     open_browser: bool = True, tail: int = 600):
    """Start a tiny stdlib HTTP server that serves a live dashboard reading
    resources.jsonl. Returns the server (runs in a daemon thread).

    ``host`` is the bind address; the default honours ``$VOXELIZER_BIND``
    (0.0.0.0 in the Docker image, so the published port is reachable from
    the host browser) and stays loopback-only elsewhere."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    jsonl_path = diag_dir / "resources.jsonl"

    class Handler(BaseHTTPRequestHandler):
        """Two-route handler: ``/data`` returns the last *tail* JSONL
        samples as a JSON array, anything else the dashboard page."""
        def log_message(self, *a):  # silence per-request logging
            """Drop the per-request access log."""
            pass

        def do_GET(self):  # noqa: N802
            """Serve ``/data*`` as JSON rows read fresh from
            ``resources.jsonl`` (unparseable or missing lines skipped),
            and every other path as the embedded dashboard HTML."""
            if self.path.startswith("/data"):
                rows: list = []
                try:
                    with open(jsonl_path, encoding="utf-8") as f:
                        lines = f.readlines()[-tail:]
                    for ln in lines:
                        ln = ln.strip()
                        if ln:
                            try:
                                rows.append(json.loads(ln))
                            except json.JSONDecodeError:
                                pass  # partial last line mid-write
                except FileNotFoundError:
                    pass
                body = json.dumps(rows).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                body = _DASH_HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

    server = ThreadingHTTPServer((host, port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    # 0.0.0.0 is a bind address, not a browsable one - display localhost.
    shown = "localhost" if host in ("0.0.0.0", "", "127.0.0.1") else host
    url = f"http://{shown}:{port}"
    print(f"[diag] live dashboard at {url}"
          + (f" (bound on {host}; publish the port if containerised)"
             if host == "0.0.0.0" else ""),
          file=sys.stderr, flush=True)
    if open_browser:
        # Same scripted-use hook as serve_tiles/serve_voxel_html: the env
        # var suppresses the launch without changing the command line.
        if os.environ.get("VOXELIZER_NO_BROWSER"):
            print(f"VOXELIZER_NO_BROWSER set - not opening {url}")
        else:
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:  # noqa: BLE001
                pass
    return server


# ---------------------------------------------------------------------------
# --view : attach to a run already being monitored
# ---------------------------------------------------------------------------
def _resolve_diag_dir(path: Path) -> Path:
    """Accept either a run dir, its diagnosis dir, or the jsonl itself."""
    if path.is_file():
        return path.parent
    if (path / "resources.jsonl").exists():
        return path
    if (path / _DIAG_SUBDIR / "resources.jsonl").exists():
        return path / _DIAG_SUBDIR
    return path if path.name == _DIAG_SUBDIR else path / _DIAG_SUBDIR


def _view(path: Path, *, serve: bool, port: int, host: str = DEFAULT_HOST,
          open_browser: bool = True) -> None:
    """Attach to an existing run's ``resources.jsonl`` (run dir, diagnosis
    dir or the file itself), optionally start the dashboard server, and
    tail new samples to stdout as one line each until Ctrl-C."""
    diag_dir = _resolve_diag_dir(path)
    jsonl = diag_dir / "resources.jsonl"
    print(f"[diag] viewing {jsonl}", file=sys.stderr)
    if serve:
        _start_dashboard(diag_dir, port, host=host, open_browser=open_browser)
    # simple console tail
    pos = 0
    try:
        while True:
            if jsonl.exists():
                with open(jsonl, encoding="utf-8") as f:
                    f.seek(pos)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            s = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        print(
                            f"t={s.get('t')}s CPU={s.get('proc_cpu_percent')}% "
                            f"RAM={_gb(s.get('proc_rss_bytes'))}GB "
                            f"thr={s.get('threads')} "
                            f"RAMsys={s.get('sys_ram_percent')}%"
                        )
                    pos = f.tell()
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[diag] stopped viewing.", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    """Build the parser: ``--run-dir``, ``--interval``, ``--log-every``,
    the dashboard flags (``--serve``, ``--port``, ``--host``,
    ``--no-open``), ``--view`` for attaching to a run, and the remainder
    ``command`` to wrap after a literal ``--``."""
    p = argparse.ArgumentParser(
        prog="voxel_runner_diagnos.py",
        description="Standalone CPU/RAM/threads/disk + crash-line diagnostics "
                    "for a voxelizer run. Wrap a command, or attach a viewer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage")[1] if "Usage" in __doc__ else "",
    )
    p.add_argument("--run-dir", type=Path, default=None,
                   help="Run directory; diagnostics go to <run-dir>/diagnosis/. "
                        "If omitted, auto-detected from the wrapped command's "
                        "-o/--output-dir.")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                   help=f"Seconds between samples (default {DEFAULT_INTERVAL}).")
    p.add_argument("--log-every", type=float, default=DEFAULT_LOG_EVERY,
                   help=f"Seconds between console lines (default {DEFAULT_LOG_EVERY}).")
    p.add_argument("--serve", action="store_true",
                   help="Start a live browser dashboard.")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"Dashboard port (default {DEFAULT_PORT}).")
    p.add_argument("--host", type=str, default=DEFAULT_HOST,
                   help="Dashboard bind address (default $VOXELIZER_BIND "
                        "when set - 0.0.0.0 in the Docker image, so the "
                        "published 8770 is reachable from the host browser - "
                        "else 127.0.0.1, loopback only).")
    p.add_argument("--no-open", action="store_true",
                   help="Do not auto-open the dashboard in a browser. "
                        "VOXELIZER_NO_BROWSER=1 in the environment does the "
                        "same.")
    p.add_argument("--view", type=Path, default=None,
                   help="Attach to an existing run's diagnosis dir / resources.jsonl "
                        "and stream it (optionally with --serve). No monitoring.")
    p.add_argument("command", nargs=argparse.REMAINDER,
                   help="The command to run, after a literal --. "
                        "e.g. -- python -m voxelizer single tile.laz -o ...\\Run5")
    return p


def main(argv: list[str] | None = None) -> int:
    """Parse the flags above and either attach to a run (``--view``, returns
    0 when the tail is interrupted) or wrap the command given after ``--``
    under ``run_command_with_diagnostics``, returning the wrapped process's
    exit code. With neither, prints the help and returns 2. Warns on stderr
    when psutil is missing."""
    args = _build_parser().parse_args(argv)

    if not _HAVE_PSUTIL:
        print("[diag] psutil not found - metrics will be limited. "
              "Install it with:  pip install psutil", file=sys.stderr)

    if args.view is not None:
        _view(args.view, serve=args.serve, port=args.port, host=args.host,
              open_browser=not args.no_open)
        return 0

    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        _build_parser().print_help()
        print("\n[diag] nothing to run. Provide a command after --, "
              "or use --view.", file=sys.stderr)
        return 2

    return run_command_with_diagnostics(
        cmd, run_dir=args.run_dir, interval=args.interval,
        log_every=args.log_every, serve=args.serve, port=args.port,
        host=args.host, open_browser=not args.no_open,
    )


if __name__ == "__main__":
    raise SystemExit(main())
