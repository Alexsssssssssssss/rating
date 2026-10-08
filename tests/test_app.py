import copy
import json
import asyncio
import pytest
from fastapi.testclient import TestClient
from rating import app as module, store
from rating.charts import render, summary
from rating.reports import parse_json_report


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    # 每个 TestClient 生命周期使用自己的事件循环队列。
    monkeypatch.setattr(module, 'QUEUE', asyncio.Queue())
    with TestClient(module.app) as c:
        yield c


def test_health_input_validation_and_job_scope(client, monkeypatch):
    async def no_network(job_id):
        pass
    monkeypatch.setattr(module, 'run_job', no_network)
    assert client.get('/api/health').json()['status'] == 'ok'
    for query in ['name', 'name 0', 'name 201', 'name abc', 'name ²']:
        assert client.post('/api/jobs', json={'query': query}).status_code == 422
    result = client.post('/api/jobs', json={'query': 'fioq421 20', 'hanchan_only': False})
    assert result.status_code == 202 and result.json()['hanchan_only'] is True


def test_import_validates_game_and_seat_then_exports(client, job, report):
    store.save_job(job)
    url = f"/api/jobs/{job['id']}/games/0/import"
    wrong = copy.deepcopy(report); wrong['log_id'] = 'another-game'
    assert client.post(url, content=json.dumps(wrong)).status_code == 422
    wrong['log_id'] = report['log_id']; wrong['player_id'] = 2
    assert client.post(url, content=json.dumps(wrong)).status_code == 422
    assert client.post(url, content=json.dumps(report)).status_code == 200
    data = client.get(f"/api/jobs/{job['id']}").json()
    assert data['status'] == 'done' and data['summary']['analyzed'] == 1
    assert data['games'][0]['analysis']['rating'] == 90.25
    image = client.get(f"/api/jobs/{job['id']}/chart.png")
    assert image.status_code == 200 and image.content.startswith(b'\x89PNG')
    assert '<svg' in client.get(f"/api/jobs/{job['id']}/chart.svg").text
    assert '90.25' in client.get(f"/api/jobs/{job['id']}/export.csv").text
    assert client.get(f"/api/jobs/{job['id']}/export.json").json()['summary']['rating_mean'] == 90.25


def test_cannot_import_while_active(client, job, report):
    job['status'] = 'analyzing'; store.save_job(job)
    assert client.post(f"/api/jobs/{job['id']}/games/0/import", content=json.dumps(report)).status_code == 409


def test_failed_points_remain_missing_and_averages_differ(job, report):
    job['games'][0].update(status='done', analysis=parse_json_report(report, 1))
    missing = copy.deepcopy(job['games'][0]); missing.update(status='failed', analysis=None, game_id='missing', pt=None)
    other = copy.deepcopy(job['games'][0]); other['analysis'].update(total_reviewed=1,total_matches=1,agreement=100,rating=100)
    job['games'] += [missing, other]
    s = summary(job)
    assert s['analyzed'] == 2 and s['failed'] == 1 and s['pt_missing'] == 1
    assert s['agreement_mean'] == pytest.approx(250/3)
    assert s['agreement_weighted'] == 75
    assert render(job).startswith(b'\x89PNG')


async def test_worker_pipeline_then_cache_reuse(tmp_path, monkeypatch, game, report):
    monkeypatch.setattr(store,'ROOT',tmp_path); store.initialize()
    calls = []
    class Source:
        async def find_player(self,*a): return {'id':421,'nickname':'测试用户'}
        async def recent(self,*a): return [copy.deepcopy(game)]
    class Mortal:
        async def __aenter__(self): return self
        async def __aexit__(self,*a): pass
        async def analyze(self,g,cancelled):
            calls.append(g['game_id']); return parse_json_report(report,1),'<html>TEST REPORT</html>'
    monkeypatch.setattr(module,'PaipuSource',Source); monkeypatch.setattr(module,'MortalBrowser',Mortal)
    for jid in ['a'*32,'b'*32]:
        j={'id':jid,'name':'测试用户','count':1,'hanchan_only':True,'refresh':False,'player_id':None,'player':None,'games':[],'status':'queued','error':None,'warning':None}
        store.save_job(j); await module.run_job(jid)
        assert store.read_job(jid)['status'] == 'done'
    assert calls == [game['game_id']]
    assert store.read_job('b'*32)['games'][0]['cached']


def test_data_paths_cannot_traverse(client):
    assert client.get('/api/jobs/not-a-valid-id').status_code == 404
