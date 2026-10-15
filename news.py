"""突发新闻：云端定时任务把 news.json 写到 GitHub 的 news-feed 分支，这里负责校验与格式化（拉取和推送见 push.py）。
news.json 来自读网页的 AI，属于不可信输入：字段、长度、网址都要查，不合格的条目直接丢弃。"""

import re
from datetime import datetime, timedelta

import f1
import health

URL = "https://raw.githubusercontent.com/MorionZHO/F1-News-Bot/news-feed/news.json"  # GitHub 有 5 分钟缓存
INTERVAL = timedelta(minutes=5)
MAX_AGE = timedelta(days=3)  # 超过 3 天的不推，防止去重记录丢失后重发旧闻
MAX_ITEMS = 5
FIELDS = {"title_zh": 40, "summary_zh": 150, "source": 30}  # 字段 -> 最大长度
LINK = re.compile(r"https?://|www\.|[\w-]+\.(com|net|org|cn|io|co|uk|me|tv)\b", re.I)  # QQ 拒发网址，也防广告链接


def _parse(it) -> dict | None:
    try:
        published = datetime.fromisoformat(it["published"])
        ok = (re.fullmatch(r"[0-9a-f]{40}", it["id"]) and published.tzinfo is not None
              and all(isinstance(it[k], str) and 0 < len(it[k].strip()) <= n and not LINK.search(it[k])
                      for k, n in FIELDS.items()))
    except (KeyError, TypeError, ValueError):
        return None
    return {k: it[k].strip() for k in FIELDS} | {"id": it["id"], "published": published} if ok else None


def valid_items(data, now: datetime) -> list[dict]:
    """合格且 3 天内的条目，按发布时间从旧到新。"""
    if data is None:
        return []
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        health.alert("news", "news.json 格式不对（缺少 items 列表），检查云端定时任务的输出")
        return []
    parsed = [_parse(it) for it in items[:MAX_ITEMS]]
    if None in parsed:
        health.alert("news", f"news.json 有 {parsed.count(None)} 条格式不合格已丢弃，检查云端定时任务的输出")
    fresh = [p for p in parsed if p and now - MAX_AGE <= p["published"] <= now + timedelta(hours=1)]
    return sorted(fresh, key=lambda p: p["published"])


def format_news(it: dict) -> str:
    return (f"【F1 News】\n「{it['title_zh']}」\n\n{it['summary_zh']}\n\n"
            f"（{it['source']} · {f1.fmt_time(it['published'])}｜程序自动抓取，内容可能有误）")
