import io
import os
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from .store import ROOT
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "matplotlib"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

LOCK = threading.Lock()
FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
if FONT.exists():
    font_manager.fontManager.addfont(str(FONT))
    matplotlib.rcParams["font.family"] = font_manager.FontProperties(fname=str(FONT)).get_name()
matplotlib.rcParams["axes.unicode_minus"] = False
TZ = ZoneInfo("Asia/Shanghai")


def summary(job):
    games = job.get("games", [])
    valid = [g["analysis"] for g in games if g.get("analysis")]
    total = sum(a["total_reviewed"] for a in valid)
    return {"selected": len(games), "analyzed": len(valid), "failed": sum(g["status"] == "failed" for g in games), "rating_mean": sum(a["rating"] for a in valid) / len(valid) if valid else None, "agreement_mean": sum(a["agreement"] for a in valid) / len(valid) if valid else None, "agreement_weighted": sum(a["total_matches"] for a in valid) / total * 100 if total else None, "pt_total": sum(g["pt"] for g in games if g.get("pt") is not None) if any(g.get("pt") is not None for g in games) else None, "pt_missing": sum(g.get("pt") is None for g in games), "models": sorted(set(a["model_tag"] for a in valid))}


def render(job, fmt="png"):
    with LOCK:
        return _render(job, fmt)


def _render(job, fmt):
    games = job.get("games", [])
    if not games:
        raise ValueError("还没有比赛记录。")
    n, stats = len(games), summary(job)
    x = np.arange(1, n + 1)
    vals = lambda key: np.array([g["analysis"].get(key) if g.get("analysis") and g["analysis"].get(key) is not None else np.nan for g in games], dtype=float)
    rating, agreement = vals("rating"), vals("agreement")
    fig, axes = plt.subplots(3, 1, figsize=(max(12, min(24, n * .19)), 8), gridspec_kw={"height_ratios": [3.2, 1, 1.25]}, sharex=True)
    try:
        fig.subplots_adjust(top=.77, bottom=.11, hspace=.34, left=.07, right=.92)
        name = job.get("player", {}).get("nickname", job["name"])
        start = datetime.fromtimestamp(games[0]["start_time"], TZ).strftime("%Y-%m-%d")
        end = datetime.fromtimestamp(games[-1]["start_time"], TZ).strftime("%Y-%m-%d")
        model = "、".join(stats["models"]) or "尚无分析结果"
        fig.suptitle(f"{name} · 最近 {n}/{job['count']} 场四人南\nRating & 一致率 · {model}\n{start} — {end}（UTC+8） · 成功 {stats['analyzed']} / {n}，缺失结果留空", y=.96, fontsize=12)
        for ax in axes:
            ax.grid(alpha=.15)
            ax.set_xlim(.5, n + .5)
            for spine in ax.spines.values():
                spine.set_color("#cccccc")
            ax.tick_params(labelsize=8)
        a = axes[0]
        b = a.twinx()
        a.plot(x, rating, "o-", color="#c95765", lw=1.3, ms=3, label="Rating")
        b.plot(x, agreement, "x--", color="#587caf", lw=1, ms=4, label="一致率")
        a.set_ylabel("Rating", color="#c95765")
        b.set_ylabel("一致率 (%)", color="#587caf")
        for ax, values in [(a, rating), (b, agreement)]:
            valid = values[np.isfinite(values)]
            if valid.size:
                ax.set_ylim(max(0, valid.min() - 8), min(100, valid.max() + 8))
                ax.axhline(valid.mean(), color="#c95765" if ax is a else "#587caf", linestyle=":", alpha=.75, label=f"均值 {valid.mean():.1f}")
        handles, labels = a.get_legend_handles_labels()
        h2, l2 = b.get_legend_handles_labels()
        a.legend(handles + h2, labels + l2, fontsize=8, ncol=4, loc="upper left")
        if n <= 100:
            for idx, (r, agree) in enumerate(zip(rating, agreement)):
                if np.isfinite(r):
                    a.annotate(f"{r:.1f}", (x[idx], r), xytext=(0, 7), textcoords="offset points", fontsize=6, ha="center", color="#a34552")
                if np.isfinite(agree):
                    b.annotate(f"{agree:.1f}%", (x[idx], agree), xytext=(0, -10), textcoords="offset points", fontsize=5, ha="center", color="#587caf")
        ticks = np.unique(np.linspace(1, n, min(n, 16), dtype=int))
        top = a.secondary_xaxis("top")
        top.set_xticks(ticks, [datetime.fromtimestamp(games[t-1]["start_time"], TZ).strftime("%m-%d") for t in ticks], rotation=40, fontsize=7)
        top.set_xlabel("对局日期", fontsize=9)
        c = axes[1]
        bad10, bad5 = vals("bad10"), vals("bad5")
        if np.isfinite(bad10).any():
            c.plot(x, bad10, "o-", ms=3, lw=1, color="#a86ac5", label="实际动作概率 <10%")
            c.plot(x, bad5, "x--", ms=3, lw=1, color="#d2a34b", label="实际动作概率 <5%")
            c.legend(fontsize=7, ncol=2)
        else:
            c.text(.5, .5, "恶手率暂无数据：需要含逐步概率的 mjai-reviewer JSON 报告", transform=c.transAxes, ha="center", va="center", fontsize=9, color="#777777")
        c.set_ylabel("恶手率 (%)", fontsize=9)
        c.set_ylim(bottom=0)
        d = axes[2]
        pt = np.array([g["pt"] if g.get("pt") is not None else np.nan for g in games])
        d.bar(x, pt, color=["#7899c7" if p >= 0 else "#c9747c" for p in pt], width=.25, label="每场 PT")
        d.axhline(0, color="#bbbbbb", linewidth=.6)
        d.set_ylabel("PT 增减", fontsize=9)
        e = d.twinx()
        # 缺失 PT 后无法知道真正的累计值，后续累计也留空。
        cumulative = np.cumsum(pt)
        e.plot(x, cumulative, color="#478b72", linewidth=1.2, label="累计 PT")
        e.set_ylabel("累计 PT（本区间）", color="#478b72", fontsize=9)
        h, l = d.get_legend_handles_labels(); h2, l2 = e.get_legend_handles_labels()
        d.legend(h + h2, l + l2, fontsize=7, ncol=2, loc="upper left")
        d.set_xticks(ticks)
        d.set_xlabel("牌谱编号（从旧到新） · 空白评分代表未分析成功", fontsize=9)
        stream = io.BytesIO()
        fig.savefig(stream, format=fmt, dpi=160, facecolor="white")
        return stream.getvalue()
    finally:
        plt.close(fig)
