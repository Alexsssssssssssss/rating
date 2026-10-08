"""牌谱屋公开前端使用的 pl4 API；不绕过访问验证。"""
import asyncio
import os
import time
import threading
from urllib.parse import quote

import httpx

API_BASE = "https://5-data.amae-koromo.com/api/v2/pl4"
MODES = {8: "金东", 9: "金南", 11: "玉东", 12: "玉南", 15: "王座东", 16: "王座南"}

# 站方规定最多 1 QPS。搜索、分页及重试共享进程级限速，留 0.1 秒余量。
_RATE_LOCK = threading.Lock()
_NEXT_REQUEST_AT = 0.0


async def wait_request_slot():
    global _NEXT_REQUEST_AT
    while True:
        with _RATE_LOCK:
            now = time.monotonic()
            delay = _NEXT_REQUEST_AT - now
            if delay <= 0:
                _NEXT_REQUEST_AT = now + 1.1
                return
        await asyncio.sleep(delay)


class SourceError(Exception):
    pass


class AmbiguousPlayer(SourceError):
    def __init__(self, candidates):
        self.candidates = candidates
        super().__init__("找到多个同名用户，请按账号 ID 选择。")


def record_url(record, account_id):
    encoded = 1358437 + ((7 * int(account_id) + 1117113) ^ 86216345)
    if record.get("_masked") or not record.get("uuid"):
        return f"{API_BASE}/view_game/1/{record['modeId']}/{record['_id']}/{encoded}"
    return f"https://game.maj-soul.com/1/?paipu={record['uuid']}_a{encoded}"


class PaipuSource:
    def __init__(self, client=None, base=API_BASE):
        self.client = client
        self.base = base.rstrip("/")

    async def _get(self, path, params=None):
        async def request(client):
            for attempt in range(3):
                await wait_request_slot()
                try:
                    r = await client.get(f"{self.base}/{path}", params=params)
                except httpx.HTTPError as exc:
                    raise SourceError("牌谱屋连接失败。请检查网络与域名放行设置。") from exc
                if r.status_code == 429 and "x-cap-token-required" in r.text:
                    raise SourceError("牌谱屋要求爬取访问 key（x-cap-token-required）。请向站方申请授权，并通过环境设置配置 AMAE_BEARER_TOKEN；程序不会绕过验证码。")
                if r.status_code in (401, 403, 429):
                    raise SourceError(f"牌谱屋返回 HTTP {r.status_code}：访问验证、网络策略或限流阻止了查询，请稍后重试或在原站完成验证。")
                if r.status_code >= 500 and attempt < 2:
                    await asyncio.sleep(1 + attempt)
                    continue
                if r.is_error:
                    raise SourceError(f"牌谱屋返回 HTTP {r.status_code}。")
                try:
                    data = r.json()
                except ValueError as exc:
                    raise SourceError("牌谱屋返回了非 JSON 内容，可能需要访问验证。") from exc
                if not isinstance(data, list):
                    raise SourceError("牌谱屋接口格式发生变化，预期为数组。")
                return data
        if self.client:
            return await request(self.client)
        headers = {"User-Agent": "RatingNotebook/0.1 (personal mahjong review)"}
        if os.getenv("AMAE_BEARER_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['AMAE_BEARER_TOKEN']}"
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, headers=headers) as client:
            return await request(client)

    async def find_player(self, name, player_id=None):
        candidates = await self._get(f"search_player/{quote(name, safe='')}", {"limit": 100, "tag": "all"})
        exact = [p for p in candidates if p.get("nickname") == name]
        if player_id is not None:
            exact = [p for p in exact if str(p.get("id")) == str(player_id)]
        if not exact:
            raise SourceError("未找到这个用户名的四人麻将记录。牌谱屋主要收录金、玉、王座段位场；请检查名字是否完全一致。")
        if len(exact) > 1:
            raise AmbiguousPlayer(exact)
        return exact[0]

    async def recent(self, player, count, hanchan_only=False):
        # 查询最近四人麻将，不能为了补足分析成功数而跳过最新场次。
        modes = [9, 12, 16] if hanchan_only else list(MODES)
        cursor = int(time.time() * 1000) + 1000
        found, seen = [], set()
        for _ in range(20):
            batch = await self._get(f"player_records/{player['id']}/{cursor}/0", {"limit": min(100, count - len(found)), "mode": ".".join(map(str, modes)), "descending": "true"})
            if not batch:
                break
            try:
                ordered = sorted(batch, key=lambda g: g["startTime"], reverse=True)
                next_cursor = int(ordered[-1]["startTime"] * 1000) - 1
                for g in ordered:
                    if g.get("modeId") not in modes or len(g.get("players", [])) != 4:
                        continue
                    key = g.get("uuid") or g.get("_id")
                    if not key or key in seen:
                        continue
                    people = g["players"]
                    seats = [i for i, p in enumerate(people) if str(p["accountId"]) == str(player["id"])]
                    if len(seats) != 1:
                        raise SourceError("牌谱玩家信息无法匹配账号 ID。")
                    seat = seats[0]
                    ranked = sorted(range(4), key=lambda i: (-people[i]["score"], i))
                    found.append({"game_id": str(key), "uuid": g.get("uuid"), "masked": bool(g.get("_masked") or not g.get("uuid")), "start_time": g["startTime"], "end_time": g.get("endTime"), "mode_id": g["modeId"], "mode": MODES[g["modeId"]], "seat": seat, "rank": ranked.index(seat) + 1, "pt": people[seat].get("gradingScore"), "score": people[seat].get("score"), "record_url": record_url(g, player["id"]), "status": "pending", "analysis": None, "error": None})
                    seen.add(key)
                    if len(found) == count:
                        return sorted(found, key=lambda g: (g["start_time"], g["game_id"]))
                if next_cursor >= cursor:
                    raise SourceError("牌谱屋分页游标未前进，已停止以避免重复请求。")
                cursor = next_cursor
            except (KeyError, TypeError, ValueError) as exc:
                raise SourceError("牌谱屋比赛记录格式发生变化。") from exc
        return sorted(found, key=lambda g: (g["start_time"], g["game_id"]))
