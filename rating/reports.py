"""严格解析 mjai-reviewer 原始 JSON / HTML，不猜测不存在的 Rating。"""
import math
import re

from bs4 import BeautifulSoup
from urllib.parse import parse_qs, urlsplit


class AnalysisError(Exception):
    pass


def log_matches(expected_uuid, log_id):
    if not expected_uuid or not isinstance(log_id, str):
        return False
    candidate = log_id.strip()
    if candidate.startswith("https://"):
        candidate = parse_qs(urlsplit(candidate).query).get("paipu", [""])[0]
    candidate = re.sub(r"_a\d+$", "", candidate)
    return candidate == expected_uuid


def percent(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
        raise AnalysisError("分析结果数值必须在 0–100 之间。")
    return float(value)


def parse_json_report(data, expected_seat=None):
    if str(data.get("engine", "")).lower() != "mortal":
        raise AnalysisError("此报告不是 Mortal 分析结果。")
    if expected_seat is not None and data.get("player_id") != expected_seat:
        raise AnalysisError("报告分析的玩家座位与目标用户不符。")
    r = data.get("review", {})
    total, matches = r.get("total_reviewed"), r.get("total_matches")
    if type(total) is not int or type(matches) is not int or total <= 0 or not 0 <= matches <= total:
        raise AnalysisError("报告中的决策总数或一致决策数无效。")
    raw_rating = r.get("rating")
    if isinstance(raw_rating, bool) or not isinstance(raw_rating, (int, float)) or not math.isfinite(raw_rating) or not 0 <= raw_rating <= 1:
        raise AnalysisError("mjai-reviewer JSON 中的 rating 必须为 0–1。")
    rating = percent(raw_rating * 100)
    entries = [e for k in r.get("kyokus", []) for e in k.get("entries", [])]
    # “恶手”采用实际动作的 softmax 概率，而非 Q 损失或一致率补集。
    probabilities = []
    for entry in entries:
        details, idx = entry.get("details", []), entry.get("actual_index")
        if type(idx) is int and 0 <= idx < len(details):
            p = details[idx].get("prob")
            if isinstance(p, (int, float)) and math.isfinite(p) and 0 <= p <= 1:
                probabilities.append(p)
    bad10 = sum(p < .10 for p in probabilities) / total * 100 if len(probabilities) == total else None
    bad5 = sum(p < .05 for p in probabilities) / total * 100 if len(probabilities) == total else None
    return {"rating": rating, "agreement": matches / total * 100, "total_reviewed": total, "total_matches": matches, "bad10": bad10, "bad5": bad5, "model_tag": str(r.get("model_tag", "未知")), "player_id": data.get("player_id"), "log_id": data.get("log_id"), "origin": "mjai-reviewer JSON"}


def parse_html_report(html, expected_seat=None):
    soup = BeautifulSoup(html, "html.parser")
    fields = {}
    for dt in soup.select("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            fields[dt.get_text(" ", strip=True).casefold()] = dd.get_text(" ", strip=True)
    engine = next((v for k, v in fields.items() if k in ("ai 引擎", "ai engine", "engine", "aiエンジン")), "")
    if engine.casefold() != "mortal":
        raise AnalysisError("尚未取得 Mortal 报告，或网页格式已变化。")
    seat_text = next((v for k, v in fields.items() if k in ("玩家 id", "player id", "プレイヤー id")), "")
    if not seat_text.isdigit() or (expected_seat is not None and int(seat_text) != expected_seat):
        raise AnalysisError("Mortal 报告座位无法验证或与目标用户不符。")
    if "rating" not in fields:
        raise AnalysisError("Mortal 报告未公开 Rating。请启用显示 Rating，或使用本地 mjai-reviewer JSON；不能用一致率代替 Rating。")
    try:
        rating = percent(float(fields["rating"].replace("%", "")))
    except ValueError as exc:
        raise AnalysisError("无法解析报告 Rating。") from exc
    match_text = next((v for k, v in fields.items() if k in ("ai 一致率", "ai match rate", "match rate", "ai一致率")), "")
    match = re.search(r"(\d+)\s*/\s*(\d+)", match_text)
    if not match or int(match[2]) <= 0 or int(match[1]) > int(match[2]):
        raise AnalysisError("无法解析报告一致决策数。")
    return {"rating": rating, "agreement": int(match[1]) / int(match[2]) * 100, "total_matches": int(match[1]), "total_reviewed": int(match[2]), "bad10": None, "bad5": None, "model_tag": fields.get("model tag", "未知"), "player_id": int(seat_text), "log_id": next((v for k, v in fields.items() if k in ("牌谱 id", "log id")), None), "origin": "Mortal 网站 HTML"}
