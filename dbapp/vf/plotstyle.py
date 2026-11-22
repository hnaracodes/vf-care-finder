"""Shared matplotlib style for every static figure in this project.

It implements the dataviz method (palette, marks, labeling) for PNGs:

* Palette: the validated reference palette, light mode only (figures are
  static PNGs for reports and slides). Categorical slots are used in fixed
  order and never cycled. The first three slots are validated for all-pairs
  use (scatter, maps); slots 3 to 5 (aqua, yellow, magenta) sit below 3:1
  contrast on the surface, so any chart that uses them must carry visible
  direct labels.
* Project semantics are fixed across figures: private = BLUE, public =
  ORANGE, unknown operator = MUTED gray. Reuse them; do not reassign.
* Marks: bars at most 24 px thick with a 4 px rounded data end and a square
  baseline, 2 px lines, a 2 px surface gap between touching fills, hairline
  solid gridlines, text in ink tokens (never in a series color).

Sizes are in CSS px at 2x: a figure is 10 in wide at 160 dpi, so 1600 image
px, meant to be shown at about 800 px. `px(n)` converts CSS px to points.

Usage::

    from vf import plotstyle as ps
    ps.use()
    fig, gs = ps.figure(5.5, "Title that states the finding", "Subtitle", note="Source: ...")
    ax = fig.add_subplot(gs[0, 0])
    ps.hbars(ax, ["a", "b"], [10, 4])
    ps.save(fig, path)          # atomic write; rounds bar ends and caps thickness
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

import matplotlib.pyplot as plt
import numpy as np
from cycler import cycler
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, PathPatch, Rectangle
from matplotlib.path import Path as MPath

# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

FIG_W_IN = 10.0
DPI = 160
SCALE = 2.0            # image px per CSS px
MARGIN_IN = 0.4        # left edge of title, subtitle, and note

BAR_MAX_PX = 24        # bar thickness cap (CSS px)
BAR_RADIUS_PX = 4      # rounded data end (CSS px)
GAP_PX = 2             # surface gap between touching fills (CSS px)


def px(n: float) -> float:
    """CSS px to points at this figure scale."""
    return n * SCALE * 72.0 / DPI


# --------------------------------------------------------------------------
# palette (light mode, from the dataviz reference palette)
# --------------------------------------------------------------------------

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100",
          "#e87ba4", "#008300", "#6250d6", "#e34948")
BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED = SERIES

BLUE_RAMP = {
    100: "#cde2fb", 150: "#b7d3f6", 200: "#9ec5f4", 250: "#86b6ef",
    300: "#6da7ec", 350: "#5598e7", 400: "#3987e5", 450: "#2a78d6",
    500: "#256abf", 550: "#1c5cab", 600: "#184f95", 650: "#104281",
    700: "#0d366b",
}
DIVERGING_MID = "#f0efec"   # neutral midpoint; poles are BLUE and RED
STATUS = {"good": "#0ca30c", "warning": "#fab219",
          "serious": "#ec835a", "critical": "#d03b3b"}

#: label color inside a categorical fill (white, near-black on yellow)
ON_FILL = {c: "#ffffff" for c in SERIES}
ON_FILL[YELLOW] = INK


def on_fill(color: str) -> str:
    """Text color for a label set inside a fill: the palette's choice for
    categorical slots, otherwise near-black on light fills and white on dark."""
    if color in ON_FILL:
        return ON_FILL[color]
    from matplotlib.colors import to_rgb
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
               for c in to_rgb(color))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return INK if lum > 0.4 else "#ffffff"

# de-emphasis tones (documented chrome colors, used for context marks)
CONTEXT = AXIS     # background dots, "fine" cells
DEEMPH = MUTED     # de-emphasized bars, unknown category

# project semantics: fixed across all figures
PRIVATE = BLUE
PUBLIC = ORANGE
UNKNOWN = MUTED
#: ordinal 3-step ramp (validated with --ordinal): low -> high
ORDINAL_3 = (BLUE_RAMP[250], BLUE_RAMP[450], BLUE_RAMP[650])

FONT_STACK = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]


def use() -> None:
    """Apply the project rcParams. Call once before making figures."""
    plt.rcParams.update({
        "figure.dpi": DPI,
        "savefig.dpi": DPI,
        "figure.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "font.family": "sans-serif",
        "font.sans-serif": FONT_STACK,
        "font.size": 12,
        "text.color": INK,
        "axes.labelcolor": INK_2,
        "axes.labelsize": 12,
        "axes.titlesize": 13.5,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.titlecolor": INK,
        "axes.titlepad": 12,
        "axes.edgecolor": AXIS,
        "axes.linewidth": px(1),
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": False,
        "axes.axisbelow": True,
        "axes.prop_cycle": cycler(color=list(SERIES)),
        "grid.color": GRID,
        "grid.linewidth": px(1),
        "grid.linestyle": "-",
        "xtick.color": AXIS,
        "ytick.color": AXIS,
        "xtick.labelcolor": INK_2,
        "ytick.labelcolor": INK_2,
        "xtick.labelsize": 11,
        "ytick.labelsize": 11,
        "xtick.major.width": px(1),
        "ytick.major.width": px(1),
        "xtick.major.size": px(4),
        "ytick.major.size": px(4),
        "xtick.major.pad": px(4),
        "ytick.major.pad": px(4),
        "legend.frameon": False,
        "legend.fontsize": 11.5,
        "legend.labelcolor": INK_2,
        "legend.handlelength": 0.9,
        "legend.handleheight": 0.9,
        "legend.handletextpad": 0.5,
        "legend.columnspacing": 1.6,
        "legend.borderaxespad": 0.0,
        "lines.linewidth": px(2),
        "lines.solid_capstyle": "round",
        "lines.solid_joinstyle": "round",
        "patch.linewidth": 0,
        "axes.unicode_minus": True,
    })


# --------------------------------------------------------------------------
# number formatting
# --------------------------------------------------------------------------

def fmt_int(v) -> str:
    return f"{int(round(float(v))):,}"


def fmt_pct(v, nd: int = 1) -> str:
    return f"{float(v):.{nd}f}%"


def compact(v) -> str:
    """1,284 / 12.9K / 4.2M (one style per axis)."""
    v = float(v)
    a = abs(v)
    if a >= 1e6:
        return f"{v / 1e6:.1f}M".replace(".0M", "M")
    if a >= 1e4:
        return f"{v / 1e3:.0f}K"
    if a >= 1e3:
        return f"{v / 1e3:.1f}K".replace(".0K", "K")
    return f"{v:,.0f}"


def int_axis(ax, axis: str = "x") -> None:
    """Ticks in full with thousands separators (use under 10,000)."""
    from matplotlib.ticker import FuncFormatter
    f = FuncFormatter(lambda v, _: f"{v:,.0f}")
    (ax.xaxis if axis == "x" else ax.yaxis).set_major_formatter(f)


def pct_axis(ax, axis: str = "x", nd: int = 0) -> None:
    from matplotlib.ticker import FuncFormatter
    f = FuncFormatter(lambda v, _: f"{v:.{nd}f}%")
    (ax.xaxis if axis == "x" else ax.yaxis).set_major_formatter(f)


# --------------------------------------------------------------------------
# figure frame: title, subtitle, note, grid of panels
# --------------------------------------------------------------------------

def text_width_in(text: str, fontsize: float, weight: str = "normal") -> float:
    """Rendered width of one line of text, in inches (measured, not guessed)."""
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextToPath
    prop = FontProperties(family=FONT_STACK, size=fontsize, weight=weight)
    w, _, _ = TextToPath().get_text_width_height_descent(text, prop, ismath=False)
    return w / 72.0


def wrap(text: str, fontsize: float, width_in: float, weight: str = "normal") -> str:
    """Greedy word wrap to a width in inches, using measured text widths."""
    out = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            trial = f"{line} {word}" if line else word
            if line and text_width_in(trial, fontsize, weight) > width_in:
                out.append(line)
                line = word
            else:
                line = trial
        out.append(line)
    return "\n".join(out)


def _wrap(text: str, fontsize: float, width_in: float, weight: str = "normal") -> str:
    return wrap(text, fontsize, width_in, weight)


def figure(height_in: float, title: str, subtitle: Optional[str] = None,
           note: Optional[str] = None, *, nrows: int = 1, ncols: int = 1,
           width_ratios=None, height_ratios=None, left_in: float = 0.9,
           right_in: float = 0.4, wspace: float = 0.3, hspace: float = 0.45,
           top_in: Optional[float] = None, bottom_in: Optional[float] = None,
           panel_titles: bool = True, panel_title_lines: int = 1,
           legend_rows: int = 0, x_axis_band_in: float = 0.55):
    """A 10 in wide figure with a left-aligned title block and a footnote.

    The title states the finding; the subtitle says what is plotted; the note
    holds source and caveat. Returns (fig, gridspec) so callers can lay out
    panels freely with fig.add_subplot(gs[...]).

    The space above the top row of panels is reserved from the header height,
    the number of panel-title lines, and the number of legend rows that
    panel_title() will place there; pass the same counts you use below.
    """
    W = FIG_W_IN
    fig = plt.figure(figsize=(W, height_in))
    text_w = W - 2 * MARGIN_IN
    x0 = MARGIN_IN / W
    y = height_in - 0.3
    t = _wrap(title, 18, text_w, "bold")
    fig.text(x0, y / height_in, t, fontsize=18, weight="bold",
             color=INK, va="top", ha="left", linespacing=1.2)
    y -= 0.36 * (t.count("\n") + 1) + 0.08
    if subtitle:
        s = _wrap(subtitle, 12.5, text_w)
        fig.text(x0, y / height_in, s, fontsize=12.5, color=INK_2,
                 va="top", ha="left", linespacing=1.4)
        y -= 0.25 * (s.count("\n") + 1)
    header_in = height_in - y
    if top_in is None:
        top_in = header_in + 0.2
        if panel_titles:
            top_in += 0.3 * panel_title_lines + 0.12
        top_in += 0.34 * legend_rows
    note_h = 0.0
    if note:
        n = _wrap(note, 10.5, text_w)
        fig.text(x0, 0.2 / height_in, n, fontsize=10.5, color=MUTED,
                 va="bottom", ha="left", linespacing=1.35)
        note_h = 0.2 * (n.count("\n") + 1)
    if bottom_in is None:
        bottom_in = 0.3 + note_h + x_axis_band_in
    gs = fig.add_gridspec(
        nrows, ncols, left=left_in / W, right=1 - right_in / W,
        top=1 - top_in / height_in, bottom=bottom_in / height_in,
        wspace=wspace, hspace=hspace,
        width_ratios=width_ratios, height_ratios=height_ratios)
    return fig, gs


def clean_axes(ax, grid: Optional[str] = None, keep: Iterable[str] = ("left", "bottom")) -> None:
    """Hide spines not in `keep`; optional hairline grid on 'x', 'y' or 'both'."""
    for side in ("left", "right", "top", "bottom"):
        ax.spines[side].set_visible(side in keep)
    if grid in ("x", "both"):
        ax.xaxis.grid(True)
    if grid in ("y", "both"):
        ax.yaxis.grid(True)


def legend(ax, items: Sequence[tuple], *, loc: str = "lower left",
           anchor=(0.0, 1.0), ncol: Optional[int] = None, fontsize: float = 11.5,
           fig_level: bool = False):
    """One-row legend above a panel. items: (label, color, kind) with kind in
    'square', 'line', 'dot'. Labels use ink, the swatch carries the color."""
    handles = []
    for it in items:
        label, color = it[0], it[1]
        kind = it[2] if len(it) > 2 else "square"
        if kind == "line":
            handles.append(Line2D([0], [0], color=color, lw=px(2), label=label))
        elif kind == "dot":
            handles.append(Line2D([0], [0], color=color, lw=0, marker="o",
                                  markersize=px(9), markeredgecolor=SURFACE,
                                  markeredgewidth=px(1.5), label=label))
        else:
            handles.append(Patch(facecolor=color, edgecolor="none", label=label))
    target = ax.figure if fig_level else ax
    kw = dict(handles=handles, loc=loc, ncol=ncol or len(handles),
              fontsize=fontsize, frameon=False, borderaxespad=0.3, borderpad=0.0)  # pads are in font-size units
    if fig_level:
        kw["bbox_transform"] = ax.figure.transFigure
    return target.legend(bbox_to_anchor=anchor, **kw)


def panel_title(ax, title: str, legend_items: Optional[Sequence[tuple]] = None,
                *, ncol: Optional[int] = None, legend_rows: int = 1,
                reserve_rows: int = 0, fontsize: float = 13.5,
                legend_fontsize: float = 11.5):
    """Left-aligned panel title; with legend_items, a legend of legend_rows
    rows sits between the title and the plot so the two never collide.
    reserve_rows leaves the same space on a panel without a legend, so titles
    of side-by-side panels share one baseline."""
    pad = 12 + reserve_rows * 24
    leg = None
    if legend_items:
        pad = 12 + max(legend_rows, reserve_rows) * 24
        leg = legend(ax, legend_items, loc="lower left", anchor=(0.0, 1.0),
                     ncol=ncol, fontsize=legend_fontsize)
    # wrap to the panel width (plus the right gutter the title may use)
    pos = ax.get_position()
    width_in = pos.width * ax.figure.get_figwidth() + 0.2
    text = wrap(title, fontsize, width_in, "bold")
    ax.set_title(text, loc="left", pad=pad, fontsize=fontsize, linespacing=1.25)
    return leg


def segmented_bar(ax, parts: Sequence[tuple], *, height_px: float = 28,
                  label_fmt: Callable = lambda v, share: f"{share:.1f}%",
                  fontsize: float = 12):
    """Tall segmented bar: one whole split into <= 6 parts.

    parts: (name, value, color). Each part is its own block with 4 px corners
    and a 2 px surface gap; its share is printed inside at the leading edge
    when it fits (white, near-black on yellow), otherwise left to the legend.
    Returns the list of parts whose label did not fit.
    """
    from matplotlib.patches import FancyBboxPatch
    fig = ax.figure
    pos = ax.get_position()
    wpx = pos.width * fig.get_figwidth() * DPI
    hpx = pos.height * fig.get_figheight() * DPI
    ax.set_xlim(0, wpx)
    ax.set_ylim(0, hpx)
    ax.set_axis_off()
    total = float(sum(p[1] for p in parts)) or 1.0
    gap = GAP_PX * SCALE
    bar_h = height_px * SCALE
    y0 = (hpx - bar_h) / 2
    x = 0.0
    r = BAR_RADIUS_PX * SCALE
    renderer = fig.canvas.get_renderer()
    unlabeled = []
    for name, val, color in parts:
        w = wpx * val / total
        if w <= 0:
            continue
        bw = max(w - gap, 0.5)
        rr = min(r, bw / 2, bar_h / 2)
        ax.add_patch(FancyBboxPatch((x + rr, y0 + rr), max(bw - 2 * rr, 0.01), bar_h - 2 * rr,
                                    boxstyle=f"round,pad={rr}", facecolor=color,
                                    edgecolor="none", linewidth=0))
        txt = ax.text(x + 8 * SCALE, y0 + bar_h / 2, label_fmt(val, 100 * val / total),
                      va="center", ha="left", fontsize=fontsize, weight="bold",
                      color=on_fill(color))
        bb = txt.get_window_extent(renderer)
        if bb.width + 16 * SCALE > bw:
            txt.remove()
            unlabeled.append(name)
        x += w
    return unlabeled


# --------------------------------------------------------------------------
# bars: drawn normally, then capped and rounded at save time
# --------------------------------------------------------------------------

def _register(ax, patches, orient: str, *, cap: bool = True, round_end: bool = True,
              max_px: float = BAR_MAX_PX) -> None:
    reg = ax.__dict__.setdefault("_vf_bars", [])
    for p in patches:
        reg.append((p, orient, cap, round_end, max_px))


def register_bars(ax, patches, orient: str = "h", *, cap: bool = True,
                  round_end: bool = True, max_px: float = BAR_MAX_PX) -> None:
    """Mark bars drawn with ax.bar/ax.barh for the save-time pass.

    cap: limit thickness to max_px CSS px. round_end: 4 px rounded data end
    (use False for interior stacked segments and touching histogram bins).
    """
    _register(ax, patches, orient, cap=cap, round_end=round_end, max_px=max_px)


def gap_edge() -> dict:
    """Keyword args that draw the 2 px surface gap around touching fills."""
    return {"edgecolor": SURFACE, "linewidth": px(GAP_PX)}


def hbars(ax, labels: Sequence[str], values: Sequence[float], *, color=BLUE,
          colors: Optional[Sequence[str]] = None, fmt: Callable = fmt_int,
          label_values: bool = True, value_axis: bool = False,
          xmax: Optional[float] = None, fontsize: float = 11,
          label_color: str = INK_2, height: float = 0.66, gap: bool = False):
    """Ranked horizontal bars in the given order (first label on top).

    Values are printed at the bar tips; the value axis is hidden unless
    value_axis=True, since every value is labeled.
    """
    y = np.arange(len(labels))
    kw = gap_edge() if gap else {"linewidth": 0}
    bars = ax.barh(y, values, height=height, color=colors if colors is not None else color, **kw)
    ax.set_yticks(y, labels)
    ax.set_ylim(len(labels) - 0.5, -0.5)
    ax.tick_params(axis="y", length=0)
    vmax = xmax if xmax is not None else (max(values) if len(values) else 1) * 1.14
    ax.set_xlim(0, vmax)
    if label_values:
        for b, v in zip(bars, values):
            ax.annotate(fmt(v), xy=(v, b.get_y() + b.get_height() / 2),
                        xytext=(px(6), 0), textcoords="offset points",
                        va="center", ha="left", fontsize=fontsize, color=label_color)
    if value_axis:
        clean_axes(ax, grid="x", keep=("left",))
        ax.tick_params(axis="x", length=0)
    else:
        clean_axes(ax, keep=("left",))
        ax.set_xticks([])
    register_bars(ax, bars, "h")
    return bars


def vbars(ax, x, values: Sequence[float], *, color=BLUE, colors=None,
          width: float = 0.66, cap: bool = True, round_end: bool = True,
          gap: bool = False, **kw):
    """Columns from a zero baseline. For touching histogram bins pass
    width=1.0, gap=True, cap=False."""
    extra = gap_edge() if gap else {"linewidth": 0}
    extra.update(kw)
    bars = ax.bar(x, values, width=width, color=colors if colors is not None else color, **extra)
    register_bars(ax, bars, "v", cap=cap, round_end=round_end)
    return bars


def _cap_bar(ax, rect: Rectangle, orient: str, max_px: float) -> None:
    T = ax.transData
    x, y, w, h = rect.get_x(), rect.get_y(), rect.get_width(), rect.get_height()
    limit = max_px * SCALE
    if orient == "h":
        thick = abs(T.transform((x, y + h))[1] - T.transform((x, y))[1])
        if thick > limit and h != 0:
            nh = h * limit / thick
            rect.set_y(y + (h - nh) / 2)
            rect.set_height(nh)
    else:
        thick = abs(T.transform((x + w, y))[0] - T.transform((x, y))[0])
        if thick > limit and w != 0:
            nw = w * limit / thick
            rect.set_x(x + (w - nw) / 2)
            rect.set_width(nw)


def _round_bar(ax, rect: Rectangle, orient: str) -> None:
    """Replace a Rectangle by a path with a rounded data end, square base."""
    T = ax.transData
    x, y, w, h = rect.get_x(), rect.get_y(), rect.get_width(), rect.get_height()
    if w == 0 or h == 0 or not rect.get_visible():
        return
    if orient == "h":
        base = T.transform((x, y + h / 2))[0]
        end = T.transform((x + w, y + h / 2))[0]
        a = T.transform((x, y))[1]
        b = T.transform((x, y + h))[1]
    else:
        base = T.transform((x + w / 2, y))[1]
        end = T.transform((x + w / 2, y + h))[1]
        a = T.transform((x, y))[0]
        b = T.transform((x + w, y))[0]
    lo, hi = min(a, b), max(a, b)
    length = abs(end - base)
    r = min(BAR_RADIUS_PX * SCALE, length / 2, (hi - lo) / 2)
    s = 1.0 if end >= base else -1.0
    # (u along the value axis, v across), in display px
    uv = [(base, lo), (end - s * r, lo), (end, lo), (end, lo + r),
          (end, hi - r), (end, hi), (end - s * r, hi), (base, hi), (base, lo)]
    codes = [MPath.MOVETO, MPath.LINETO, MPath.CURVE3, MPath.CURVE3,
             MPath.LINETO, MPath.CURVE3, MPath.CURVE3, MPath.LINETO, MPath.CLOSEPOLY]
    disp = [(u, v) if orient == "h" else (v, u) for u, v in uv]
    data = T.inverted().transform(np.array(disp))
    patch = PathPatch(MPath(data, codes), facecolor=rect.get_facecolor(),
                      edgecolor=rect.get_edgecolor(), linewidth=rect.get_linewidth(),
                      zorder=rect.get_zorder(), alpha=rect.get_alpha(),
                      transform=ax.transData)
    xl, yl = ax.get_xlim(), ax.get_ylim()
    ax.add_patch(patch)
    ax.set_xlim(xl)
    ax.set_ylim(yl)
    rect.set_visible(False)


def _finalize(fig) -> None:
    fig.canvas.draw()
    for ax in fig.axes:
        reg = ax.__dict__.get("_vf_bars", [])
        for p, orient, cap, _, max_px in reg:
            if cap and isinstance(p, Rectangle):
                _cap_bar(ax, p, orient, max_px)
        for p, orient, _, round_end, _ in reg:
            if round_end and isinstance(p, Rectangle):
                _round_bar(ax, p, orient)
        ax.__dict__["_vf_bars"] = []


def save(fig, path, close: bool = True) -> Path:
    """Finalize bars, then write the PNG atomically (temp name, then rename).

    No bbox_inches='tight', so the output is exactly FIG_W_IN * DPI px wide.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _finalize(fig)
    tmp = path.with_name(f".{path.name}.tmp")
    fig.savefig(tmp, format=path.suffix.lstrip(".") or "png", dpi=DPI,
                facecolor=SURFACE)
    os.replace(tmp, path)
    if close:
        plt.close(fig)
    return path


def stacked_hbars(ax, labels: Sequence[str], matrix, colors: Sequence[str], *,
                  label_fmt: Optional[Callable] = None, height: float = 0.62,
                  fontsize: float = 10.5, label_parts: Optional[Sequence[bool]] = None,
                  xmax: Optional[float] = None):
    """Horizontal stacked bars, first label on top.

    matrix: rows x parts of values (already normalized if you want shares).
    Segments are separated by the 2 px surface gap; only the outermost
    segment of each bar gets the rounded data end. label_fmt(value, row_total)
    prints a label inside a segment only when it fits with padding (white,
    near-black on yellow); label_parts limits which parts may be labeled.
    Returns the list of (row, part) segments whose label did not fit.
    """
    M = np.asarray(matrix, dtype=float)
    n, k = M.shape
    y = np.arange(n)
    left = np.zeros(n)
    fig = ax.figure
    totals = M.sum(axis=1)
    ax.set_yticks(y, labels)
    ax.set_ylim(n - 0.5, -0.5)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, xmax if xmax is not None else totals.max())
    seg = []
    for j in range(k):
        bars = ax.barh(y, M[:, j], left=left, height=height, color=colors[j], **gap_edge())
        for i, b in enumerate(bars):
            outer = not (M[i, j + 1:] > 0).any()
            register_bars(ax, [b], "h", round_end=outer)
            seg.append((i, j, b))
        left += M[:, j]
    unfit = []
    if label_fmt is not None:
        renderer = fig.canvas.get_renderer()
        T = ax.transData
        for i, j, b in seg:
            if M[i, j] <= 0 or (label_parts is not None and not label_parts[j]):
                continue
            x0, w = b.get_x(), b.get_width()
            wpx = abs(T.transform((x0 + w, 0))[0] - T.transform((x0, 0))[0])
            color = colors[j]
            txt = ax.text(x0, i, label_fmt(M[i, j], totals[i]), va="center", ha="left",
                          fontsize=fontsize, weight="bold",
                          color=on_fill(color))
            txt.set_x(x0)
            from matplotlib.transforms import offset_copy
            txt.set_transform(offset_copy(T, fig=fig, x=px(6), y=0, units="points"))
            if txt.get_window_extent(renderer).width + px(12) * DPI / 72 > wpx:
                txt.remove()
                unfit.append((i, j))
    return unfit


def label_points(ax, xs, ys, texts, *, dx_px: float = 7, fontsize: float = 9.5,
                 color: str = INK_2) -> None:
    """Label selected scatter points to their right, pushing a label down when
    it would overlap one already placed; a displaced label gets a thin leader
    line back to its point. Set the axes limits before calling."""
    T = ax.transData
    h = fontsize * DPI / 72 * 1.3
    pts = [T.transform((x, y)) for x, y in zip(xs, ys)]
    placed = []
    for i in sorted(range(len(texts)), key=lambda i: -pts[i][1]):
        px_, py_ = pts[i]
        w = text_width_in(texts[i], fontsize) * DPI
        x0, x1 = px_ + dx_px * SCALE, px_ + dx_px * SCALE + w
        y = py_
        moved = True
        while moved:
            moved = False
            for a0, a1, ay in placed:
                if x0 < a1 and a0 < x1 and abs(y - ay) < h:
                    y = ay - h
                    moved = True
        placed.append((x0, x1, y))
        tx, ty = T.inverted().transform((x0, y))
        lead = abs(y - py_) > 2
        ax.annotate(texts[i], xy=(xs[i], ys[i]), xytext=(tx, ty), textcoords="data",
                    ha="left", va="center", fontsize=fontsize, color=color,
                    arrowprops=dict(arrowstyle="-", color=MUTED, lw=px(1), shrinkA=0,
                                    shrinkB=4) if lead else None)
