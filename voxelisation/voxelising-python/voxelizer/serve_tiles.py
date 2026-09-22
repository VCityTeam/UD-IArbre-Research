"""
Serve a 3-D Tiles directory (``tileset.json`` + ``tile_*.glb``) for viewing.
@ingroup t4_entrees


    python -m voxelizer.serve_tiles <dir> [--port N] [--open-viewer cesium]

This is the tileset counterpart of voxelizer.serve_voxel_html (which
serves the *streaming* ``*_stream.html`` viewer and its Range-fetched ``.bin``).
A tileset needs different things from its origin, so it gets its own module
rather than a flag on that one:

  * **Content types glTF viewers actually check.**  CPython's
    ``mimetypes`` has no entry for ``.glb``, so the stock handler labels every
    tile ``application/octet-stream``.  Cesium and iTowns both cope, but any
    proxy, cache or debugging tool in between is then guessing, and a browser's
    network panel shows a wall of anonymous binaries.  ``model/gltf-binary``
    (the registered type) is sent instead, alongside ``model/gltf+json`` for
    ``.gltf`` and ``application/json`` for the tileset itself.  The older 3-D
    Tiles content formats (``.b3dm``, ``.i3dm``, ``.pnts``, ``.cmpt``) have no
    registered type at all and stay octet-stream, deliberately.

  * **No caching.**  A tileset is regenerated in place under stable file names
    (``tile_0.glb`` means something different after every export), so a cached
    tile is a wrong tile.  Every response carries ``Cache-Control: no-store,
    no-cache, must-revalidate``, which is what makes a re-export visible on a
    plain reload instead of after a hard refresh.

  * **CORS.**  ``Access-Control-Allow-Origin: *`` so a viewer served from
    somewhere else (an iTowns dev server, another port, a second tileset for
    side-by-side comparison) may fetch these tiles.

  * **Both viewer pages, from the package.**  ``/viewer.html`` is the bundled
    CesiumJS page and ``/viewer-itowns.html`` the iTowns one
    (``voxelizer/data/``), each pointed at the sibling ``tileset.json``.  Viewer
    and tiles therefore share one origin in the default workflow, so nothing
    depends on the CORS header above.

  * **Localhost only.**  The server binds ``127.0.0.1``; ``--bind`` can widen
    that deliberately (a container publishing the port needs ``0.0.0.0``), but
    the default never puts a directory of the machine's files on the LAN.
    ``VOXELIZER_BIND`` in the environment replaces that default for every entry
    point at once - one ``ENV`` line in the repository's Dockerfile, rather
    than a flag on command lines nobody inside the container types - and an
    explicit ``--bind`` still overrides it.

  * **A free port by default.**  ``--port 0`` (the default) asks the OS for an
    unused port and prints the url it got, so several tilesets can be served at
    once - which is the whole point of a side-by-side comparison - without the
    user hand-allocating port numbers.  ``--port 8765`` pins it.

Paths are confined to the served directory: a request that resolves outside the
root (via ``..``, or through a symlink inside it) is answered ``403`` rather
than followed.

``--open-viewer {cesium,itowns}`` opens that page in the default browser once
the port is known.  Setting ``VOXELIZER_NO_BROWSER=1`` in the environment
suppresses the launch without changing the command line, which is what the
generated ``view_*.cmd`` launchers and the tests rely on.
"""
from __future__ import annotations

import argparse
import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .cli_common import BIND_ENV_VAR, LOCALHOST, default_bind

DATA_DIR = Path(__file__).with_name("data")

# url path -> bundled page.  Each viewer answers to two paths, with and
# without the `.html`: `/viewer` and `/viewer.html` are the same page, as are
# `/viewer-itowns` and `/viewer-itowns.html`. Both spellings appear in the
# documentation and in saved links, so both are routed rather than one 404ing.
VIEWERS = {
    "cesium": DATA_DIR / "cesium_viewer.html",
    "itowns": DATA_DIR / "itowns_viewer.html",
}
_ROUTES = {
    "/viewer.html": "cesium",
    "/viewer": "cesium",
    "/viewer-itowns.html": "itowns",
    "/viewer-itowns": "itowns",
}


class TilesRequestHandler(SimpleHTTPRequestHandler):
    """Static handler for a 3-D Tiles directory.

    Adds the glTF content types, the no-cache and CORS headers, the two
    bundled viewer pages and the root-confinement check described in the
    module docstring on top of ``SimpleHTTPRequestHandler``, over HTTP/1.1.
    """
    protocol_version = "HTTP/1.1"

    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".glb": "model/gltf-binary",
        ".gltf": "model/gltf+json",
        ".json": "application/json",
        ".html": "text/html; charset=utf-8",
        ".js": "application/javascript",
        # 3-D Tiles 1.0 content formats: no type was ever registered for these,
        # so octet-stream is the correct answer rather than a placeholder.
        ".b3dm": "application/octet-stream",
        ".i3dm": "application/octet-stream",
        ".pnts": "application/octet-stream",
        ".cmpt": "application/octet-stream",
        ".bin": "application/octet-stream",
    }

    def end_headers(self) -> None:
        """Add the wildcard CORS header and ``Cache-Control: no-store, no-cache, must-revalidate`` to every response before the base class closes the header block."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        super().end_headers()

    # ---------------- bundled viewer pages ---------------------------------
    def _viewer_for(self, path: str) -> Path | None:
        """Return the bundled viewer page a request *path* routes to, or None.

        The query string is dropped before the lookup, so ``/viewer.html?x=1``
        still resolves to the Cesium page.
        """
        name = _ROUTES.get(path.split("?")[0])
        return VIEWERS[name] if name else None

    def _send_viewer(self, page: Path, body_wanted: bool) -> None:
        """Answer 200 with the bundled *page* as UTF-8 HTML.

        The headers (including Content-Length) are always sent; the body is
        written only when *body_wanted* is true, which is how HEAD shares this
        with GET.
        """
        body = page.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body_wanted:
            self.wfile.write(body)

    # ---------------- confinement to the served root -----------------------
    def _outside_root(self) -> bool:
        """True when the request resolves outside the served directory.

        ``translate_path`` already collapses ``..`` segments, so a plain
        ``GET /../../etc/passwd`` cannot escape; this also catches the case it
        does not cover, a symlink inside the root pointing somewhere else.
        """
        try:
            root = Path(self.directory).resolve()
            target = Path(self.translate_path(self.path)).resolve()
        except OSError:
            return True
        return target != root and root not in target.parents

    def send_head(self):
        """Answer 403 and return None when the request resolves outside the served root, otherwise defer to the base class."""
        if self._outside_root():
            self.send_error(403, "Forbidden")
            return None
        return super().send_head()

    def do_GET(self) -> None:
        """Serve a bundled viewer page with its body when the path is one of the viewer routes, otherwise defer to the base class."""
        page = self._viewer_for(self.path)
        if page is not None:
            self._send_viewer(page, body_wanted=True)
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        """Send the headers of a bundled viewer page without its body when the path is one of the viewer routes, otherwise defer to the base class."""
        page = self._viewer_for(self.path)
        if page is not None:
            self._send_viewer(page, body_wanted=False)
            return
        super().do_HEAD()

    def do_OPTIONS(self) -> None:
        """Answer the CORS preflight the base class lacks: 204 with an empty body and ``Access-Control-Allow-Methods: GET, HEAD, OPTIONS``."""
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.end_headers()

    def log_message(self, fmt, *args):
        """Write the formatted message alone to stderr, without the client address and timestamp prefix the base class adds."""
        sys.stderr.write("%s\n" % (fmt % args))


def make_server(root: str | Path, port: int = 0,
                bind: str = LOCALHOST) -> ThreadingHTTPServer:
    """Return a server for *root*, already bound (``port=0`` picks a free one).

    Bound but not started: the caller decides between ``serve_forever()`` on
    this thread and a background thread, which is what lets the tests drive it.

    The bind default here is the literal loopback address, deliberately NOT
    ``$VOXELIZER_BIND``: this is the library entry point, and a function whose
    listening interface depends on the ambient environment is one whose
    callers cannot reason about it. The environment override belongs to the
    command line, where main() applies it as argparse's default.
    """
    root = Path(root).resolve()
    handler = partial(TilesRequestHandler, directory=str(root))
    server = ThreadingHTTPServer((bind, port), handler)
    server.daemon_threads = True
    return server


def _maybe_open(url: str) -> bool:
    """Open *url* in a browser unless VOXELIZER_NO_BROWSER says otherwise.

    The env var is the scripted-use hook: a generated ``.cmd`` launcher always
    asks for the browser, and a test (or a headless machine) suppresses it
    without editing the launcher or the command line.
    """
    if os.environ.get("VOXELIZER_NO_BROWSER"):
        print(f"VOXELIZER_NO_BROWSER set - not opening {url}")
        return False
    import webbrowser
    webbrowser.open(url)
    return True


def main(argv=None) -> int:
    """Command-line entry point: serve a tileset directory until Ctrl+C.

    Parses ``dir`` plus ``--port``, ``--bind``, ``--open-viewer`` and
    ``--no-open``, refuses (return 1) a path that is not a directory or has
    no ``tileset.json``, then binds the server, prints the localhost urls of
    the tileset and both viewer pages, optionally opens one in a browser and
    runs ``serve_forever()``. Returns 0 after the socket is closed.

    @param argv  Command-line arguments to parse; ``None`` uses ``sys.argv[1:]``.
    @return The process exit code: 0 on a clean stop, 1 when ``dir`` is not a
            directory or holds no ``tileset.json``.
    @throws SystemExit Raised by argparse with status 2 on a rejected command line.
    """
    ap = argparse.ArgumentParser(
        prog="python -m voxelizer.serve_tiles",
        description="Serve a 3-D Tiles directory (tileset.json + .glb) "
                    "with the headers and viewer pages browsers need.")
    ap.add_argument("dir", type=Path,
                    help="Directory containing tileset.json + *.glb")
    ap.add_argument("--port", type=int, default=0,
                    help="TCP port; 0 (default) asks the OS for a free one and "
                         "prints the url it got.")
    ap.add_argument("--bind", default=default_bind(),
                    help=f"Interface to bind (default 127.0.0.1, localhost "
                         f"only, or ${BIND_ENV_VAR} when that is set in the "
                         f"environment - the repository's Dockerfile sets it "
                         f"to 0.0.0.0 because a container's published port "
                         f"needs it). An explicit --bind always wins.")
    ap.add_argument("--open-viewer", choices=sorted(VIEWERS), default=None,
                    help="Open this viewer page in the default browser once "
                         "the port is known.")
    ap.add_argument("--no-open", action="store_true",
                    help="Never open a browser, whatever --open-viewer says "
                         "(equivalent to VOXELIZER_NO_BROWSER=1).")
    args = ap.parse_args(argv)

    root = args.dir.resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 1
    if not (root / "tileset.json").exists():
        print(f"error: {root} has no tileset.json", file=sys.stderr)
        return 1

    server = make_server(root, port=args.port, bind=args.bind)
    port = server.server_address[1]
    # The printed url has to be one a browser can open. "0.0.0.0" is a bind
    # wildcard, not a destination - pasted into a browser it fails outright on
    # Windows - so every url below says localhost, which reaches this server
    # under any bind that includes the loopback interface. The bind itself is
    # stated on its own line when it is wider than loopback, because that is a
    # different fact (who else can reach it) and the url cannot carry it: the
    # LAN address to hand out is the machine's, which this process does not
    # presume to guess.
    base = f"http://localhost:{port}"
    print(f"serving {root}")
    if args.bind not in (LOCALHOST, "localhost"):
        print(f"  bound to {args.bind}:{port} - reachable from other hosts")
    print(f"  tileset : {base}/tileset.json")
    print(f"  viewer  : {base}/viewer.html            (CesiumJS)")
    print(f"  viewer  : {base}/viewer-itowns.html     (iTowns)")
    print("Press Ctrl+C to stop.")

    if args.open_viewer and not args.no_open:
        suffix = "/viewer.html" if args.open_viewer == "cesium" \
            else "/viewer-itowns.html"
        _maybe_open(base + suffix)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        # serve_forever() has already left its loop by the time we get here
        # (Ctrl+C unwinds through it), so the only thing left to do is release
        # the listening socket - without which a relaunch on a pinned --port
        # is refused.
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
