"""F1 数据获取、缓存与中文文本格式化。"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

API = "https://api.jolpi.ca/ergast/f1"
DATA = Path(__file__).parent / "data"
BJT = timezone(timedelta(hours=8))
RACE_LEN = timedelta(hours=3)
WEEKDAYS = "一二三四五六日"
log = logging.getLogger("f1")

GP_NAMES = {
    "Australian Grand Prix": "澳大利亚大奖赛",
    "Chinese Grand Prix": "中国大奖赛",
    "Japanese Grand Prix": "日本大奖赛",
    "Miami Grand Prix": "迈阿密大奖赛",
    "Canadian Grand Prix": "加拿大大奖赛",
    "Monaco Grand Prix": "摩纳哥大奖赛",
    "Barcelona Grand Prix": "巴塞罗那大奖赛",
    "Austrian Grand Prix": "奥地利大奖赛",
    "British Grand Prix": "英国大奖赛",
    "Belgian Grand Prix": "比利时大奖赛",
    "Hungarian Grand Prix": "匈牙利大奖赛",
    "Dutch Grand Prix": "荷兰大奖赛",
    "Italian Grand Prix": "意大利大奖赛",
    "Spanish Grand Prix": "西班牙大奖赛",
    "Azerbaijan Grand Prix": "阿塞拜疆大奖赛",
    "Singapore Grand Prix": "新加坡大奖赛",
    "United States Grand Prix": "美国大奖赛",
    "Mexico City Grand Prix": "墨西哥城大奖赛",
    "Brazilian Grand Prix": "巴西大奖赛",
    "Las Vegas Grand Prix": "拉斯维加斯大奖赛",
    "Qatar Grand Prix": "卡塔尔大奖赛",
    "Bahrain Grand Prix in Malaysia": "巴林大奖赛",
    "Abu Dhabi Grand Prix": "阿布扎比大奖赛",
}
DRIVER_NAMES = {
    "Antonelli": "安东内利", "Russell": "拉塞尔", "Hamilton": "汉密尔顿", "Norris": "诺里斯",
    "Leclerc": "勒克莱尔", "Verstappen": "维斯塔潘", "Piastri": "皮亚斯特里", "Hadjar": "哈贾尔",
    "Lawson": "劳森", "Gasly": "加斯利", "Lindblad": "林德布拉德", "Colapinto": "科拉平托",
    "Bearman": "贝尔曼", "Bortoleto": "博尔托莱托", "Hülkenberg": "霍肯伯格", "Ocon": "奥康",
    "Sainz": "塞恩斯", "Albon": "阿尔本", "Alonso": "阿隆索", "Tsunoda": "角田裕毅",
    "Stroll": "斯特罗尔", "Bottas": "博塔斯", "Pérez": "佩雷兹",
}
TEAM_NAMES = {
    "Mercedes": "梅奔", "Ferrari": "法拉利", "McLaren": "迈凯轮", "Red Bull": "红牛",
    "RB F1 Team": "小红牛", "Alpine F1 Team": "Alpine", "Haas F1 Team": "哈斯",
    "Audi": "奥迪", "Williams": "威廉姆斯", "Aston Martin": "阿斯顿马丁",
    "Cadillac F1 Team": "凯迪拉克",
}
SESSIONS = [
    ("FirstPractice", "一练 FP1"),
    ("SprintQualifying", "冲刺排位 SQ"),
    ("SecondPractice", "二练 FP2"),
    ("Sprint", "冲刺赛"),
    ("ThirdPractice", "三练 FP3"),
    ("Qualifying", "排位赛 Quali"),
]


def to_utc(date: str, time: str) -> datetime:
    return datetime.fromisoformat(f"{date}T{time.rstrip('Z')}").replace(tzinfo=timezone.utc)


def gp_name(race: dict) -> str:
    return GP_NAMES.get(race["raceName"], race["raceName"])


def sessions(race: dict) -> list[tuple[str, datetime]]:
    """返回按时间排序的 (中文节次名, UTC 时间)，正赛放最后。"""
    found = [(label, to_utc(**race[key])) for key, label in SESSIONS if key in race]
    found.append(("正赛 Race", to_utc(race["date"], race["time"])))
    return sorted(found, key=lambda s: s[1])


def race_start(race: dict) -> datetime:
    return to_utc(race["date"], race["time"])


def fmt_time(dt: datetime) -> str:
    b = dt.astimezone(BJT)
    return f"周{WEEKDAYS[b.weekday()]} {b:%m-%d %H:%M}"


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


async def _fetch(url: str, cache: str):
    """请求接口并写缓存；失败时回退到缓存，再失败返回 None。"""
    DATA.mkdir(exist_ok=True)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            async with s.get(url) as r:
                r.raise_for_status()
                data = await r.json()
        (DATA / cache).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data
    except Exception as e:
        log.warning("fetch %s failed: %s", url, e)
        return _read(DATA / cache)


async def refresh_schedule() -> None:
    await _fetch(f"{API}/current.json?limit=100", "schedule.json")


def load_races() -> list[dict]:
    """手动导入文件优先，其次自动缓存。"""
    data = _read(DATA / "schedule.override.json") or _read(DATA / "schedule.json")
    return data["MRData"]["RaceTable"]["Races"] if data else []


def next_race(races: list[dict], now: datetime) -> dict | None:
    return next((r for r in races if race_start(r) + RACE_LEN > now), None)


def format_weekend(race: dict, head: str = "") -> str:
    lines = [f"{head}第{race['round']}站 {gp_name(race)}（{race['Circuit']['Location']['locality']}）"]
    lines += [f"{name}：{fmt_time(t)}" for name, t in sessions(race)]
    return "\n".join(lines + ["（均为北京时间）"])


def format_calendar(races: list[dict]) -> str:
    rows = [f"R{r['round']} {race_start(r).astimezone(BJT):%m-%d} {gp_name(r)}" for r in races]
    return "\n".join(["今年F1赛历（正赛日期，北京时间）"] + rows)


def format_reminder(race: dict, name: str, start: datetime, minutes: int) -> str:
    return f"提醒：{gp_name(race)} {name} 将在 {minutes} 分钟后开始（{fmt_time(start)}）"


SESSION_API = {  # kind -> (接口名, 结果列表字段)；Jolpica 没有练习赛/冲刺排位数据
    "race": ("results", "Results"),
    "sprint": ("sprint", "SprintResults"),
    "qualifying": ("qualifying", "QualifyingResults"),
}


async def fetch_session(round_: str, kind: str):
    """round_ 可为轮次号或 'last'。结果尚未入库时返回 None；有结果时统一放在 race['Results']。"""
    endpoint, field = SESSION_API[kind]
    data = await _fetch(f"{API}/current/{round_}/{endpoint}.json", f"{kind}-{round_}.json")
    races = data["MRData"]["RaceTable"]["Races"] if data else []
    if not races or not races[0].get(field):
        return None
    races[0]["Results"] = races[0][field]
    return races[0]


async def fetch_results(round_: str):
    return await fetch_session(round_, "race")


async def fetch_standings(kind: str):
    """kind: driver 或 constructor。"""
    data = await _fetch(f"{API}/current/{kind}standings.json", f"standings-{kind}.json")
    lists = data["MRData"]["StandingsTable"]["StandingsLists"] if data else []
    st = lists[0] if lists else None
    if st and kind == "driver":
        st["latest_team"] = await _latest_teams(st["round"])
    return st


async def _latest_teams(round_: str) -> dict:
    """driverId -> 最近一站实际代表的车队。积分榜里 Constructors 的顺序不可靠，不能直接取最后一个。"""
    race = await fetch_session(round_, "race")
    return {r["Driver"]["driverId"]: r["Constructor"] for r in race["Results"]} if race else {}


def driver_name(d: dict) -> str:
    return DRIVER_NAMES.get(d["familyName"], f"{d['givenName']} {d['familyName']}")


def team_name(c: dict) -> str:
    return TEAM_NAMES.get(c["name"], c["name"])


def format_results(race: dict, top: int = 10, title: str = "正赛") -> str:
    lines = [f"{gp_name(race)} {title}结果（前{top}）"]
    for r in race["Results"][:top]:
        lines.append(f"{r['position']}. {driver_name(r['Driver'])}（{team_name(r['Constructor'])}）+{r['points']}分")
    fastest = next((r for r in race["Results"] if r.get("FastestLap", {}).get("rank") == "1"), None)
    if fastest:
        lines.append(f"最快圈速：{driver_name(fastest['Driver'])}（{team_name(fastest['Constructor'])}）"
                     f" {fastest['FastestLap']['Time']['time']}")
    return "\n".join(lines)


def format_qualifying(race: dict, top: int = 10) -> str:
    lines = [f"{gp_name(race)} 排位赛结果（前{top}）"]
    for r in race["Results"][:top]:
        best = next(r[q] for q in ("Q3", "Q2", "Q1") if r.get(q)) if any(
            r.get(q) for q in ("Q1", "Q2", "Q3")) else "无成绩"
        lines.append(f"{r['position']}. {driver_name(r['Driver'])}（{team_name(r['Constructor'])}）{best}")
    return "\n".join(lines)


def format_standings(st: dict, kind: str, top: int | None = None) -> str:
    key = "DriverStandings" if kind == "driver" else "ConstructorStandings"
    title = "车手" if kind == "driver" else "车队"
    race = next((r for r in load_races() if r["round"] == st["round"]), None)
    after = gp_name(race) if race else f"第{st['round']}站"
    lines = [f"{title}积分榜（{after}后）"]
    for r in st[key][:top]:
        if kind == "constructor":
            who = team_name(r["Constructor"])
        else:  # 车手可能中途转队或替补，优先取最近一站的车队
            team = st.get("latest_team", {}).get(r["Driver"]["driverId"], r["Constructors"][-1])
            who = f"{driver_name(r['Driver'])}（{team_name(team)}）"
        lines.append(f"{r['position']}. {who} {r['points']}分")
    return "\n".join(lines)
