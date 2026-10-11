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
