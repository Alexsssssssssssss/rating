import httpx
import pytest
import asyncio
from rating.source import PaipuSource, SourceError, AmbiguousPlayer, record_url


async def test_search_pagination_and_retry_share_rate_limit(monkeypatch):
    from rating import source as module
    clock = [0.0]
    times = []
    monkeypatch.setattr(module, '_NEXT_REQUEST_AT', 0.0)
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    async def sleep(delay):
        clock[0] += delay
    monkeypatch.setattr(module.asyncio, 'sleep', sleep)
    def handler(req):
        times.append(clock[0])
        if 'search_player' in req.url.path:
            return httpx.Response(200, json=[{'id':421,'nickname':'fioq421'}])
        if len(times) == 2:
            return httpx.Response(503)
        return httpx.Response(200, json=[raw_game('b',200)] if len(times)==3 else [raw_game('a',100)])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        s=PaipuSource(client)
        player=await s.find_player('fioq421')
        assert len(await s.recent(player,2,True)) == 2
    assert len(times) == 4
    assert all(b-a >= 1.099 for a,b in zip(times,times[1:]))
    # 避免虚拟时钟测试把后续真实时钟限速推向未知时间。
    module._NEXT_REQUEST_AT = 0.0


def raw_game(key, t, mode=12, masked=False):
    return {"uuid": key, "_id": key, "_masked": masked, "modeId": mode, "startTime": t, "endTime": t+3000, "players": [{"accountId": 1, "score": 27000}, {"accountId": 421, "score": 27000, "gradingScore": 30}, {"accountId": 3, "score": 26000}, {"accountId": 4, "score": 20000}]}


async def test_exact_name_and_paging_south_only():
    paths = []
    def handler(req):
        paths.append(req)
        if 'search_player' in req.url.path:
            return httpx.Response(200, json=[{"id": 7, "nickname": "fioq421_other"}, {"id": 421, "nickname": "fioq421"}])
        page = sum('player_records' in r.url.path for r in paths)
        return httpx.Response(200, json=[raw_game('b', 200)] if page == 1 else [raw_game('a', 100)])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = PaipuSource(client)
        player = await source.find_player('fioq421')
        games = await source.recent(player, 2, True)
    assert player['id'] == 421
    assert [g['uuid'] for g in games] == ['a', 'b']
    assert games[0]['seat'] == 1 and games[0]['rank'] == 2 and games[0]['pt'] == 30
    assert paths[1].url.params['mode'] == '9.12.16'
    assert '/199999/0' in paths[2].url.path


async def test_duplicate_names_require_choice():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[{'id': 1, 'nickname': '同名'}, {'id': 2, 'nickname': '同名'}]))) as client:
        source = PaipuSource(client)
        with pytest.raises(AmbiguousPlayer):
            await source.find_player('同名')
        assert (await source.find_player('同名', 2))['id'] == 2


@pytest.mark.parametrize('status', [403, 429])
async def test_denial_stops_requests(status):
    requests = []
    def handler(req):
        requests.append(req); return httpx.Response(status, text='denied')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceError, match=str(status)):
            await PaipuSource(client).find_player('name')
    assert len(requests) == 1


async def test_no_prefix_substitution():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[{'id': 1, 'nickname': 'name_extra'}]))) as client:
        with pytest.raises(SourceError, match='未找到'):
            await PaipuSource(client).find_player('name')


async def test_explicit_crawler_authorization_message():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(429, text='[x-cap-token-required] CAPTCHA required'))) as client:
        with pytest.raises(SourceError, match='AMAE_BEARER_TOKEN'):
            await PaipuSource(client).find_player('name')


async def test_credential_only_sent_to_fixed_domain(monkeypatch):
    original = httpx.AsyncClient
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(200,json=[{'id':421,'nickname':'fioq421'}])
    def client(**kwargs):
        assert kwargs['follow_redirects'] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setenv('AMAE_BEARER_TOKEN','TEST-CREDENTIAL-NOT-REAL')
    monkeypatch.setattr(httpx,'AsyncClient',client)
    await PaipuSource().find_player('fioq421')
    assert len(seen) == 1 and seen[0].url.host == '5-data.amae-koromo.com'
    assert seen[0].headers['authorization'] == 'Bearer TEST-CREDENTIAL-NOT-REAL'


def test_masked_link_uses_numeric_zone():
    assert '/view_game/1/12/hidden/' in record_url(raw_game('hidden', 10, masked=True), 421)


async def test_preserves_hidden_game_and_does_not_backfill():
    def handler(req):
        return httpx.Response(200, json=[raw_game('latest-hidden', 200, masked=True), raw_game('older', 100)])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        games = await PaipuSource(client).recent({'id': 421}, 1, True)
    assert len(games) == 1 and games[0]['masked'] and games[0]['game_id'] == 'latest-hidden'
