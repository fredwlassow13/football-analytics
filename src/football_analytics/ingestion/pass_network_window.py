from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from football_analytics.ingestion.action_context import ContextualizedAction
from football_analytics.integrations.base import MatchPass


class PassNetworkWindow(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    external_match_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    start_source_index: int = Field(gt=0)
    end_source_index: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> "PassNetworkWindow":
        if self.end_source_index <= self.start_source_index:
            raise ValueError("Window end must follow start")

        return self


def select_pass_network_window(
    actions: Sequence[ContextualizedAction],
    window: PassNetworkWindow,
) -> list[ContextualizedAction]:
    expected_identity = (window.provider, window.external_match_id)

    if any(
        (item.action.provider, item.action.external_match_id) != expected_identity
        for item in actions
    ):
        raise ValueError("Window and actions must share provider and match")

    return sorted(
        (
            item
            for item in actions
            if isinstance(item.action, MatchPass)
            and item.action.period in {1, 2, 3, 4}
            and window.start_source_index <= item.action.source_index < window.end_source_index
        ),
        key=lambda item: item.action.source_index,
    )
