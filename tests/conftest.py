import copy
import pytest


@pytest.fixture
def report():
    # 明确的测试数据，不用于用户查询，也不是 fioq421 的真实结果。
    return {"engine": "Mortal", "player_id": 1, "log_id": "test-game-001", "review": {"rating": .9025, "total_reviewed": 3, "total_matches": 2, "model_tag": "TEST MODEL", "kyokus": [{"entries": [{"actual_index": 0, "details": [{"prob": .9}]}, {"actual_index": 1, "details": [{"prob": .99}, {"prob": .01}]}, {"actual_index": 0, "details": [{"prob": .8}]}]}]}}


@pytest.fixture
def html_report():
    return '''<!doctype html><html><head><meta charset="utf-8"></head><body><details><summary>元数据</summary><dl>
    <dt>AI 引擎</dt><dd>Mortal</dd><dt>model tag</dt><dd>TEST MODEL</dd>
    <dt>玩家 ID</dt><dd>1</dd><dt>牌谱 ID</dt><dd>test-game-001</dd>
    <dt>rating</dt><dd>90.25</dd><dt>AI 一致率</dt><dd>2/3 = 66.667%</dd>
    </dl></details></body></html>'''


@pytest.fixture
def game():
    return {"game_id": "test-game-001", "uuid": "test-game-001", "masked": False, "start_time": 1700000000, "mode_id": 12, "mode": "玉南", "seat": 1, "rank": 2, "pt": 30, "score": 27000, "record_url": "https://game.maj-soul.com/1/?paipu=test-game-001_a123", "status": "pending", "analysis": None, "error": None}


@pytest.fixture
def job(game):
    return {"id": "a" * 32, "name": "测试用户", "count": 1, "hanchan_only": True, "refresh": False, "player_id": None, "player": {"id": 421, "nickname": "测试用户"}, "created_at": "2026-01-01", "updated_at": "2026-01-01", "status": "failed", "games": [copy.deepcopy(game)], "error": None, "warning": None}
