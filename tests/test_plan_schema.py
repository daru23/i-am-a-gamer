from agent.providers.base import WAIT_PLAN, parse_plan


def test_valid_plan_parses():
    plan = parse_plan('{"reasoning": "grab infotron", "moves": ["right", "down"]}')
    assert plan is not None and plan.moves == ["right", "down"]


def test_invalid_move_rejected():
    assert parse_plan('{"reasoning": "x", "moves": ["jump"]}') is None


def test_wait_fallback():
    assert WAIT_PLAN.moves == ["wait"]
