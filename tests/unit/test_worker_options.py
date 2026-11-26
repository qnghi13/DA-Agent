"""Which agent options a run job asks for (ADR 0051). Jobs queued before analysis existed carry
no analysis key, and must run as plain runs: a resumed run must not change its tools midway."""

from da_agent.worker.runs import graph_options


def test_a_new_api_run_asks_for_charts_and_analysis() -> None:
    options = graph_options({"charts": True, "analysis": True})

    assert options == {"charts": True, "analysis": True, "analysis_kind": None}


def test_a_job_queued_before_analysis_runs_without_it() -> None:
    assert graph_options({"question": "Total?"}) == {
        "charts": True,
        "analysis": False,
        "analysis_kind": None,
    }


def test_a_forced_kind_is_passed_on() -> None:
    options = graph_options({"analysis": True, "analysis_kind": "explain_change"})

    assert options["analysis_kind"] == "explain_change"
