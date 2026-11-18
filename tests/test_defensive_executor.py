from decimal import Decimal

import pytest
from test_analytics_executor import execute, mapping, match_data, query

from football_analytics.analytics.catalogue import METRICS


def data():
    ref, events, lineups = match_data()
    for event in events[:2]:
        event["tactics"]["formation"] = 442
        for n, player in enumerate(event["tactics"]["lineup"]):
            player["position"] = {
                "id": 1 if n == 0 else 2,
                "name": "Goalkeeper" if n == 0 else "Right Back",
            }
    events[3]["related_events"] = ["keeper"]
    events.append(
        {
            "id": "keeper",
            "index": 8,
            "period": 2,
            "timestamp": "00:02:00.000",
            "type": {"name": "Goal Keeper"},
            "team": {"id": 2},
            "player": {"id": 201},
            "related_events": ["shot"],
            "goalkeeper": {"type": {"name": "Shot Saved"}, "outcome": {"name": "Success"}},
        }
    )

    def add(index, kind, pid, **details):
        events.append(
            {
                "id": f"d{index}",
                "index": index,
                "period": 1,
                "timestamp": "00:03:00.000",
                "type": {"name": kind},
                "team": {"id": 1},
                "player": {"id": pid},
                **details,
            }
        )

    add(9, "Interception", 102, interception={"outcome": {"name": "Won"}})
    add(10, "Duel", 103, duel={"type": {"name": "Tackle"}, "outcome": {"name": "Won"}})
    add(11, "Clearance", 104, clearance={"aerial_won": True, "body_part": {"name": "Head"}})
    add(12, "Duel", 104, duel={"type": {"name": "Aerial Lost"}})
    add(13, "Block", 105, block={"offensive": False})
    return ref, events, lineups


def test_defense_union_duels_and_aerial_ratio():
    r = execute(
        query(
            metrics=(
                "defense.unique_sources",
                "defense.interceptions",
                "duels.tackles.won",
                "aerial.won",
                "aerial.lost",
                "aerial.win_pct",
            )
        ),
        data(),
    )
    p = {row.player_id: mapping(row) for row in r.rows}
    assert p["102"]["defense.interceptions"].value == 1
    assert p["103"]["duels.tackles.won"].value == 1
    assert (
        p["104"]["defense.unique_sources"].value == 2
    )  # Clearance and its aerial win share an ID.
    assert p["104"]["aerial.win_pct"].value == Decimal("50.00")
    assert p["106"]["defense.interceptions"].value == 0
    assert p["106"]["aerial.win_pct"].status == "not_applicable"
    assert p["112"]["defense.interceptions"].status == "unknown"


def test_flags_preserve_missing_false_and_true():
    r = execute(
        query(
            player_ids=("105",),
            metrics=(
                "defense.blocks.offensive.false",
                "defense.blocks.offensive.unknown",
                "defense.blocks.save_block.unknown",
            ),
        ),
        data(),
    )
    v = mapping(r.rows[0])
    assert v["defense.blocks.offensive.false"].value == 1
    assert v["defense.blocks.offensive.unknown"].value == 0
    assert v["defense.blocks.save_block.unknown"].value == 1


def test_keeper_role_presence_and_nonkeeper_are_not_interchangeable():
    r = execute(query(metrics=("goalkeepers.counts.on_target_saves",)), data())
    p = {row.player_id: row.values[0] for row in r.rows}
    assert p["201"].value == 1
    assert p["101"].value == 0  # Recorded keeper role with presence, no keeper actions.
    assert p["102"].status == "not_applicable"
    assert p["112"].status == "unknown"  # Unobserved reserve: do not infer role or zero.
    first_half = execute(
        query(periods=(1,), player_ids=("201",), metrics=("goalkeepers.counts.on_target_saves",)),
        data(),
    )
    assert first_half.rows[0].values[0].value == 0


def test_all_new_definitions_and_period_selection():
    metrics = tuple(
        m.id for m in METRICS if m.family in {"defense", "duels", "aerial", "goalkeepers"}
    )
    r = execute(query(group_by="team", metrics=metrics), data())
    assert len(r.rows) == 2
    assert mapping(r.rows[0])["defense.unique_sources"].value == 5
    r = execute(query(group_by="team", periods=(2,), metrics=("defense.unique_sources",)), data())
    assert all(row.values[0].value == 0 for row in r.rows)


def test_keeper_review_pairs_are_not_saves():
    d = data()
    d[1][-6]["goalkeeper"]["type"]["name"] = "Shot Faced"
    r = execute(
        query(
            player_ids=("201",),
            metrics=("goalkeepers.counts.on_target_saves", "goalkeepers.counts.review_pairs"),
        ),
        d,
    )
    values = mapping(r.rows[0])
    assert values["goalkeepers.counts.on_target_saves"].value == 0
    assert values["goalkeepers.counts.review_pairs"].value == 1
    assert (
        "goalkeeper_pairs_require_review"
        in values["goalkeepers.counts.on_target_saves"].review_reasons
    )


def test_unknown_tactical_position_does_not_become_outfield_or_zero():
    d = data()
    # An explicit tactical shift no longer records player 101's position.
    d[1].append(
        {
            "id": "shift",
            "index": 14,
            "period": 2,
            "timestamp": "00:00:00.000",
            "type": {"name": "Tactical Shift"},
            "team": {"id": 1},
            "tactics": {"formation": 442, "lineup": [d[1][0]["tactics"]["lineup"][1]]},
        }
    )
    r = execute(
        query(player_ids=("101",), periods=(2,), metrics=("goalkeepers.counts.pair_count",)), d
    )
    assert r.rows[0].values[0].status == "unknown"


def test_mixed_query_keeps_offensive_values_and_defensive_population_separate():
    r = execute(
        query(metrics=("shots.xg", "defense.interceptions", "goalkeepers.counts.on_target_saves")),
        data(),
    )
    p = {row.player_id: mapping(row) for row in r.rows}
    assert p["101"]["shots.xg"].value == Decimal("0.25")
    assert p["102"]["defense.interceptions"].value == 1
    assert p["201"]["goalkeepers.counts.on_target_saves"].value == 1


def test_keeper_unlisted_or_without_presence_is_rejected():
    for pid in (999, 212):
        d = data()
        d[1][-6]["player"]["id"] = pid
        with pytest.raises(ValueError, match="unlisted|participation"):
            execute(query(metrics=("goalkeepers.counts.on_target_saves",)), d)


def test_unlinked_shot_action_is_reviewed_but_sweeper_is_not():
    d = data()
    d[1][3].pop("related_events")
    d[1][-6].pop("related_events")
    r = execute(query(player_ids=("201",), metrics=("goalkeepers.counts.pair_count",)), d)
    assert "unlinked_goalkeeper_shot_action" in r.rows[0].values[0].review_reasons
    d[1][-6]["goalkeeper"]["type"]["name"] = "Keeper Sweeper"
    r = execute(query(player_ids=("201",), metrics=("goalkeepers.counts.pair_count",)), d)
    assert "unlinked_goalkeeper_shot_action" not in r.rows[0].values[0].review_reasons


def test_penalty_and_unknown_shot_type_remain_separate():
    d = data()
    d[1][3]["shot"]["type"]["name"] = "Penalty"
    d[1][-6]["goalkeeper"]["type"]["name"] = "Penalty Saved"
    r = execute(
        query(
            player_ids=("201",),
            metrics=(
                "goalkeepers.penalty_counts.on_target_saves",
                "goalkeepers.non_penalty_counts.on_target_saves",
            ),
        ),
        d,
    )
    assert [v.value for v in r.rows[0].values] == [1, 0]
    d[1][3]["shot"].pop("type")
    r = execute(
        query(
            player_ids=("201",),
            metrics=(
                "goalkeepers.unknown_penalty_counts.review_pairs",
                "goalkeepers.counts.on_target_saves",
            ),
        ),
        d,
    )
    assert [v.value for v in r.rows[0].values] == [1, 0]
