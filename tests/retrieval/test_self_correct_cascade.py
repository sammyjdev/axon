from axon.retrieval.self_correct import aggregate_score, grade, is_structural


def test_aggregate_score_is_best_hit():
    hits = [{"score": 0.4}, {"score": 0.9}, {"score": 0.1}]
    assert aggregate_score(hits) == 0.9


def test_aggregate_score_empty_is_zero():
    assert aggregate_score([]) == 0.0


def test_is_structural_true_on_dependency_phrasing():
    assert is_structural("quem usa PolicyRegistry?")
    assert is_structural("what depends on AuthService")


def test_is_structural_true_on_symbol_token():
    assert is_structural("ContextDetector.detect flow")


def test_is_structural_false_on_prose_query():
    assert not is_structural("como funciona a compressao de contexto")


def test_grade_empty_hits_insufficient_without_judge():
    called = []
    verdict = grade([], "q", "", lambda q, c: called.append(1) or True)
    assert verdict == (False, "empty")
    assert called == []


def test_grade_low_score_insufficient_without_judge():
    verdict = grade([{"score": 0.10}], "q", "ctx", lambda q, c: True)
    assert verdict == (False, "low_score")


def test_grade_high_score_sufficient_without_judge():
    called = []
    verdict = grade([{"score": 0.90}], "q", "ctx", lambda q, c: called.append(1) or False)
    assert verdict == (True, "high_score")
    assert called == []


def test_grade_gray_zone_defers_to_judge():
    assert grade([{"score": 0.50}], "q", "ctx", lambda q, c: True) == (True, "judge_sufficient")
    assert grade([{"score": 0.50}], "q", "ctx", lambda q, c: False) == (False, "judge_insufficient")
