import copy
import pytest
from rating.reports import AnalysisError, parse_json_report, parse_html_report, log_matches


def test_json_scale_and_actual_probabilities(report):
    r = parse_json_report(report, 1)
    assert r['rating'] == pytest.approx(90.25)
    assert r['agreement'] == pytest.approx(200/3)
    assert r['bad10'] == pytest.approx(100/3)
    assert r['bad5'] == pytest.approx(100/3)


def test_html_same_metrics(html_report):
    r = parse_html_report(html_report, 1)
    assert r['rating'] == 90.25 and r['total_matches'] == 2
    assert r['bad10'] is None and r['player_id'] == 1


def test_missing_rating_is_not_inferred(html_report):
    with pytest.raises(AnalysisError, match='未公开 Rating'):
        parse_html_report(html_report.replace('<dt>rating</dt><dd>90.25</dd>', ''), 1)


@pytest.mark.parametrize('value', [None, True, -1, 1.1, float('nan'), float('inf')])
def test_rejects_invalid_rating(report, value):
    report['review']['rating'] = value
    with pytest.raises(AnalysisError): parse_json_report(report, 1)


def test_wrong_actor_and_zero_decisions(report, html_report):
    with pytest.raises(AnalysisError): parse_json_report(report, 2)
    with pytest.raises(AnalysisError): parse_html_report(html_report, 2)
    report['review']['total_reviewed'] = 0
    with pytest.raises(AnalysisError): parse_json_report(report, 1)


def test_log_identity_exact():
    assert log_matches('uuid', 'https://game.maj-soul.com/1/?paipu=uuid_a123')
    assert log_matches('uuid', 'uuid')
    assert not log_matches('uuid', 'uuid-other')
    assert not log_matches('uuid', None)
