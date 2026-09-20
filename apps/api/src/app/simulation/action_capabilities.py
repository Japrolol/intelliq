"""Actions with an existing, verified Timecue write representation."""

from collections.abc import Mapping, Sequence

TIMECUE_DATE_ACTIONS = frozenset(
    {"date_shift", "schedule_shift", "weather_shift", "material_shift"}
)


def executable_actions(actions: object) -> bool:
    """Require the whole simulated bundle; never silently discard manual steps."""
    return (
        isinstance(actions, Sequence)
        and not isinstance(actions, (str, bytes))
        and bool(actions)
        and all(
            isinstance(action, Mapping)
            and action.get("type") in TIMECUE_DATE_ACTIONS
            and bool(action.get("taskId"))
            and bool(action.get("startsAt"))
            and bool(action.get("endsAt"))
            for action in actions
        )
    )
