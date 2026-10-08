import json
import os
from pathlib import Path

ROOT = Path(os.getenv("RATING_DATA_DIR", ".data")).resolve()


def initialize():
    for part in ("jobs", "cache", "reports"):
        (ROOT / part).mkdir(parents=True, exist_ok=True)


def read_job(job_id):
    path = ROOT / "jobs" / f"{job_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


def write_json(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def save_job(job):
    write_json(ROOT / "jobs" / f"{job['id']}.json", job)


def cache_path(game):
    import hashlib
    # 命名空间可在切换模型/服务版本后调整；评分保留 model_tag。
    key = f"{os.getenv('MORTAL_CACHE_TAG', 'official-web-v2')}:{os.getenv('MORTAL_MODEL_TAG', '4.1b')}:{game['game_id']}:{game['seat']}"
    return ROOT / "cache" / (hashlib.sha256(key.encode()).hexdigest() + ".json")
