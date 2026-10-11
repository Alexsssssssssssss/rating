import json

import httpx
import pytest

from rating import captcha
from rating.reports import AnalysisError


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('TWOCAPTCHA_API_KEY', 'test-secret-not-real')
    async def pause(seconds, cancelled):
        if cancelled():
            raise AnalysisError('任务已取消。')
    monkeypatch.setattr(captcha, '_pause', pause)


def test_opt_in_and_missing_key(monkeypatch):
    monkeypatch.delenv('MORTAL_CAPTCHA_PROVIDER', raising=False)
    assert not captcha.enabled()
    monkeypatch.setenv('MORTAL_CAPTCHA_PROVIDER', '2captcha')
    monkeypatch.delenv('TWOCAPTCHA_API_KEY', raising=False)
    with pytest.raises(AnalysisError, match='TWOCAPTCHA_API_KEY'):
        captcha.enabled()


async def test_create_once_then_poll(configured):
    seen = []
    def handle(request):
        assert request.url.host == 'api.2captcha.com'
        body = json.loads(request.content)
        seen.append((request.url.path, body))
        if request.url.path == '/createTask':
            assert body['task'] == {'type': 'TurnstileTaskProxyless',
                'websiteURL': 'https://mjai.ekyu.moe/zh-cn.html',
                'websiteKey': 'public-key', 'action': 'review', 'data': 'data'}
            return httpx.Response(200, json={'errorId': 0, 'taskId': 7})
        assert body['taskId'] == 7
        if len(seen) == 2:
            return httpx.Response(200, json={'errorId': 0, 'status': 'processing'})
        return httpx.Response(200, json={'errorId': 0, 'status': 'ready',
                                        'solution': {'token': 'test-result'}})
    token = await captcha.solve({'url': 'https://mjai.ekyu.moe/zh-cn.html',
        'sitekey': 'public-key', 'action': 'review', 'data': 'data'},
        transport=httpx.MockTransport(handle))
    assert token == 'test-result'
    assert [path for path, _ in seen].count('/createTask') == 1


async def test_provider_error_does_not_disclose_secret(configured):
    def handle(request):
        return httpx.Response(200, json={'errorId': 1,
            'errorCode': 'ERROR_ZERO_BALANCE', 'errorDescription': 'test-secret-not-real'})
    with pytest.raises(AnalysisError) as exc:
        await captcha.solve({'url': 'https://mjai.ekyu.moe', 'sitekey': 'public'},
                            transport=httpx.MockTransport(handle))
    assert 'ERROR_ZERO_BALANCE' in str(exc.value)
    assert 'test-secret-not-real' not in str(exc.value)


async def test_network_error_never_recreates_paid_task(configured):
    calls = []
    def handle(request):
        calls.append(request.url.path)
        raise httpx.ReadTimeout('test-secret-not-real', request=request)
    with pytest.raises(AnalysisError) as exc:
        await captcha.solve({'url': 'https://mjai.ekyu.moe', 'sitekey': 'public'},
                            transport=httpx.MockTransport(handle))
    assert calls == ['/createTask']
    assert 'test-secret-not-real' not in str(exc.value)


async def test_cancel_before_creating_task(configured):
    def handle(request):
        pytest.fail('No paid request should be made')
    with pytest.raises(AnalysisError, match='取消'):
        await captcha.solve({'url': 'https://mjai.ekyu.moe', 'sitekey': 'public'},
                            cancelled=lambda: True, transport=httpx.MockTransport(handle))


async def test_ready_without_token_is_rejected(configured):
    def handle(request):
        if request.url.path == '/createTask':
            return httpx.Response(200, json={'errorId': 0, 'taskId': 7})
        return httpx.Response(200, json={'errorId': 0, 'status': 'ready', 'solution': {}})
    with pytest.raises(AnalysisError, match='有效验证结果'):
        await captcha.solve({'url': 'https://mjai.ekyu.moe', 'sitekey': 'public'},
                            transport=httpx.MockTransport(handle))
