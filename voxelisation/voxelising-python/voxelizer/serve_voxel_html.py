"""
Static file server tailored to streaming voxel HTMLs.
@ingroup t4_entrees


    python -m voxelizer.serve_voxel_html [<dir>] [--port 8000] [--bind HOST]
        [--open] [--open-page NAME] [--no-open] [--no-cache]

If *dir* is omitted, the directory under ``outputs/`` with the newest
mtime that contains a ``*_stream.html`` (an area_output or a
``single/<tile>`` folder) is auto-detected. ``--open`` launches the default
browser automatically, at ``--open-page NAME`` when one is named and at the
directory listing otherwise - the page is only auto-selected one level up, by
``python -m voxelizer serve``, by the area GUI's Serve button (``gui_area``)
and by the generated ``view_stream.cmd``, all of which pass ``--open-page``
themselves. ``--no-open`` overrides ``--open``
(equivalent to ``VOXELIZER_NO_BROWSER=1``). ``--bind`` widens the default
loopback bind. ``--no-cache`` turns off the BROWSER-side payload cache: this
process caches nothing, it only chooses which ``Cache-Control`` header a
payload is served with.

It is ``python -m http.server`` with the four things a *view-dependent*
voxel viewer actually needs from its origin, and which CPython's
``SimpleHTTPRequestHandler`` does not provide:

  1. **HTTP Range / 206 Partial Content.**  This is the whole point.
     ``http.server`` has no Range support whatsoever - no ``Range`` header
     is parsed and no ``206`` is ever emitted - so *every* fetch of a
     sidecar is the *entire* sidecar.  At metropolis scale that is a 10.16 GB
     download to draw a 4 M-instance working set.  A viewer that wants to
     hold only the boxes near the camera must be able to say
     ``Range: bytes=<off>-<off+len-1>`` and get back exactly those bytes;
     that is what this handler now answers, including ``bytes=N-``,
     ``bytes=-N`` (suffix), and a spec-correct ``416`` with
     ``Content-Range: bytes */<size>`` for an unsatisfiable range.
     ``If-Range`` is honoured against the ``Last-Modified`` validator: a
     request whose validator no longer matches gets the full ``200``
     instead of a ``206``, so a client revalidating a cached unversioned
     payload can never splice bytes from two different exports.
     Multi-range requests (``bytes=0-9,20-29``) are legally *ignored* - we
     answer 200 with the full body - because browsers never generate them
     on their own, the viewer only ever asks for single ranges, and
     multipart/byteranges is not worth the surface area. (A hand-written
     ``fetch()`` CAN send one; it legally receives the 200.)

  2. **Cacheable payloads.**  The previous handler sent
     ``Cache-Control: no-store`` on everything.  For a viewer whose tile
     cache *is* the browser's HTTP cache that is exactly backwards: it
     forces a re-download of every tile the camera revisits.  Payloads
     (``.bin`` / ``.vxg`` / ``.b64`` / ``.idx`` / ``.json``) are now
     cacheable; the ``.html`` shell stays ``no-store`` so a re-export is
     always picked up.  ``--no-cache`` restores the old blanket behaviour
     for debugging.

     A payload is only advertised ``immutable`` when its url carries a
     ``?v=`` version tag, because the payload NAME is stable across
     re-exports (``<out>.bin``): the same url really does change bytes, so
     an unconditional ``immutable`` would pin a stale payload for a year.
     ``tiled_exporter`` stamps ``?v=<size>-<mtime>`` into the page's
     payload url, which both makes the long cache safe and busts it the
     moment the run is re-exported.  Unversioned payloads fall back to
     ``no-cache`` (cache, but revalidate).

  3. **CORS that survives Range.**  ``Access-Control-Allow-Origin: *`` was
     already here, but cross-origin JS cannot *read* ``Content-Range`` /
     ``Accept-Ranges`` without ``Access-Control-Expose-Headers``
     (``Content-Length`` has been CORS-safelisted since 2017 and is
     readable anyway; exposing it too is harmless), and a cross-origin ``Range`` request
     may be preflighted (a suffix form like ``bytes=-N`` always is; a
     simple single range is CORS-safelisted in current engines) - so
     ``OPTIONS`` is answered and ``Range`` is named in
     ``Access-Control-Allow-Headers``.

  4. **HTTP/1.1 keep-alive.**  A residency manager issues many small range
     requests per camera move.  Under HTTP/1.0 each one paid a fresh TCP
     handshake.  ``protocol_version = "HTTP/1.1"`` keeps the connection
     open; ``SimpleHTTPRequestHandler`` already sends an accurate
     ``Content-Length`` on every response (files, listings and errors
     alike), which is what makes that safe.

Aborted fetches (the residency manager cancelling a tile the camera has
already left) surface as ``BrokenPipeError`` / ``ConnectionResetError`` on
the write side; they are expected traffic, not faults, and are swallowed
quietly rather than dumped as tracebacks.

The socket binds ``127.0.0.1``: what is being served is a directory of the
machine's own files, and the viewer runs on the machine that ran the pipeline.
``--bind 0.0.0.0`` widens that on purpose - a container publishing the port
needs it - but nothing reaches the LAN by default.  ``VOXELIZER_BIND`` in the
environment moves that default without touching any command line, which is how
the repository's Dockerfile widens the bind for every entry point at once
(image-wide ``ENV``) while desktops stay on localhost; an explicit ``--bind``
still wins over both.

Tileset (3-D Tiles) output has its own server, voxelizer.serve_tiles;
this one is for the streaming ``*_stream.html`` viewer and its ``.bin``.
"""
from __future__ import annotations

import argparse
import http.server
import os
import re
import socketserver
import sys
import time
from pathlib import Path

from .cli_common import BIND_ENV_VAR, LOCALHOST, default_bind

# bytes=<first>-<last> | bytes=<first>- | bytes=-<suffix-length>
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

_COPY_BLOCK = 256 * 1024

# Suffixes eligible for long-lived caching (the viewer's tile cache IS the
# browser's HTTP cache, so re-downloading a revisited tile is pure waste).
#
# Eligible is not the same as immutable. A payload name is derived from the
# output path (`<out>.bin`), so re-exporting a run rewrites the SAME url with
# different bytes - exactly what `immutable` tells the browser can never
# happen. Serving it unconditionally means a re-render is invisible for up to
# a year, with no reload short of a hard cache purge.
#
# So `immutable` is promised only when the url actually carries a version tag
# (`?v=<size>-<mtime>`, emitted by tiled_exporter). An unversioned payload is
# still cacheable but must revalidate, which costs one conditional request per
# tile and is answered with 304 while the file is unchanged.
_PAYLOAD_SUFFIXES = (".bin", ".vxg", ".b64", ".idx", ".json")


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Static handler with single-range 206 support, payload caching headers, CORS and keep-alive.

    Everything the module docstring lists on top of
    ``SimpleHTTPRequestHandler``; ``cache_payloads`` is a class attribute
    that main() sets from ``--no-cache``.
    """
    protocol_version = "HTTP/1.1"          # keep-alive for many small ranges

    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".bin":  "application/octet-stream",
        ".vxg":  "application/octet-stream",
        ".idx":  "application/octet-stream",
        ".b64":  "text/plain; charset=utf-8",
        ".json": "application/json",
        ".js":   "application/javascript",
        ".mjs":  "application/javascript",
        ".html": "text/html; charset=utf-8",
    }

    # overridden by a class-attribute assignment in main()
    cache_payloads = True

    # ---------------- Range-aware send_head -------------------------------
    def send_head(self):
        """Answer a single-range ``Range`` request on a file with 206 and a bounded body (416 when unsatisfiable, 400 for ``bytes=-``), falling back to the base class's full 200 for no Range, a directory, a multi-range or malformed header, or an ``If-Range`` validator that does not strongly match the file's Last-Modified."""
        self._copy_len = None             # reset per request (see copyfile)

        rng = self.headers.get("Range")
        if not rng:
            return super().send_head()

        path = self.translate_path(self.path)
        if os.path.isdir(path):           # Range on a directory: meaningless
            return super().send_head()

        m = _RANGE_RE.match(rng.strip())
        if m is None:                     # multi-range or garbage -> ignore it
            return super().send_head()    # (RFC 9110: a server MAY ignore Range)

        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None

        try:
            st = os.fstat(f.fileno())
            size = st.st_size

            # If-Range (RFC 7233 s3.2): a conditional Range whose validator no
            # longer matches the current representation MUST be answered with
            # the full 200 - otherwise a client revalidating a cached
            # unversioned payload (the `public, no-cache` mode below) would
            # splice old cached bytes and new range bytes into one buffer
            # after a re-export. The only validator this server emits is
            # Last-Modified, so the comparison is an exact HTTP-date match;
            # an entity-tag form (leading `"` or `W/`) can never match here
            # and likewise falls back to the full body. HTTP dates have
            # whole-second resolution, so Last-Modified only counts as a
            # STRONG validator (RFC 7232 s2.2.2) once the file is at least a
            # second old - a re-export landing in the same second as the
            # client's cached date would otherwise still pass the match.
            if_range = self.headers.get("If-Range")
            if if_range is not None:
                strong_match = (
                    if_range.strip() == self.date_time_string(st.st_mtime)
                    and time.time() - st.st_mtime >= 1.0)
                if not strong_match:
                    f.close()
                    return super().send_head()

            first, last = m.group(1), m.group(2)

            if first == "" and last == "":
                f.close()
                self.send_error(400, "Malformed Range")
                return None

            if first == "":               # bytes=-N  -> final N bytes
                n = int(last)
                if n == 0:
                    return self._unsatisfiable(f, size)
                start = max(0, size - n)
                end = size - 1
            else:
                start = int(first)
                end = size - 1 if last == "" else min(int(last), size - 1)

            if start >= size or start > end:
                return self._unsatisfiable(f, size)

            length = end - start + 1
            f.seek(start)

            self.send_response(206)
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header(
                "Last-Modified",
                self.date_time_string(os.fstat(f.fileno()).st_mtime))
            self.end_headers()            # adds Accept-Ranges / CORS / cache
            self._copy_len = length
            return f
        except Exception:
            f.close()
            raise

    def _unsatisfiable(self, f, size: int):
        """Close *f* and answer 416 with ``Content-Range: bytes */<size>`` and no body; returns None so send_head sends nothing more."""
        f.close()
        self.send_response(416)
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return None

    # ---------------- bounded copy for the 206 case ------------------------
    def copyfile(self, source, outputfile):
        """Copy only the ``_copy_len`` bytes a 206 promised (in 256 KiB blocks) instead of the whole file, and mark the connection closed rather than raising when the client aborts the fetch."""
        n = getattr(self, "_copy_len", None)
        try:
            if n is None:
                return super().copyfile(source, outputfile)
            while n > 0:
                buf = source.read(min(_COPY_BLOCK, n))
                if not buf:
                    break
                outputfile.write(buf)
                n -= len(buf)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The viewer cancelled a tile it no longer needs. Expected.
            self.close_connection = True

    # ---------------- headers common to every response ---------------------
    def end_headers(self):
        """Add ``Accept-Ranges``, the CORS allow/expose headers and a ``Cache-Control`` chosen per url (``immutable`` for a versioned payload, ``public, no-cache`` for an unversioned one, ``no-store`` otherwise or when payload caching is off) to every response."""
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range, If-Range")
        self.send_header(
            "Access-Control-Expose-Headers",
            "Content-Range, Content-Length, Accept-Ranges")

        path, _, query = self.path.partition("?")
        suffix = Path(path).suffix.lower()
        if self.cache_payloads and suffix in _PAYLOAD_SUFFIXES:
            # The tile cache IS the browser cache. Let it work - but only
            # promise immutability for a url that carries a version tag, since
            # an unversioned `<out>.bin` is rewritten in place by a re-export.
            if "v=" in query:
                self.send_header("Cache-Control",
                                 "public, max-age=31536000, immutable")
            else:
                self.send_header("Cache-Control", "public, no-cache")
        else:
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_OPTIONS(self):                 # CORS preflight for Range
        """Answer the CORS preflight the base class lacks with 204 and an empty body; the allow headers come from end_headers."""
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):    # one line, no client-address noise
        """Write the formatted message alone to stderr, without the client address and timestamp prefix the base class adds."""
        sys.stderr.write("%s\n" % (fmt % args))


class _Server(socketserver.ThreadingTCPServer):
    """Threaded TCP server whose handler threads are daemons and whose port can be rebound immediately after a restart."""
    daemon_threads = True
    allow_reuse_address = True


def _find_latest_stream_html() -> Path | None:
    """Scan outputs/ for the most recent dir with a *_stream.html."""
    outputs = Path("outputs")
    if not outputs.is_dir():
        return None
    candidates: list[tuple[float, Path]] = []

    def _scan_dir(parent: Path) -> None:
        """Append (mtime, parent) to the candidates once if *parent* holds a ``*_stream*.html``."""
        if not parent.is_dir():
            return
        for f in parent.iterdir():
            if f.suffix == ".html" and "_stream" in f.stem:
                candidates.append((parent.stat().st_mtime, parent))
                return

    for run_dir in sorted(outputs.iterdir(), reverse=True):
        _scan_dir(run_dir / "area_output")
        single_dir = run_dir / "single"
        if single_dir.is_dir():
            for tile_dir in single_dir.iterdir():
                if tile_dir.is_dir():
                    _scan_dir(tile_dir)
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def main(argv=None) -> None:
    """Command-line entry point: serve a streaming-viewer directory until Ctrl+C.

    Parses the optional ``dir`` plus ``--port``, ``--bind``, ``--no-cache``,
    ``--open``, ``--open-page`` and ``--no-open``; auto-detects the newest
    run folder holding a ``*_stream.html`` when *dir* is omitted and raises
    ``SystemExit(1)`` when none is found or the path is not a directory.
    Sets ``_Handler.cache_payloads``, binds the server, prints the localhost
    url (with the bound port), optionally opens it in a browser and runs
    ``serve_forever()``; returns None.

    @param argv  Command-line arguments to parse; ``None`` uses ``sys.argv[1:]``.
    @throws SystemExit When no ``*_stream.html`` is found while auto-detecting the
                       directory, or when the resolved ``dir`` is not a directory,
                       in both cases with status 1. Also raised by argparse with
                       status 2 on a rejected command line.
    """
    ap = argparse.ArgumentParser(
        description="Range-capable static server for streaming voxel viewers.")
    ap.add_argument("dir", nargs="?", type=Path, default=None,
                    help="Directory to serve.  Defaults to the most recently "
                         "modified run folder (area_output or single/<tile>) "
                         "containing a *_stream.html.")
    ap.add_argument("--port", type=int, default=8000,
                    help="TCP port (default 8000). 0 asks the operating "
                         "system for a free one, so several runs can be "
                         "served at once; the url printed is the one it got.")
    # Localhost by default. This used to default to 0.0.0.0 for the Docker
    # services, whose published port (docker-compose.yml, 8000:8000) needs the
    # server to listen on all interfaces - but no service runs it as a
    # `command:`, it is started by hand inside the container, so the container
    # case cannot be served by a command line nobody types. The default now
    # serves a directory of the machine's files to the machine only, rather
    # than to every host on the LAN, which is what a double-clicked launcher
    # must do; the container widens it once, image-wide, through the
    # environment variable read here.
    ap.add_argument("--bind", default=default_bind(),
                    help=f"Interface to bind (default 127.0.0.1, localhost "
                         f"only, or ${BIND_ENV_VAR} when that is set in the "
                         f"environment - the repository's Dockerfile sets it "
                         f"to 0.0.0.0 because a container's published port "
                         f"needs it). An explicit --bind always wins.")
    ap.add_argument("--no-cache", action="store_true",
                    help="Send Cache-Control: no-store on payloads too "
                         "(debugging; defeats the browser-side tile cache).")
    ap.add_argument("--open", dest="auto_open", action="store_true",
                    help="Open the viewer in the default browser.")
    ap.add_argument("--open-page", default=None, metavar="NAME",
                    help="With --open, open this page under the served "
                         "directory (e.g. area_stream.html) instead of the "
                         "directory listing.")
    ap.add_argument("--no-open", action="store_true",
                    help="Never open a browser, whatever --open says. Setting "
                         "VOXELIZER_NO_BROWSER=1 in the environment does the "
                         "same without changing the command line, which is "
                         "what the generated launchers and the tests use.")
    args = ap.parse_args(argv)

    if args.dir is not None:
        serve_dir = args.dir.resolve()
    else:
        serve_dir = _find_latest_stream_html()
        if serve_dir is None:
            print("No *_stream.html found under outputs/.  "
                  "Pass a directory explicitly.", file=sys.stderr)
            raise SystemExit(1)
        print(f"Auto-detected: {serve_dir}")

    if not serve_dir.is_dir():
        print(f"Not a directory: {serve_dir}", file=sys.stderr)
        raise SystemExit(1)

    cache = not args.no_cache

    def handler(*a, **kw):
        """Handler factory that pins ``directory`` to the served folder."""
        h = _Handler(*a, directory=str(serve_dir), **kw)
        return h

    _Handler.cache_payloads = cache

    with _Server((args.bind, args.port), handler) as srv:
        # The bound port, not the requested one: with --port 0 the OS chooses,
        # and the url printed (and opened) has to be the real one.
        port = srv.server_address[1]
        # And the host has to be one a browser can open. "0.0.0.0" is a bind
        # wildcard, not a destination - pasted into a browser it fails outright
        # on Windows - so the url always says localhost, which reaches this
        # server under any bind that includes the loopback interface. The bind
        # itself is stated on its own line when it is wider than loopback,
        # because that is a different fact (who else can reach it) and the url
        # cannot carry it: the LAN address to hand out is the machine's, which
        # this process does not presume to guess. serve_tiles prints the same
        # way, for the same reason.
        url = f"http://localhost:{port}/"
        if args.open_page:
            url += args.open_page.lstrip("/")
        print(f"serving {serve_dir} on {url}  "
              f"(Range: on, payload cache: {'on' if cache else 'off'})")
        if args.bind not in (LOCALHOST, "localhost"):
            print(f"  bound to {args.bind}:{port} - reachable from other hosts")
        print("Press Ctrl+C to stop.")

        if args.auto_open and not args.no_open:
            if os.environ.get("VOXELIZER_NO_BROWSER"):
                print(f"VOXELIZER_NO_BROWSER set - not opening {url}")
            else:
                import webbrowser
                webbrowser.open(url)

        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")


if __name__ == "__main__":
    main()
