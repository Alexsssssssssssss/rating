import pytest
from rating.mortal import MortalBrowser
from rating.reports import AnalysisError


OFFICIAL_FORM = '''<html><head><meta charset="utf-8"></head><body>
<form name="reviewForm" action="/review" method="post">
<input name="log-url" type="url">
<select name="player-id"><option value="0">0</option><option value="1">1</option></select>
<select name="engine"><option value="mortal">Mortal</option><option value="akochan">akochan</option></select>
<select name="ui"><option value="killerducky">KillerDucky</option><option value="classic">Classic</option></select>
<select name="lang"><option value="zh-CN">中文</option></select>
<select name="mortal-model-tag"><option value="4.1b">4.1b</option><option value="4.1c">4.1c</option></select>
<details><summary>高级选项</summary><label><input name="show-rating" type="checkbox" value="1">显示 rating</label></details>
<button name="submitBtn" type="submit" disabled>提交</button></form>
<form name="botForm"><button type="submit">提交</button></form>
</body></html>'''


async def test_browser_form_submission_and_report_identity(game, html_report, monkeypatch):
    monkeypatch.setenv('MORTAL_INTERVAL','0')
    monkeypatch.setenv('MORTAL_TIMEOUT','10')
    async with MortalBrowser() as browser:
        async def handle(route):
            await route.fulfill(content_type='text/html; charset=utf-8', body='''<html><body><form action="/report"><label>牌谱 URL<input type="url" name="url"></label><button type="submit">开始分析</button></form></body></html>''')
        await browser.context.route('https://mjai.ekyu.moe/zh-cn.html*',handle)
        await browser.context.route('https://mjai.ekyu.moe/report*',lambda route: route.fulfill(content_type='text/html',body=html_report))
        result, html = await browser.analyze(game)
        assert result['rating'] == 90.25 and result['player_id'] == game['seat']
        assert result['log_id'] == game['uuid']
        assert not browser.context.pages


async def test_real_form_shape_selects_classic_rating_and_target(game):
    async with MortalBrowser() as browser:
        p = await browser.context.new_page()
        await p.set_content(OFFICIAL_FORM)
        button = await browser.prepare_form(p, game)
        assert await button.count() == 1 and await button.is_disabled()
        form = p.locator('form[name="reviewForm"]')
        assert await form.locator('select[name="player-id"]').input_value() == '1'
        assert await form.locator('select[name="ui"]').input_value() == 'classic'
        assert await form.locator('select[name="engine"]').input_value() == 'mortal'
        assert await form.locator('select[name="mortal-model-tag"]').input_value() == '4.1b'
        assert await form.locator('input[name="show-rating"]').is_checked()


async def test_challenge_blocks_submission_without_post(game, monkeypatch):
    monkeypatch.setenv('MORTAL_INTERVAL','0')
    monkeypatch.setenv('MORTAL_VERIFICATION_TIMEOUT','0')
    async with MortalBrowser() as browser:
        requests = []
        async def handle(route):
            requests.append(route.request.method)
            await route.fulfill(content_type='text/html; charset=utf-8',body=OFFICIAL_FORM)
        await browser.context.route('https://mjai.ekyu.moe/**',handle)
        with pytest.raises(AnalysisError,match='Cloudflare'):
            await browser.analyze(game)
        assert requests == ['GET']


def test_cache_namespace_includes_model(game, monkeypatch):
    from rating.store import cache_path
    monkeypatch.setenv('MORTAL_MODEL_TAG','4.1b'); a=cache_path(game)
    monkeypatch.setenv('MORTAL_MODEL_TAG','4.1c'); b=cache_path(game)
    assert a != b
