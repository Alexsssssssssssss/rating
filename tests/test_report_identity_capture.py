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


def test_explicit_wrong_id_rejected_even_with_submission(tmp_path, monkeypatch):
    monkeypatch.setenv('RATING_DATA_DIR', str(tmp_path))
    with pytest.raises(AnalysisError, match='牌谱 ID 与当前比赛不同'):
        MortalBrowser.validate_report_identity({'log_id': 'other'}, 'report',
            {'uuid': 'expected', 'record_url': 'https://example.test'}, True)


@pytest.mark.parametrize('encoding', ['application/x-www-form-urlencoded', 'multipart/form-data'])
@pytest.mark.parametrize('target', ['_self', '_blank'])
async def test_missing_id_bound_only_to_exact_post(game, html_report, monkeypatch, encoding, target):
    from test_browser import OFFICIAL_FORM
    monkeypatch.setenv('MORTAL_INTERVAL', '0')
    monkeypatch.setenv('MORTAL_TIMEOUT', '10')
    report = html_report.replace('<dt>牌谱 ID</dt><dd>test-game-001</dd>', '')
    form = OFFICIAL_FORM.replace(' disabled', '').replace('method="post"', f'method="post" enctype="{encoding}" target="{target}"')
    async with MortalBrowser() as browser:
        await browser.context.route('https://mjai.ekyu.moe/zh-cn.html*',
            lambda route: route.fulfill(content_type='text/html; charset=utf-8', body=form))
        async def handle(route):
            assert route.request.method == 'POST'
            await route.fulfill(content_type='text/html; charset=utf-8', body=report)
        await browser.context.route('https://mjai.ekyu.moe/review', handle)
        await browser.context.route('https://mjai.ekyu.moe/report/test',
            lambda route: route.fulfill(content_type='text/html; charset=utf-8', body=report))
        result, text = await browser.analyze(game)
        assert result['rating'] == 90.25
        assert result['identity_method'] == 'official-post-response'
        assert result['log_id'] == game['uuid']
        assert '牌谱 ID' not in text


@pytest.mark.parametrize('change', ['url', 'seat', 'engine', 'host', 'duplicate', 'expected_uuid'])
def test_unrelated_submission_rejected(game, change):
    from types import SimpleNamespace
    from urllib.parse import urlencode
    fields = [('log-url', game['record_url']), ('player-id', str(game['seat'])), ('engine', 'mortal')]
    if change == 'url': fields[0] = ('log-url', 'https://game.maj-soul.com/1/?paipu=other')
    if change == 'seat': fields[1] = ('player-id', '0')
    if change == 'engine': fields[2] = ('engine', 'akochan')
    if change == 'duplicate': fields.append(('log-url', game['record_url']))
    request = SimpleNamespace(url='https://mjai.ekyu.moe/review', method='POST', redirected_from=None,
        headers={'content-type': 'application/x-www-form-urlencoded'}, post_data_buffer=urlencode(fields).encode())
    if change == 'host': request.url = 'https://example.test/review'
    if change == 'expected_uuid': game = dict(game, uuid='other')
    assert not MortalBrowser.submission_matches(request, game)


def test_redirect_chain_preserves_post_identity(game):
    from types import SimpleNamespace
    from urllib.parse import urlencode
    initial = SimpleNamespace(url='https://mjai.ekyu.moe/review', method='POST', redirected_from=None,
        headers={'content-type': 'application/x-www-form-urlencoded'}, post_data_buffer=urlencode({
            'log-url': game['record_url'], 'player-id': game['seat'], 'engine': 'mortal'}).encode())
    final = SimpleNamespace(url='https://mjai.ekyu.moe/report/test', method='GET', redirected_from=initial)
    assert MortalBrowser.submission_matches(final, game)
    final.url = 'https://example.test/report/test'
    assert not MortalBrowser.submission_matches(final, game)


@pytest.mark.parametrize('kind', ['header', 'meta'])
async def test_report_navigation_from_server_refresh(game, html_report, monkeypatch, kind):
    from test_browser import OFFICIAL_FORM
    monkeypatch.setenv('MORTAL_INTERVAL', '0')
    monkeypatch.setenv('MORTAL_TIMEOUT', '6')
    report = html_report.replace('<dt>牌谱 ID</dt><dd>test-game-001</dd>', '')
    form = OFFICIAL_FORM.replace(' disabled', '')
    async with MortalBrowser() as browser:
        await browser.context.route('https://mjai.ekyu.moe/zh-cn.html*',
            lambda route: route.fulfill(content_type='text/html; charset=utf-8', body=form))
        async def submitted(route):
            if kind == 'header':
                await route.fulfill(content_type='text/html', headers={'Refresh': '0; url=/report/refresh.html'}, body='Waiting')
            else:
                await route.fulfill(content_type='text/html', body='<meta http-equiv="refresh" content="0; url=/report/refresh.html">')
        await browser.context.route('https://mjai.ekyu.moe/review', submitted)
        await browser.context.route('https://mjai.ekyu.moe/report/refresh.html',
            lambda route: route.fulfill(content_type='text/html; charset=utf-8', body=report))
        result, raw = await browser.analyze(game)
        assert result['identity_method'] == 'official-post-response'
        assert result['rating'] == 90.25


def test_only_explicit_same_site_server_navigation_is_trusted():
    from types import SimpleNamespace
    response = SimpleNamespace(url='https://mjai.ekyu.moe/review', headers={})
    targets = MortalBrowser.server_redirect_targets(response, '''
        <a href="/report/example.html">Example</a>
        <meta http-equiv="refresh" content="0; url=https://example.test/report/foreign.html">
        <script>window.location.replace('/report/actual.html');</script>
    ''')
    assert targets == {'https://mjai.ekyu.moe/report/actual.html'}


def test_refresh_header_with_quoted_url():
    from types import SimpleNamespace
    response = SimpleNamespace(url='https://mjai.ekyu.moe/review',
        headers={'refresh': '0; URL="/report/actual.html"'})
    assert MortalBrowser.server_redirect_targets(response, '') == {'https://mjai.ekyu.moe/report/actual.html'}


async def test_slow_post_uses_analysis_deadline_not_click_timeout(game, html_report, monkeypatch):
    import asyncio
    from test_browser import OFFICIAL_FORM
    monkeypatch.setenv('MORTAL_INTERVAL', '0')
    monkeypatch.setenv('MORTAL_TIMEOUT', '8')
    original = MortalBrowser.prepare_form
    async def short_click_timeout(self, page, game):
        submit = await original(self, page, game)
        page.set_default_timeout(500)
        return submit
    monkeypatch.setattr(MortalBrowser, 'prepare_form', short_click_timeout)
    async with MortalBrowser() as browser:
        await browser.context.route('https://mjai.ekyu.moe/zh-cn.html*',
            lambda route: route.fulfill(content_type='text/html', body=OFFICIAL_FORM.replace(' disabled', '')))
        async def slow_report(route):
            await asyncio.sleep(1.5)
            await route.fulfill(content_type='text/html', body=html_report)
        await browser.context.route('https://mjai.ekyu.moe/review', slow_report)
        result, _ = await browser.analyze(game)
        assert result['rating'] == 90.25


async def test_browser_failure_saves_stage_without_secret_call_logs(game, monkeypatch, tmp_path):
    import json
    from playwright.async_api import Error
    from test_browser import OFFICIAL_FORM
    monkeypatch.setenv('MORTAL_INTERVAL', '0')
    monkeypatch.setenv('RATING_DATA_DIR', str(tmp_path))
    async def broken_form(self, page, game):
        raise Error('Locator.fill: Timeout; URL ?token=TEST-SECRET-DO-NOT-SAVE')
    monkeypatch.setattr(MortalBrowser, 'prepare_form', broken_form)
    async with MortalBrowser() as browser:
        await browser.context.route('https://mjai.ekyu.moe/zh-cn.html*',
            lambda route: route.fulfill(content_type='text/html', body=OFFICIAL_FORM))
        with pytest.raises(AnalysisError, match='填写分析表单.*等待超时') as exc:
            await browser.analyze(game)
    paths = list((tmp_path / 'reports').glob('navigation-*.json'))
    assert len(paths) == 1
    raw = paths[0].read_text(encoding='utf-8')
    data = json.loads(raw)
    assert data['stage'] == '填写分析表单'
    assert data['responses'][0]['path'] == '/zh-cn.html'
    assert 'TEST-SECRET' not in raw + str(exc.value)
    assert str(paths[0]) in str(exc.value)
