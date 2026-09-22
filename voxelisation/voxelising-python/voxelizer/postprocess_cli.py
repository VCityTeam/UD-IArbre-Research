"""
CLI for the semantic post-processors: one store in, one store out.
@ingroup t4_entrees


    python -m voxelizer.postprocess_cli IN OUT.npz
        [--min-points [N]]            # drop intervals with <= N points
        [--morph [MIN_NEIGH]]         # morphological support filter
            [--morph-classes 3,4,5,8]  # example; default is every class
        [--absorb]                    # enclosed vegetation -> building
            [--absorb-min-neighbour 0.75] [--absorb-max-noise 2]
        [--resolve]                   # one class per voxel
            [--tie-margin 1] [--tie-rel 0.0] [--no-prefer-taller]
            [--no-demote-uncertain] [--class-priority 6,5,4,...]
        [--group [--group-gap M]]     # re-group after the passes
        [--stats] [--dry-run]

``denoise``, ``absorb`` and ``resolve`` were library-only: the only way to run
them was a hand-written Python snippet, while every OUTPUT path (2-D maps, 3-D
viewers, 3-D Tiles, reconstruction) is store-driven and CLI-first. So "the
denoised, absorbed, resolved city as 3-D Tiles" could not be produced without
writing code. This is the missing link, and it is deliberately Unix-shaped -
it reads a store, applies the selected passes, writes a store, and every
existing tool consumes the result unchanged::

    python -m voxelizer.postprocess_cli area.npz area_clean.npz \\
        --min-points 4 --morph --absorb --resolve
    python -m voxelizer.tileset_cli from-store area_clean.npz --out-dir tiles

Pass order is FIXED, and is not the order the flags appear in::

    min-points -> morph -> absorb -> resolve -> group

Denoisers run first, so no neighbourhood vote is cast over speckle;
absorption runs before resolve, because it reasons about vegetation runs that
resolve may relabel; grouping runs last, because every earlier pass can
fragment or merge runs. Requesting no pass at all is an error rather than a
silent copy - ``cp`` already exists.

Defaults mirror the library signatures exactly (``min_points=4``,
``min_neighbours_same=2``, ``min_neighbour_building=0.75``,
``max_noise_run_voxels=2``, ``tie_margin=1``, ``tie_rel=0.0``,
``prefer_taller=True``, ``demote_uncertain=True``), the same rule the cell-size
defaults follow: the CLI and the library can never disagree about what an
unqualified run means. Each of ``--min-points`` and ``--morph`` therefore takes
its value optionally - bare, it is the library default. (Value-optional flags
have to follow the two positionals, or argparse reads the input path as the
flag's value.)

Point-count reporting and the resolve guard
-------------------------------------------
Every pass reports the columns, intervals and points it changed, and
``--stats`` adds the full per-class point table before and after. Nothing is
asserted about the denoisers or ``absorb``: they legitimately destroy points,
that is what they are for, and a run that silently kept the totals identical
would be the surprising one.

``resolve`` is the one pass with an arithmetic invariant, and it is an
INEQUALITY, not conservation. Total point count is NOT preserved: as
``resolve``'s own module docstring says, a run that loses a contested span
drops its share of the count outright - the points are discarded rather than
transferred - and a class whose every run lies inside a winner's span
disappears entirely. What proportional attribution does guarantee is
direction: a segment carries
``count * segment_height / run_height`` of its ORIGINATING run and nothing
else, so the total can fall but cannot rise. That is what is asserted here,
with one point of slack per emitted interval for the single rounding step at
the end of each column. It is a live guard, not a formality: the previous
implementation gave every segment the run's full count and summed on merge,
inflating split runs n-fold - a violation orders of magnitude past the
tolerance.

Input may be a ``.npz`` store or a raw ``store_raw/`` directory; the latter is
attached by memory map, so a large store costs pages the OS may reclaim rather
than heap. That directory form is why the usage above reads "IN" rather than
"IN.npz". The output is always a ``.npz``, written atomically
(``.partial.npz`` renamed on success) so an interrupted run cannot leave a
truncated store under the final name.

What this CLI does NOT do is stream. ``absorb_interior`` walks every column in
Python and ``resolve`` rebuilds each column's intervals, so peak memory scales
with the store and a metropolis-scale ``area.npz`` will not fit; the passes
themselves would have to be rewritten band-at-a-time for that, which is a
different piece of work from putting a command line on them. Post-process a
sub-area, or a shard.

The encoder variants v1-v4 (class smooth, gap fill, minor-class drop,
majority-above) stay in ``Experiments/`` as benchmarked experiment variants and
are deliberately not offered here.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# The fixed order, as data. The module docstring above states the same order
# and the reasoning behind it; this is the copy the code runs from, so a
# change here without a change there is what to watch for.
PASS_ORDER = ("min-points", "morph", "absorb", "resolve", "group")

# Rounding slack for the resolve guard: _resolve_column accumulates float
# shares and rounds once per emitted interval, so the total may exceed the
# exact proportional sum by less than one point per interval.
_RESOLVE_ROUNDING_SLACK_PER_INTERVAL = 1


class PostprocessError(RuntimeError):
    """A pass produced a result its own contract forbids."""


# ---------------------------------------------------------------------------
# loading / saving
# ---------------------------------------------------------------------------
def load_store(path: Path):
    """Load a ``ColumnStore`` from a ``.npz`` file or a raw store directory.

    A directory is attached with ``mmap=True``: the arrays stay file-backed
    pages rather than heap, which is how the output stages read the same
    format. A compressed ``.npz`` cannot be mapped - ``np.load`` inflates every
    member - so that path is a full read, unchanged.
    """
    from .data_structures import ColumnStore

    path = Path(path)
    if path.is_dir():
        return ColumnStore.load_dir(path, mmap=True), "mmap directory"
    return ColumnStore.load(path), "npz"


def save_store(store, path: Path) -> Path:
    """Write *store* to *path* atomically. Returns the path actually written."""
    from .store_streaming import save_store_npz_atomic
    return save_store_npz_atomic(store, path)


# ---------------------------------------------------------------------------
# per-pass count reporting + the resolve non-increase check
# ---------------------------------------------------------------------------
def snapshot(store) -> dict:
    """The three totals plus the per-class point counts, for one report line."""
    s = store.stats()
    return {
        "n_columns": s["n_columns"],
        "n_intervals": s["n_intervals"],
        "n_points": s["n_points"],
        "points_per_class": dict(s["points_per_class"]),
    }


def _delta(before: int, after: int) -> str:
    """Format *after* with its signed absolute and percentage change from
    *before*, or "(unchanged)"; the percentage is 0 when *before* is 0."""
    d = after - before
    if d == 0:
        return f"{after:,} (unchanged)"
    pct = (100.0 * d / before) if before else 0.0
    return f"{after:,} ({d:+,}, {pct:+.2f}%)"


def format_report(name: str, before: dict, after: dict,
                  *, per_class: bool = False) -> list[str]:
    """The lines one pass prints. Pure, so the tests can read them."""
    lines = [
        f"[{name}] columns   {_delta(before['n_columns'], after['n_columns'])}",
        f"[{name}] intervals {_delta(before['n_intervals'], after['n_intervals'])}",
        f"[{name}] points    {_delta(before['n_points'], after['n_points'])}",
    ]
    moved = []
    names = sorted(set(before["points_per_class"]) | set(after["points_per_class"]))
    for cls in names:
        b = before["points_per_class"].get(cls, 0)
        a = after["points_per_class"].get(cls, 0)
        if a != b or per_class:
            moved.append(f"    {cls}: {_delta(b, a)}")
    if moved:
        lines.append(f"[{name}] points by class:")
        lines.extend(moved)
    return lines


def _check_resolve(before: dict, after: dict) -> None:
    """Fail loudly if resolve grew the point total (see the module docstring)."""
    slack = after["n_intervals"] * _RESOLVE_ROUNDING_SLACK_PER_INTERVAL
    if after["n_points"] > before["n_points"] + slack:
        raise PostprocessError(
            f"resolve increased the total point count from "
            f"{before['n_points']:,} to {after['n_points']:,} "
            f"(+{after['n_points'] - before['n_points']:,}), beyond the "
            f"{slack:,}-point rounding allowance. Proportional attribution "
            f"can only lose points, never create them, so this is a bug in "
            f"the attribution, not a legitimate result.")


# ---------------------------------------------------------------------------
# the passes
# ---------------------------------------------------------------------------
def build_pass_plan(args) -> list[tuple[str, object]]:
    """Turn parsed arguments into ``[(name, callable)]`` in ``PASS_ORDER``.

    The callables close over the parsed options and take a store, so the
    ordering rule lives here alone and the runner cannot reorder it.
    """
    plan: list[tuple[str, object]] = []

    if args.min_points is not None:
        from .denoise import min_points_filter
        n = args.min_points
        plan.append(("min-points", lambda s: min_points_filter(s, min_points=n)))

    if args.morph is not None:
        from .denoise import morphological_filter
        k = args.morph
        classes = args.morph_classes
        plan.append(("morph",
                     lambda s: morphological_filter(s, k, check_classes=classes)))

    if args.absorb:
        from .absorb import absorb_interior
        plan.append(("absorb", lambda s: absorb_interior(
            s,
            min_neighbour_building=args.absorb_min_neighbour,
            max_noise_run_voxels=args.absorb_max_noise)))

    if args.resolve:
        from .resolve import resolve as resolve_pass
        plan.append(("resolve", lambda s: resolve_pass(
            s,
            tie_margin=args.tie_margin,
            tie_rel=args.tie_rel,
            prefer_taller=args.prefer_taller,
            demote_uncertain=args.demote_uncertain,
            class_priority=args.class_priority)))

    if args.group:
        gap = args.group_gap
        plan.append(("group", lambda s: s.grouped(
            max_gap_cells=(None if gap is None
                           else max(0, int(round(gap / s.cell_z)))))))

    # Belt and braces: the list above is already written in PASS_ORDER, and
    # this is what makes that a checked property rather than a comment.
    names = [n for n, _ in plan]
    assert names == [n for n in PASS_ORDER if n in names], names
    return plan


def _log_line(line: str) -> None:
    """Default *log* sink of run_passes(): one line to the module
    logger at INFO."""
    logger.info("%s", line)


def run_passes(store, plan, *, log=_log_line, per_class: bool = False):
    """Apply *plan* to *store*, reporting each pass. Returns the final store.

    *log* takes one already-formatted line, so a caller that wants the report
    as data (a test, or a GUI log pane) passes a list's ``append``.
    """
    for name, fn in plan:
        before = snapshot(store)
        log(f"[{name}] running ...")
        store = fn(store)
        after = snapshot(store)
        for line in format_report(name, before, after, per_class=per_class):
            log(line)
        if name == "resolve":
            _check_resolve(before, after)
    return store


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------
def _int_list(s: str):
    """Comma/space separated class codes -> list of int (empty string -> [])."""
    return [int(c) for c in s.replace(",", " ").split() if c]


def _non_negative_int(s: str) -> int:
    """argparse type: an int that must be >= 0, else ArgumentTypeError."""
    v = int(s)
    if v < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0 (got {s})")
    return v


def _build_parser() -> argparse.ArgumentParser:
    """Build the parser: the ``input`` and ``output`` positionals, then the
    denoise (``--min-points``, ``--morph``, ``--morph-classes``), absorb,
    resolve, group and reporting (``--stats``, ``--dry-run``) option
    groups, with the library defaults as the flags' bare values."""
    p = argparse.ArgumentParser(
        prog="python -m voxelizer.postprocess_cli",
        description="Apply the semantic post-processors to a store and write "
                    "a new one. Passes always run in the order "
                    + " -> ".join(PASS_ORDER) + ".")
    p.add_argument("input", type=Path,
                   help="Input store: a .npz, or a raw store_raw/ directory "
                        "(attached by memory map).")
    p.add_argument("output", type=Path,
                   help="Output .npz (written atomically; .npz appended if "
                        "missing).")

    d = p.add_argument_group("denoise")
    d.add_argument("--min-points", type=_non_negative_int, nargs="?",
                   const=4, default=None, metavar="N",
                   help="Drop intervals with <= N points. Bare --min-points "
                        "means 4, the library default. Omitted: pass not run.")
    d.add_argument("--morph", type=_non_negative_int, nargs="?",
                   const=2, default=None, metavar="MIN_NEIGH",
                   help="Morphological support filter: drop intervals with "
                        "fewer than MIN_NEIGH same-class neighbours among the "
                        "8 columns around them. Bare --morph means 2, the "
                        "library default.")
    d.add_argument("--morph-classes", type=_int_list, default=None,
                   metavar="A,B,C",
                   help="Restrict --morph to these ASPRS classes (default: "
                        "every class - morphological_filter with "
                        "check_classes=None checks them all).")

    a = p.add_argument_group("absorb")
    a.add_argument("--absorb", action="store_true",
                   help="Reclassify vegetation runs sandwiched by building, "
                        "inside a building-dominant neighbourhood, to "
                        "building.")
    a.add_argument("--absorb-min-neighbour", type=float, default=0.75,
                   metavar="F",
                   help="Minimum building fraction among the PENETRATED "
                        "8-neighbours (default 0.75).")
    a.add_argument("--absorb-max-noise", type=int, default=2, metavar="V",
                   help="Sandwiched runs this many voxels tall or shorter are "
                        "absorbed unconditionally (default 2).")

    r = p.add_argument_group("resolve")
    r.add_argument("--resolve", action="store_true",
                   help="Collapse cross-class overlaps so every voxel carries "
                        "one class.")
    r.add_argument("--tie-margin", type=int, default=1, metavar="N",
                   help="Counts within this absolute margin of the best count "
                        "are treated as tied (default 1; 0 restores strict "
                        "highest-count-wins).")
    r.add_argument("--tie-rel", type=float, default=0.0, metavar="F",
                   help="Relative near-tie band, unioned with --tie-margin "
                        "(default 0.0, off).")
    r.add_argument("--no-prefer-taller", dest="prefer_taller",
                   action="store_false",
                   help="Do not break near-ties by taller originating run.")
    r.add_argument("--no-demote-uncertain", dest="demote_uncertain",
                   action="store_false",
                   help="Let unclassified / noise / artefact classes win "
                        "near-ties against genuinely labelled classes.")
    r.add_argument("--class-priority", type=_int_list, default=None,
                   metavar="A,B,C",
                   help="Explicit near-tie preference order (earlier wins). "
                        "Overrides --no-demote-uncertain.")

    g = p.add_argument_group("group")
    g.add_argument("--group", action="store_true",
                   help="Merge vertically-consecutive same-class intervals "
                        "after the passes above.")
    g.add_argument("--group-gap", type=float, default=None, metavar="M",
                   help="With --group: merge only across empty gaps of at "
                        "most M metres (converted to cells as "
                        "round(M / cell_z), the same arithmetic area_cli "
                        "uses). Default: merge across any gap.")

    o = p.add_argument_group("reporting")
    o.add_argument("--stats", action="store_true",
                   help="Print the full per-class point table for every pass, "
                        "not only the classes whose totals moved.")
    o.add_argument("--dry-run", action="store_true",
                   help="Run the passes and report, but write nothing.")
    return p


def main(argv=None) -> int:
    """Parse ``IN OUT.npz`` plus the pass flags, refuse an empty plan or an
    orphan ``--group-gap`` / ``--morph-classes`` via ``parser.error``, load
    the store, run the selected passes in ``PASS_ORDER`` with a per-pass
    report, print the total report and save the result atomically (skipped
    under ``--dry-run``). Returns 0 on success, 1 when the input is missing,
    unloadable or empty, or when a pass raises ``PostprocessError``."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)

    plan_names = [n for n in PASS_ORDER
                  if (n == "min-points" and args.min_points is not None)
                  or (n == "morph" and args.morph is not None)
                  or (n == "absorb" and args.absorb)
                  or (n == "resolve" and args.resolve)
                  or (n == "group" and args.group)]
    if not plan_names:
        parser.error(
            "no pass requested - pick at least one of --min-points, --morph, "
            "--absorb, --resolve, --group. Copying a store unchanged is not "
            "this command's job.")
    if args.group_gap is not None and not args.group:
        parser.error("--group-gap has no meaning without --group.")
    if args.morph_classes is not None and args.morph is None:
        parser.error("--morph-classes has no meaning without --morph.")

    if not args.input.exists():
        print(f"error: input store not found: {args.input}", file=sys.stderr)
        return 1

    try:
        store, how = load_store(args.input)
    except Exception as exc:  # noqa: BLE001
        print(f"error: could not load {args.input}: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    start = snapshot(store)
    if start["n_intervals"] == 0:
        print(f"error: {args.input} holds an empty store - nothing to "
              f"post-process.", file=sys.stderr)
        return 1

    logger.info("loaded %s (%s): %s columns, %s intervals", args.input, how,
                f"{start['n_columns']:,}", f"{start['n_intervals']:,}")
    logger.info("passes: %s", " -> ".join(plan_names))

    try:
        store = run_passes(store, build_pass_plan(args), per_class=args.stats)
    except PostprocessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for line in format_report("total", start, snapshot(store),
                              per_class=args.stats):
        _log_line(line)

    if args.dry_run:
        logger.info("--dry-run: %s not written.", args.output)
        return 0

    written = save_store(store, args.output)
    logger.info("wrote %s", written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
