import pytest

from rating.mortal import MortalBrowser
from rating.reports import AnalysisError


def test_missing_id_saves_report_but_rejects_score(tmp_path, monkeypatch):
    monkeypatch.setenv('RATING_DATA_DIR', str(tmp_path))
    with pytest.raises(AnalysisError, match='缺少牌谱 ID') as exc:
        MortalBrowser.validate_report_identity({'log_id': None}, '<html>真实报告</html>', {'uuid': 'expected'})
    files = list((tmp_path / 'reports').glob('unverified-*.html'))
    assert len(files) == 1
    assert files[0].read_text(encoding='utf-8') == '<html>真实报告</html>'
    assert str(files[0]) in str(exc.value)


def test_wrong_id_is_distinguished_from_missing(tmp_path, monkeypatch):
    monkeypatch.setenv('RATING_DATA_DIR', str(tmp_path))
    with pytest.raises(AnalysisError, match='牌谱 ID 与当前比赛不同'):
        MortalBrowser.validate_report_identity({'log_id': 'other'}, 'report', {'uuid': 'expected'})


def test_matching_id_does_not_create_diagnostic_file(tmp_path, monkeypatch):
    monkeypatch.setenv('RATING_DATA_DIR', str(tmp_path))
    MortalBrowser.validate_report_identity({'log_id': 'expected_a123'}, 'report', {'uuid': 'expected'})
    assert not (tmp_path / 'reports').exists()
