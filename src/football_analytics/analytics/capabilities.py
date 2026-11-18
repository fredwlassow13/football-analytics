"""Families integrated and accepted by the shared executor."""

EXECUTOR_FAMILIES = frozenset(
    {
        "passes",
        "shots",
        "xg",
        "participation",
        "defense",
        "duels",
        "aerial",
        "goalkeepers",
        "dribbles",
        "creation",
        "movement",
        "ball_control",
    }
)

STRUCTURED_EXECUTOR_RESOURCES = frozenset(
    {
        "analysis.pass_connections",
        "analysis.defensive_map",
    }
)
