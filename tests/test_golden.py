from content_core.golden import GOLDEN_BRIEFS
from content_core.graph import run_flow
from content_core.runner import check_case


def test_all_six_golden_briefs_pass():
    assert len(GOLDEN_BRIEFS) == 6
    for case in GOLDEN_BRIEFS:
        state = run_flow(case["brief"], outline_approved=True)
        ok, detail = check_case(case, state)
        assert ok, f"{case['brief']['id']}: {detail} status={state.get('status')}"


def test_golden_runner_exit_code_zero():
    from content_core.runner import main
    assert main() == 0
