"""Export a score timeline, cumulative xG and creation from local StatsBomb sources."""

import hashlib
import json
from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from football_analytics.ingestion.match_evolution import MatchEvolution, build_match_evolution
from football_analytics.integrations.base import MatchShot
from football_analytics.integrations.statsbomb.creation import normalize_creation_events
from football_analytics.integrations.statsbomb.event_relations import normalize_event_relations
from football_analytics.integrations.statsbomb.events import normalize_events
from football_analytics.integrations.statsbomb.period_clock import normalize_period_clock
from football_analytics.integrations.statsbomb.shots import normalize_shots


def evolution_from_payload(
    payload: object,
    *,
    match_id: int,
    team_ids: tuple[str, str],
    events_sha256: str,
) -> MatchEvolution:
    return build_match_evolution(
        normalize_events(payload, match_id=match_id),
        normalize_shots(payload, match_id=match_id),
        normalize_event_relations(payload, match_id=match_id),
        normalize_creation_events(payload, match_id=match_id),
        normalize_period_clock(payload),
        provider="statsbomb",
        match_id=str(match_id),
        team_ids=team_ids,
        events_sha256=events_sha256,
    )


def render_match_evolution(result: MatchEvolution, names: dict[str, str]) -> Figure:
    figure = Figure(figsize=(13, 10), layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots(3, 1, sharex=True)
    end = result.periods[-1].end_elapsed_ms / 60000
    for i, team in enumerate(result.team_ids):
        color = ("#0891b2", "#9333ea")[i]
        points = [p for p in result.points if p.external_team_id == team]
        # Preserve source order and original clocks, including diagnosed regressions.
        x = [0.0, *[p.elapsed_ms / 60000 for p in points], end]
        xg = [0.0, *[p.xg_available for p in points], points[-1].xg_available if points else 0.0]
        creation = [
            0,
            *[p.created_shots for p in points],
            points[-1].created_shots if points else 0,
        ]
        label = names.get(team, team)
        if points and points[-1].missing_xg:
            label += f" (xG parcial: {points[-1].missing_xg} ausentes)"
        axes[0].step(x, xg, where="post", label=label, color=color)
        axes[2].step(x, creation, where="post", label=names.get(team, team), color=color)
        if result.goal_evidence.status == "resolved":
            goals = [r for r in result.events if r.scoring_team_id == team]
            axes[1].step(
                [0.0, *[g.elapsed_ms / 60000 for g in goals], end],
                [0, *range(1, len(goals) + 1), len(goals)],
                where="post",
                label=names.get(team, team),
                color=color,
            )
        else:
            axes[1].text(
                0.5,
                0.5,
                "Placar desconhecido: evidência requer revisão",
                transform=axes[1].transAxes,
                ha="center",
            )
    for ax, title in zip(
        axes,
        (
            "xG pré-chute disponível acumulado",
            "Gols (inclui gols contra)",
            "Chances criadas: pares passe–chute utilizáveis",
        ),
        strict=True,
    ):
        ax.set_title(title, fontsize=11, loc="left")
        ax.set_ylabel("xG" if ax is axes[0] else "Contagem")
        ax.set_ylim(bottom=0)
        ax.set_xlim(0, end)
        ax.grid(alpha=0.2)
        handles, _ = ax.get_legend_handles_labels()
        if handles:
            ax.legend(loc="upper left")
        for p in result.periods[1:]:
            ax.axvline(p.start_elapsed_ms / 60000, color="#9ca3af", linestyle="--", linewidth=0.8)
    axes[-1].set_xlabel(
        "Minutos decorridos: inclui acréscimos; exclui intervalos e disputa de pênaltis"
    )
    figure.suptitle(f"Evolução da partida {result.external_match_id}")
    footer = "Criação atribuída ao passe; contexto de placar anterior à ação; dados observados."
    if result.issues:
        labels = {"source_clock_not_monotonic": "relógio da fonte recua entre alguns eventos"}
        footer += "\nRevisão: " + ", ".join(labels.get(issue, issue) for issue in result.issues)
    figure.supxlabel(footer, fontsize=9)
    return figure


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--match-id", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("Output directory exists; choose a new destination")
    raw = args.events.read_bytes()
    matches_raw = args.matches.read_bytes()
    matches = [m for m in json.loads(matches_raw) if m["match_id"] == args.match_id]
    if len(matches) != 1:
        parser.error("Exactly one matching match record required")
    match = matches[0]
    teams = tuple(str(match[f"{side}_team"][f"{side}_team_id"]) for side in ("home", "away"))
    team_ids = (teams[0], teams[1])
    names = {
        str(match[f"{side}_team"][f"{side}_team_id"]): match[f"{side}_team"][f"{side}_team_name"]
        for side in ("home", "away")
    }
    result = evolution_from_payload(
        json.loads(raw),
        match_id=args.match_id,
        team_ids=team_ids,
        events_sha256=hashlib.sha256(raw).hexdigest(),
    )
    recorded = (match["home_score"], match["away_score"])
    reconstructed = result.events[-1].score_after if result.events else None
    reconciliation = (
        "review"
        if reconstructed is None
        else ("matched" if reconstructed == recorded else "mismatch")
    )
    if reconciliation == "mismatch":
        parser.error(f"Reconstructed score {reconstructed} differs from recorded {recorded}")
    metadata = {
        "evolution": result.model_dump(mode="json"),
        "recorded_score": recorded,
        "score_reconciliation": reconciliation,
        "matches_sha256": hashlib.sha256(matches_raw).hexdigest(),
        "team_names": names,
    }
    figure = render_match_evolution(result, names)
    import io

    buffer = io.BytesIO()
    try:
        figure.savefig(buffer, format="png", dpi=150, bbox_inches="tight")
    finally:
        figure.clear()
    image = buffer.getvalue()
    metadata["image_sha256"] = hashlib.sha256(image).hexdigest()
    encoded = json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.output_dir.mkdir(parents=True, exist_ok=False)
    image_path, data_path = args.output_dir / "plot.png", args.output_dir / "data.json"
    try:
        with image_path.open("xb") as handle:
            handle.write(image)
        with data_path.open("x") as handle:
            handle.write(encoded)
    except Exception:
        image_path.unlink(missing_ok=True)
        data_path.unlink(missing_ok=True)
        args.output_dir.rmdir()
        raise
    shots = normalize_shots(json.loads(raw), match_id=args.match_id)
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "score": reconstructed,
                "score_reconciliation": reconciliation,
                "events": len(result.events),
                "shots": sum(isinstance(s, MatchShot) and s.period != 5 for s in shots),
                "issues": result.issues,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
