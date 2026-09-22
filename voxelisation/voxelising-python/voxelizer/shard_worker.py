"""
One-tile shard worker: voxelize a single LAZ/LAS file to a shard .npz in a
child process, so that a NATIVE crash (an access violation inside the LAZ
decompressor DLL, exit code 0xC0000005 on Windows) kills only this worker
and never the multi-hour sharded area run that spawned it.
@ingroup t3_orchestr


Motivation: the full-Lyon run died at tile 160/2842 with exactly
such a crash. The parent (``sharding.run_area_sharded`` with
``isolate_tiles=True``) launches::

    python -m voxelizer.shard_worker --laz TILE.laz --out SHARD.npz
        --cell-xy 2.0 --cell-z 2.0 --origin X0 Y0 Z0
        [--clip XMIN YMIN XMAX YMAX] [--keep-classes 2,3,4,5]
        [--chunk-size 5000000]

Contract with the parent (keep in sync with ``sharding._voxelize_tile_in_child``):

* exit 0 and the shard file exists   -> tile voxelized, parent folds it;
* exit 0 and no shard file           -> tile was empty (no occupied column);
* exit 3                             -> clean Python-level failure (message
                                        on stderr: bad CRS, unreadable file);
* any other exit code -> native crash (on Windows subprocess reports the
  unsigned NTSTATUS as a large POSITIVE code, 3221225477 = 0xC0000005;
  on POSIX death-by-signal is the negative -signum).
  NOTE the parent's retry policy does not read this split at all, and BY
  DEFAULT THERE IS NO RETRY. ``sharding._voxelize_tile_in_child`` runs the
  child once; a second attempt happens only when the run was given
  ``--retry-lazrs`` (``retry_lazrs=True``), and then on ANY non-zero exit
  including 3, with the single-thread lazrs backend
  (``VOXELIZER_LAZ_BACKEND=lazrs``, honoured by ``io_laz``). Either way the
  tile is then recorded in ``failed_tiles.json`` and the run moves on. So the
  worst case is one attempt without the flag and two with it.

The shard is written ATOMICALLY: first to ``_tmp_<name>.npz`` in the same
directory, then ``os.replace``d into place. A worker killed mid-write can
therefore never leave a truncated .npz that a later ``--resume-shards`` run
would trust. The parent sweeps leftover ``_tmp_*.npz`` files at startup.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _parse_args(argv):
    """Parse the worker's command line (``--laz``, ``--out``, ``--cell-xy``,
    ``--cell-z``, ``--origin`` required; ``--clip``, ``--keep-classes`` and
    ``--chunk-size`` optional) and return the argparse namespace."""
    p = argparse.ArgumentParser(
        prog="voxelizer.shard_worker",
        description="Voxelize ONE tile to a shard .npz (crash-isolation "
                    "child of the sharded area runner).")
    p.add_argument("--laz", required=True, help="Input .laz/.las tile.")
    p.add_argument("--out", required=True, help="Output shard .npz path.")
    p.add_argument("--cell-xy", type=float, required=True)
    p.add_argument("--cell-z", type=float, required=True)
    # The parent always knows the shared origin before spawning (it reads the
    # tile header itself); requiring it here keeps every shard on the exact
    # same lattice as the rest of the run.
    p.add_argument("--origin", type=float, nargs=3, required=True,
                   metavar=("X0", "Y0", "Z0"))
    p.add_argument("--clip", type=float, nargs=4, default=None,
                   metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
                   help="Optional area bbox; points outside are dropped "
                        "(same semantics as the in-process path).")
    p.add_argument("--keep-classes", default=None,
                   help="Comma-separated ASPRS codes to keep (default: all).")
    p.add_argument("--chunk-size", type=int, default=5_000_000,
                   help="Points per chunk; 0 = whole-file read "
                        "(mirrors use_chunks=False in the parent).")
    return p.parse_args(argv)


def main(argv=None) -> int:
    """Entry point of the crash-isolation child: parse the flags above,
    voxelize the one tile (chunked when ``--chunk-size`` > 0, otherwise a
    whole-file read with an optional bbox clip) and write the shard
    atomically via ``_tmp_<name>.npz`` + ``os.replace``. Returns 0 on success
    (with no file written, and any stale same-named shard removed, when the
    tile has no occupied column) and 3 on a clean Python-level failure,
    including a stale shard that could not be removed."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    # Imports happen after argparse so that `--help` works even if numpy or
    # laspy are broken; any import-time failure below still exits non-zero
    # and is handled by the parent like every other worker failure.
    from .io_laz import read_laz
    from .voxelize import voxelize, voxelize_laz_chunked

    # Checked with `is not None` rather than truthiness: --keep-classes "" is a deliberate
    # "keep nothing" (empty set), distinct from the flag being absent
    # ("keep everything") - see sharding._voxelize_tile_in_child, which
    # now always emits the flag when the parent's set is not None.
    keep = None
    if args.keep_classes is not None:
        keep = {int(t) for t in args.keep_classes.split(",") if t.strip()}
    clip = tuple(args.clip) if args.clip else None
    out = Path(args.out)

    try:
        if args.chunk_size > 0:
            tile = voxelize_laz_chunked(
                args.laz, cell_xy=args.cell_xy, cell_z=args.cell_z,
                keep_classes=keep, origin=tuple(args.origin),
                chunk_size=args.chunk_size, clip_bbox=clip)
        else:
            x, y, z, cls = read_laz(args.laz)
            if clip is not None:
                xmn, ymn, xmx, ymx = clip
                m = (x >= xmn) & (x < xmx) & (y >= ymn) & (y < ymx)
                x, y, z, cls = x[m], y[m], z[m], cls[m]
            tile = voxelize(x, y, z, cls, cell_xy=args.cell_xy,
                            cell_z=args.cell_z, keep_classes=keep,
                            origin=tuple(args.origin))
    except Exception as exc:  # noqa: BLE001  (clean Python failure -> exit 3)
        print(f"shard_worker: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    # Empty tile: succeed WITHOUT writing a shard (exit 0, no file), exactly
    # like the in-process path, which only saves stores with occupied columns.
    # The parent tells "empty" from "produced" purely by whether the file
    # exists, so any shard already sitting at this name (a previous run over
    # the same folder with, say, different --keep-classes on the identical
    # lattice) must go - otherwise the parent would load the stale file and
    # fold another run's data in as this tile's result.
    if not len(tile.columns):
        try:
            out.unlink(missing_ok=True)
        except OSError as exc:
            # A surviving stale file would be adopted as this tile's data
            # (archive_cli globs shards/*.npz; --resume-shards trusts file
            # existence), so a failed cleanup must read as a failed TILE -
            # exit 3, recorded and retried - never as a clean empty result.
            print(f"shard_worker: could not remove stale shard {out}: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 3
        return 0

    # Atomic write: tmp name ends in .npz on purpose, so numpy does not
    # append a second extension; the "_tmp_" prefix keeps it recognisable
    # for the parent's startup sweep.
    tmp = out.with_name("_tmp_" + out.name)
    tile.save(tmp)
    os.replace(tmp, out)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    sys.exit(main())
