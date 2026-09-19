"""Plot saved evaluation results without rerunning matching.

Run with: uv run --frozen python scripts/plot_evaluation.py
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.ticker import PercentFormatter

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "data" / "reports"
OUTPUT = ROOT / "outputs" / "figures"
BLUE = "#0072B2"
ORANGE = "#D55E00"
PALE = "#E5E5E5"
INK = "#222222"
WIDTH = 6.69  # Inches; fonts are sized for print.


def save(fig: plt.Figure, name: str) -> None:
    for extension in ("png", "svg"):
        metadata = {"Date": None} if extension == "svg" else {}
        fig.savefig(OUTPUT / f"{name}.{extension}", dpi=600, facecolor="white",
                    bbox_inches="tight", pad_inches=0.03, metadata=metadata)
    plt.close(fig)


def ablation() -> None:
    results = json.loads((REPORTS / "horizon-dev-ablation.json").read_text())
    stages = [
        ("normalized_exact", "Exact\nonly"),
        ("lexical", "+ Lexical\nretrieval"),
        ("coverage", "+ Token\ncoverage"),
        ("properties", "+ Geographic\nproperties"),
        ("margin_and_guard", "+ Margin /\ngeneric guard"),
        ("full_with_trigram", "+ Trigram\nretrieval"),
    ]
    fig, ax = plt.subplots(figsize=(WIDTH, 3.05))
    for metric, label, colour, marker, linestyle in [
        ("selective_precision", "Selective precision", BLUE, "o", "-"),
        ("end_to_end_recall", "End-to-end recall", ORANGE, "s", "--"),
    ]:
        values = [results[key][metric] for key, _ in stages]
        ax.plot(range(6), values, color=colour, marker=marker, linestyle=linestyle,
                lw=1.35, ms=4.5, markeredgewidth=1,
                markerfacecolor=colour if marker == "o" else "white", label=label)
    ax.set_xticks(range(6), [label for _, label in stages])
    ax.set_ylim(0, 1.06)
    ax.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1])
    ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.set_xlim(-0.2, 5.2)
    ax.set_ylabel("Proportion", labelpad=6)
    ax.grid(axis="y", color="#E5E5E5", linewidth=0.5)
    ax.set_axisbelow(True)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.025), ncol=2, frameon=False,
              handlelength=2.6, columnspacing=2.5, borderaxespad=0)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("bottom", "left"):
        ax.spines[spine].set_color("#777777")
        ax.spines[spine].set_linewidth(0.6)
    ax.tick_params(axis="both", length=3, width=0.6, color="#777777", pad=5)
    ax.tick_params(axis="x", labelsize=9)
    fig.subplots_adjust(left=0.105, right=0.975, bottom=0.19, top=0.86)
    save(fig, "development-ablation")


def held_out_counts() -> dict[str, dict[bool, Counter]]:
    local = list(csv.DictReader((REPORTS / "horizon-test-evaluation-cases.csv").open()))
    hosted = json.loads((REPORTS / "horizon-test-ror-api-baseline.json").read_text())["cases"]
    assert {row["case_id"] for row in local} == {row["case_id"] for row in hosted}
    counts = {name: {True: Counter(), False: Counter()} for name in ("Local service", "Hosted ROR")}
    for name, rows in (("Local service", local), ("Hosted ROR", hosted)):
        for row in rows:
            expected = row["expected_ror_id"] or None
            predicted = row["predicted_ror_id"] or None
            if row.get("lookup_error"):
                outcome = "error"
            elif predicted:
                outcome = "correct" if predicted == expected else "wrong"
            else:
                outcome = "unresolved" if expected else "correct"
            counts[name][bool(expected)][outcome] += 1
        assert sum(sum(group.values()) for group in counts[name].values()) == 75
    return counts


def outcomes() -> None:
    counts = held_out_counts()
    fig, axes = plt.subplots(2, 1, figsize=(WIDTH, 3.75), sharex=True)
    colours = {"correct": BLUE, "wrong": ORANGE, "unresolved": PALE, "error": INK}
    for ax, known, title in zip(
        axes, (True, False),
        ("(a) Available ROR match (n = 36)", "(b) No ROR match (n = 39)"),
        strict=True,
    ):
        for y, name in ((1, "Local service"), (0, "Hosted ROR")):
            left = 0
            for outcome in ("correct", "wrong", "unresolved", "error"):
                value = counts[name][known][outcome]
                if not value:
                    continue
                ax.barh(y, value, left=left, height=0.42, color=colours[outcome],
                        edgecolor="white", linewidth=0.7,
                        hatch="///" if outcome == "wrong" else None)
                if value == 1:
                    ax.annotate(str(value), (left + value / 2, y), xytext=(9, 0),
                                textcoords="offset points", ha="left", va="center",
                                arrowprops={"arrowstyle": "-", "color": INK, "lw": 0.5},
                                fontsize=9.5)
                else:
                    ax.text(left + value / 2, y, str(value), ha="center", va="center",
                            color=INK if outcome == "unresolved" else "white", fontsize=9.5,
                            bbox={"facecolor": colours[outcome], "edgecolor": "none", "pad": 0.5})
                left += value
        ax.set_yticks([1, 0], ["Local service", "ROR search\n(top result)"])
        ax.set_title(title, loc="left", fontsize=10, pad=7)
        ax.set_ylim(-0.48, 1.48)
        ax.set_xlim(0, 40)
        ax.set_xticks([0, 10, 20, 30, 40])
        ax.grid(axis="x", color="#E5E5E5", linewidth=0.5)
        ax.set_axisbelow(True)
        ax.tick_params(axis="both", length=0, pad=5)
        for spine in ax.spines.values():
            spine.set_visible(False)
    axes[1].set_xlabel("Number of organisations", labelpad=6)
    legend = [Patch(facecolor=colours[key], edgecolor="white", linewidth=0.5,
                    hatch="///" if key == "wrong" else None, label=label) for key, label in [
        ("correct", "Correct decision"), ("wrong", "Incorrect link"),
        ("unresolved", "Unresolved match"), ("error", "Lookup error"),
    ]]
    fig.legend(handles=legend, loc="lower center", ncol=4, frameon=False,
               bbox_to_anchor=(0.51, 0.005), fontsize=9, handlelength=1.7, columnspacing=1.5)
    fig.subplots_adjust(left=0.19, right=0.975, bottom=0.24, top=0.90, hspace=0.62)
    save(fig, "held-out-outcomes")
    print(json.dumps({name: {str(known): dict(values) for known, values in groups.items()}
                      for name, groups in counts.items()}, indent=2))


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    plt.rcdefaults()
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 9.5, "text.color": INK,
        "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK,
        "axes.titlesize": 10, "axes.titleweight": "normal", "svg.fonttype": "path",
        "svg.hashsalt": "ror-reconcile-evaluation",
        "hatch.linewidth": 0.45,
        "savefig.facecolor": "white",
    })
    ablation()
    outcomes()


if __name__ == "__main__":
    main()
