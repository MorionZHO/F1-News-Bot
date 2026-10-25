"""F1 数据获取、缓存与中文文本格式化。"""

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

import health

API = "https://api.jolpi.ca/ergast/f1"
OPENF1 = "https://api.openf1.org/v1"
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
    "Malaysian Grand Prix": "马来西亚大奖赛",
    "Styrian Grand Prix": "施蒂利亚大奖赛",
    "70th Anniversary Grand Prix": "70周年纪念大奖赛",
}
GP_ALIASES = {  # 模糊查询用：国家 / 城市 / 赛道别名，键为接口 raceName
    "Australian Grand Prix": ["澳大利亚", "澳洲", "墨尔本", "阿尔伯特公园", "albert park"],
    "Chinese Grand Prix": ["中国", "上海", "嘉定", "上海国际赛车场", "shanghai"],
    "Japanese Grand Prix": ["日本", "铃鹿", "suzuka"],
    "Miami Grand Prix": ["迈阿密", "miami"],
    "Canadian Grand Prix": ["加拿大", "蒙特利尔", "维伦纽夫", "吉尔维伦纽夫", "villeneuve"],
    "Monaco Grand Prix": ["摩纳哥", "蒙特卡洛", "monaco"],
    "Barcelona Grand Prix": ["巴塞罗那", "加泰罗尼亚", "蒙特梅罗", "montmeló", "montmelo", "catalunya"],
    "Austrian Grand Prix": ["奥地利", "红牛环", "斯皮尔堡", "red bull ring", "spielberg"],
    "British Grand Prix": ["英国", "银石", "silverstone"],
    "Belgian Grand Prix": ["比利时", "斯帕", "spa", "弗朗科尔尚", "francorchamps"],
    "Hungarian Grand Prix": ["匈牙利", "布达佩斯", "亨格罗林", "匈格罗林", "hungaroring"],
    "Dutch Grand Prix": ["荷兰", "赞德福特", "赞德沃特", "zandvoort"],
    "Italian Grand Prix": ["意大利", "蒙扎", "蒙札", "蒙察", "monza"],
    "Spanish Grand Prix": ["西班牙", "马德里", "马德林", "madring", "madrid"],
    "Azerbaijan Grand Prix": ["阿塞拜疆", "巴库", "baku"],
    "Singapore Grand Prix": ["新加坡", "滨海湾", "marina bay"],
    "United States Grand Prix": ["美国", "奥斯汀", "美洲赛道", "cota", "austin"],
    "Mexico City Grand Prix": ["墨西哥", "罗德里格斯", "埃尔曼诺斯", "墨西哥城", "hermanos rodriguez"],
    "Brazilian Grand Prix": ["巴西", "圣保罗", "英特拉格斯", "因特拉格斯", "interlagos"],
    "Las Vegas Grand Prix": ["拉斯维加斯", "维加斯", "vegas"],
    "Qatar Grand Prix": ["卡塔尔", "多哈", "洛塞尔", "lusail", "losail"],
    "Bahrain Grand Prix in Malaysia": ["巴林", "萨基尔", "麦纳麦", "sakhir"],
    "Abu Dhabi Grand Prix": ["阿布扎比", "阿联酋", "亚斯码头", "亚斯岛", "亚斯", "yas marina"],
}
DRIVER_NAMES = {
    "Antonelli": "安东内利", "Russell": "拉塞尔", "Hamilton": "汉密尔顿", "Norris": "诺里斯",
    "Leclerc": "勒克莱尔", "Verstappen": "维斯塔潘", "Piastri": "皮亚斯特里", "Hadjar": "哈贾尔",
    "Lawson": "劳森", "Gasly": "加斯利", "Lindblad": "林德布拉德", "Colapinto": "科拉平托",
    "Bearman": "贝尔曼", "Bortoleto": "博托莱托", "Hülkenberg": "霍肯伯格", "Ocon": "奥康",
    "Sainz": "塞恩斯", "Albon": "阿尔本", "Alonso": "阿隆索", "Tsunoda": "角田裕毅",
    "Stroll": "斯特罗尔", "Bottas": "博塔斯", "Pérez": "佩雷兹",
    "Vettel": "维特尔", "Räikkönen": "莱科宁", "Ricciardo": "里卡多", "Rosberg": "罗斯伯格",
}
TEAM_NAMES = {
    "Mercedes": "梅奔", "Ferrari": "法拉利", "McLaren": "迈凯轮", "Red Bull": "红牛",
    "RB F1 Team": "小红牛", "Alpine F1 Team": "Alpine", "Haas F1 Team": "哈斯",
    "Audi": "奥迪", "Williams": "威廉姆斯", "Aston Martin": "阿斯顿马丁",
    "Cadillac F1 Team": "凯迪拉克",
    "Red Bull Racing": "红牛", "Racing Bulls": "小红牛", "Cadillac": "凯迪拉克",  # OpenF1 的叫法
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
                data = await r.json(content_type=None)  # GitHub raw 文件是 text/plain
        (DATA / cache).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        health.fetch_done(urlsplit(url).hostname)
        return data
    except Exception as e:
        log.warning("fetch %s failed: %s", url, e)
        health.fetch_done(urlsplit(url).hostname, e)
        return _read(DATA / cache)


async def refresh_schedule() -> None:
    await _fetch(f"{API}/current.json?limit=100", "schedule.json")


def load_races() -> list[dict]:
    """手动导入文件优先，其次自动缓存。"""
    data = _read(DATA / "schedule.override.json") or _read(DATA / "schedule.json")
    return data["MRData"]["RaceTable"]["Races"] if data else []


def next_race(races: list[dict], now: datetime) -> dict | None:
    return next((r for r in races if race_start(r) + RACE_LEN > now), None)


def find_race(text: str, races: list[dict]) -> dict | None:
    """按别名 / 中文站名 / 站序号（如 R23）模糊匹配赛历中的某一站；取命中的最长关键词。"""
    text = text.lower()
    best, best_len = None, 0
    for r in races:
        keys = GP_ALIASES.get(r["raceName"], []) + [gp_name(r).removesuffix("大奖赛"), f"r{r['round']}"]
        for k in keys:
            if k.lower() in text and len(k) > best_len:
                best, best_len = r, len(k)
    return best


def format_weekend(race: dict, head: str = "") -> str:
    lines = [f"{head}第{race['round']}站 {gp_name(race)}（{race['Circuit']['Location']['locality']}）"]
    lines += [f"{name}：{fmt_time(t)}" for name, t in sessions(race)]
    return "\n".join(lines + ["（均为北京时间）"])


def format_calendar(races: list[dict], title: str = "今年F1赛历") -> str:
    rows = [f"R{r['round']} {race_start(r).astimezone(BJT):%m-%d} {gp_name(r)}" for r in races]
    return "\n".join([f"{title}（正赛日期，北京时间）"] + rows)


def format_reminder(race: dict, name: str, start: datetime, minutes: int) -> str:
    return f"提醒：{gp_name(race)} {name} 将在 {minutes} 分钟后开始（{fmt_time(start)}）"


SESSION_API = {  # kind -> (接口名, 结果列表字段)；Jolpica 没有练习赛/冲刺排位数据
    "race": ("results", "Results"),
    "sprint": ("sprint", "SprintResults"),
    "qualifying": ("qualifying", "QualifyingResults"),
}


async def fetch_session(round_: str, kind: str):
    """OpenF1 为主（官方成绩公布后几分钟内可用），取不到或出错再回退 Jolpica（入库常晚几小时）。
    结果尚未出来时返回 None；有结果时统一放在 race['Results']，字段同 Jolpica。"""
    race = next((r for r in load_races() if r["round"] == round_), None)
    if race:
        try:
            result = await _openf1_session(race, kind)
        except Exception:  # 接口格式异常不能让查询和推送整个失败，回退备用源
            log.exception("openf1 %s %s failed", round_, kind)
            result = None
        if result:
            return result
    return await _jolpica_session(round_, kind)


async def _jolpica_session(round_: str, kind: str):
    endpoint, field = SESSION_API[kind]
    data = await _fetch(f"{API}/current/{round_}/{endpoint}.json", f"{kind}-{round_}.json")
    races = data["MRData"]["RaceTable"]["Races"] if data else []
    if not races or not races[0].get(field):
        return None
    races[0]["Results"] = races[0][field]
    return races[0]


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
    race = await _jolpica_session(round_, "race")  # 积分榜本身来自 Jolpica，车队也取同源，避免每次查积分都多请求 OpenF1
    return {r["Driver"]["driverId"]: r["Constructor"] for r in race["Results"]} if race else {}


GRID_SOURCES = {  # 发车格挂在排位那一节下：kind -> (Jolpica 节次字段, OpenF1 session_name)
    "race": ("Qualifying", "Qualifying"),
    "sprint": ("SprintQualifying", "Sprint Qualifying"),
}


def _code_drivers() -> dict:
    """三字代码 -> Jolpica 车手信息，取自已缓存的车手积分榜（OpenF1 的姓不带重音，不能直接查 DRIVER_NAMES）。"""
    data = _read(DATA / "standings-driver.json")
    lists = data["MRData"]["StandingsTable"]["StandingsLists"] if data else []
    return {r["Driver"]["code"]: r["Driver"] for r in (lists[0]["DriverStandings"] if lists else [])}


def _code_names() -> dict:
    return {code: driver_name(d) for code, d in _code_drivers().items()}


async def _openf1_key(race: dict, when: datetime, session_name: str):
    """开始时间与 Jolpica 相差 3 小时内、名称相同的 OpenF1 session_key；找不到返回 None。"""
    sessions = await _fetch(f"{OPENF1}/sessions?year={race['season']}", "openf1-sessions.json")
    return next((s["session_key"] for s in (sessions if isinstance(sessions, list) else [])
                 if s["session_name"] == session_name
                 and abs(datetime.fromisoformat(s["date_start"]) - when) < timedelta(hours=3)), None)


async def fetch_grid(race: dict, kind: str) -> list[str] | None:
    """OpenF1 发车顺序（中文名列表）；取不到返回 None。比赛开始前后 30 分钟内免费账号访问不了 OpenF1。"""
    field, session_name = GRID_SOURCES[kind]
    if field not in race:
        return None
    key = await _openf1_key(race, to_utc(**race[field]), session_name)
    if key is None:
        return None
    grid = await _fetch(f"{OPENF1}/starting_grid?session_key={key}", f"openf1-grid-{key}.json")
    drivers = await _fetch(f"{OPENF1}/drivers?session_key={key}", f"openf1-drivers-{key}.json")
    if not isinstance(grid, list) or not grid or not isinstance(drivers, list):
        return None
    by_number, cn = {d["driver_number"]: d for d in drivers}, _code_names()
    out = []
    for g in sorted(grid, key=lambda g: g.get("position") or 99):
        d = by_number.get(g["driver_number"], {})
        out.append(cn.get(d.get("name_acronym"), d.get("last_name") or str(g["driver_number"])))
    return out


RESULT_SOURCES = {  # kind -> (Jolpica 节次字段，None 表示正赛, OpenF1 session_name)
    "race": (None, "Race"),
    "sprint": ("Sprint", "Sprint"),
    "qualifying": ("Qualifying", "Qualifying"),
}
# 被赛会取消成绩的圈：“CAR 12 (ANT) TIME 1:37.395 DELETED - … LAP 19”或“CAR 6 (HAD) LAP DELETED - … LAP 33”
DELETED_LAP = re.compile(r"CAR (\d+) \(\w+\) (?:TIME \S+ )?(?:LAP )?DELETED\b.*?\bLAP (\d+)")


def _lap_str(sec: float) -> str:
    ms = round(sec * 1000)
    return f"{ms // 60000}:{ms % 60000 / 1000:06.3f}"


async def _fastest_lap(key: int) -> list | None:
    """[车号, 圈速]。取消成绩的圈（赛道界限违规）仍在圈速数据里，必须按赛会消息排除，否则会算错；
    任一数据取不到就返回 None（不显示最快圈速）。成绩固定后缓存，查询不用每次拉 600KB 圈速。"""
    cache = DATA / f"openf1-fastest-{key}.json"
    if hit := _read(cache):
        return hit
    laps = await _fetch(f"{OPENF1}/laps?session_key={key}", f"openf1-laps-{key}.json")
    control = await _fetch(f"{OPENF1}/race_control?session_key={key}", f"openf1-control-{key}.json")
    if not (isinstance(laps, list) and isinstance(control, list)):
        return None
    deleted = {(int(c), int(n)) for m in control for c, n in DELETED_LAP.findall(m.get("message") or "")}
    timed = [x for x in laps if x.get("lap_duration") and (x["driver_number"], x.get("lap_number")) not in deleted]
    if not timed:
        return None
    best = min(timed, key=lambda x: x["lap_duration"])
    out = [best["driver_number"], _lap_str(best["lap_duration"])]
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


def _result_row(r: dict, rank: int, d: dict, known: dict, kind: str, fastest: list | None) -> dict:
    """OpenF1 的一行成绩 -> Jolpica 格式（format_results / format_qualifying 直接可用）。"""
    code = d.get("name_acronym", "")
    driver = known.get(code) or {"driverId": code.lower(), "code": code, "givenName": d.get("first_name", ""),
                                 "familyName": d.get("last_name", "")}
    row = {"position": str(r["position"] or rank), "Driver": driver, "Constructor": {"name": d.get("team_name", "")}}
    if kind == "qualifying":
        return row | {f"Q{i}": _lap_str(t) for i, t in enumerate(r.get("duration") or [], 1) if t}
    pos = str(r["position"]) if r["position"] else None
    text, status = (("D", "Disqualified") if r["dsq"] else ("W", "Did not start") if r["dns"]
                    else (pos or "R", "Retired") if r["dnf"] or not pos else (pos, "Finished"))  # 没有名次也算未完赛
    points = r.get("points") or 0
    row |= {"positionText": text, "status": status, "laps": str(r.get("number_of_laps") or 0),
            "points": str(int(points)) if points == int(points) else str(points)}
    if fastest and fastest[0] == r["driver_number"]:
        row["FastestLap"] = {"rank": "1", "Time": {"time": fastest[1]}}
    return row


async def _openf1_session(race: dict, kind: str):
    """成绩未公布、或只公布了一部分车手时返回 None，由调用方回退 Jolpica。"""
    field, name = RESULT_SOURCES[kind]
    if field and field not in race:
        return None
    key = await _openf1_key(race, to_utc(**race[field]) if field else race_start(race), name)
    if key is None:
        return None
    rows = await _fetch(f"{OPENF1}/session_result?session_key={key}", f"openf1-result-{key}.json")
    drivers = await _fetch(f"{OPENF1}/drivers?session_key={key}", f"openf1-drivers-{key}.json")
    if not (isinstance(rows, list) and isinstance(drivers, list)) or not rows or len(rows) < len(drivers):
        return None
    rows.sort(key=lambda r: (r["position"] is None, r["position"] or 0))
    fastest = await _fastest_lap(key) if kind != "qualifying" else None
    known, by_number = _code_drivers(), {d["driver_number"]: d for d in drivers}
    out = [_result_row(r, i, by_number.get(r["driver_number"], {}), known, kind, fastest)
           for i, r in enumerate(rows, 1)]
    return race | {"Results": out}


_FULLWIDTH = str.maketrans("0123456789 ", "０１２３４５６７８９　")


def format_grid(names: list[str]) -> str:
    """每行两人；QQ 是比例字体，序号和补位全用全角字符才能让右列序号对齐。
    ponytail: 按中文名等宽补位，名字若退化成英文代码，右列会错位几个像素。"""
    num = lambda i: f"{i:>2}".translate(_FULLWIDTH)
    width = max(map(len, names[::2]))
    rows = []
    for i in range(0, len(names), 2):
        row = f"{num(i + 1)} {names[i]}"
        if i + 1 < len(names):
            row += "　" * (width - len(names[i]) + 1) + f"{num(i + 2)} {names[i + 1]}"
        rows.append(row)
    return "\n".join(["发车顺序（维修区起步不单独标注）："] + rows)


async def fetch_winners(circuit_id: str) -> list[dict] | None:
    """该赛道历届正赛冠军（按年份升序）；缓存 12 小时内直接用，避免触发接口限频。"""
    cache = f"winners-{circuit_id}.json"
    path = DATA / cache
    fresh = path.exists() and time.time() - path.stat().st_mtime < 12 * 3600
    # ponytail: limit=100 足够，现役赛道最多约 75 届
    data = _read(path) if fresh else await _fetch(f"{API}/circuits/{circuit_id}/results/1.json?limit=100", cache)
    return data["MRData"]["RaceTable"]["Races"] if data else None


def format_winners(race: dict, winners: list[dict], n: int = 5) -> str:
    if not winners:
        return "本赛道此前未举办过F1大奖赛。"
    lines = [f"本赛道近{min(n, len(winners))}届冠军："]
    for w in reversed(winners[-n:]):
        r = w["Results"][0]
        other = f" [{gp_name(w)}]" if w["raceName"] != race["raceName"] else ""
        lines.append(f"{w['season']} {driver_name(r['Driver'])}（{team_name(r['Constructor'])}）{other}")
    return "\n".join(lines)


def driver_name(d: dict) -> str:
    return DRIVER_NAMES.get(d["familyName"], f"{d['givenName']} {d['familyName']}")


def team_name(c: dict) -> str:
    return TEAM_NAMES.get(c["name"], c["name"])


def format_results(race: dict, title: str = "正赛") -> str:
    scorers = [r for r in race["Results"] if float(r["points"]) > 0]  # 按实际得分筛，积分规则变了也不用改
    lines = [f"{gp_name(race)} {title}结果（前{len(scorers)}）"]
    for r in scorers:
        lines.append(f"{r['position']}. {driver_name(r['Driver'])}（{team_name(r['Constructor'])}）+{r['points']}分")
    blocks = ["\n".join(lines)]  # 各模块之间空一行
    fastest = next((r for r in race["Results"] if r.get("FastestLap", {}).get("rank") == "1"), None)
    if fastest:
        blocks.append(f"【最快圈速】\n{driver_name(fastest['Driver'])}（{team_name(fastest['Constructor'])}）"
                      f" {fastest['FastestLap']['Time']['time']}")
    return "\n\n".join(blocks + _non_finishers(race["Results"]))


def _non_finish_kind(r: dict) -> str | None:
    """Jolpica 自 2024 年起不再给具体退赛原因，只剩 Retired / Did not start / Disqualified。
    跑够 90% 距离的退赛车手仍有名次，状态同样是 Retired，也算退赛。"""
    pos, status = r["positionText"], r["status"]
    if pos in ("D", "E") or status == "Disqualified":
        return "取消资格"
    if pos in ("W", "F") or status in ("Did not start", "Withdrew"):
        return "未起步"
    if pos in ("R", "N") or status not in ("Finished", "Lapped") and not status.startswith("+"):
        return "退赛"
    return None


def _non_finishers(results: list[dict]) -> list[str]:
    groups = {"退赛": [], "未起步": [], "取消资格": []}
    for r in results:
        kind = _non_finish_kind(r)
        if kind:
            laps = f"（完成{r['laps']}圈）" if kind == "退赛" else ""
            groups[kind].append(driver_name(r["Driver"]) + laps)
    return [f"【{kind}】\n{'、'.join(names)}" for kind, names in groups.items() if names]


def format_qualifying(race: dict, top: int = 10) -> str:
    lines = [f"{gp_name(race)} 排位赛结果（前{top}）"]
    for r in race["Results"][:top]:
        best = next(r[q] for q in ("Q3", "Q2", "Q1") if r.get(q)) if any(
            r.get(q) for q in ("Q1", "Q2", "Q3")) else "无成绩"
        lines.append(f"{r['position']}. {driver_name(r['Driver'])}（{team_name(r['Constructor'])}）{best}")
    return "\n".join(lines)


# ---- 争冠计算 ----
# 规则依据：FIA 2026 F1 Regulations Section A，A2.2.1（正赛积分）、A2.2.2（冲刺赛积分）、A2.1.4（同分判定）。
# 规则变更时需要修改的地方都标了「规则变更」，搜索这四个字即可：
#   积分制度 -> RACE_POINTS / SPRINT_POINTS；最快圈加分恢复 -> weekend_max 与 clinch_requirements；
#   冲刺赛赛制（场次、积分、是否计入同分判定）-> SPRINT_POINTS、weekend_max、clinch_requirements、_locked；
#   同分判定规则 -> _locked。
# ponytail: 本站按完整比赛距离积分计算；红旗缩短比赛（<75% 距离用缩短积分表）和并列名次平分积分不在考虑范围内。
RACE_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]  # 规则变更：正赛积分表
SPRINT_POINTS = [8, 7, 6, 5, 4, 3, 2, 1]  # 规则变更：冲刺赛积分表
SPRINT_MAX = SPRINT_POINTS[0]
TITLE_LABELS = ["车手总冠军", "车手总亚军", "车手总季军"]


def points_for(pos: int) -> int:
    return RACE_POINTS[pos - 1] if pos <= len(RACE_POINTS) else 0


def weekend_max(race: dict) -> int:
    """一个比赛周单人最多能拿的分。
    规则变更：最快圈加分恢复则正赛部分 +1；冲刺赛场次或积分变化改这里。"""
    return RACE_POINTS[0] + (SPRINT_MAX if "Sprint" in race else 0)


def _locked(gap: int, rest: int, me_wins: int, rival_wins: int) -> bool:
    """本站后领先 gap 分、之后最多还有 rest 分时，是否无论之后怎么跑都排在对手前面。
    gap == rest 时对手只能靠之后每站满分（必然包揽之后全部正赛冠军）追平，此时比正赛冠军数，
    rival_wins 已含这些冠军。gap < rest 时对手可以直接反超，不算锁定。
    规则变更：同分判定改变（如冲刺赛冠军也计入）改这里和 clinch_requirements 的胜场计算。
    ponytail: 冠军数也相同时要比亚军数，接口只给冠军数，按未锁定处理（偏保守）。"""
    return gap > rest or (gap == rest and me_wins > rival_wins)


def clinch_requirements(me: dict, rows: list[dict], nxt: dict, after: list[dict]) -> list:
    """本站正赛 me 跑第 p 名（11 = 无积分）时，rows 里其他人各需落到第几名或更后，me 才能锁定排在他们所有人前面。
    返回 [(p, [条件...]), ...]，只含能锁定的 p；名次越靠后越难锁定，所以一旦锁不住就停止。
    本站冲刺赛按最坏情况：me 0 分、对手拿满。之后的比赛同样按 me 0 分、对手每站满分。
    规则变更：最快圈加分恢复时，对手前十名的分数要 +1（me 按最坏情况不加）。"""
    rest = sum(weekend_max(r) for r in after)
    sprint_pts = SPRINT_MAX if "Sprint" in nxt else 0
    me_pts, me_wins = int(me["points"]), int(me["wins"])  # 接口的 wins 只计正赛冠军，与同分判定口径一致
    out = []
    for p in range(1, 12):
        conds = []
        for r in rows:
            if r is me:
                continue
            pts, wins = int(r["points"]) + sprint_pts, int(r["wins"]) + len(after)
            bad = [q for q in range(1, 12) if (q != p or p == 11) and not _locked(
                me_pts + points_for(p) - pts - points_for(q), rest, me_wins + (p == 1), wins + (q == 1))]
            if 11 in bad:  # 对手零分也挡不住，me 名次再靠后更不可能
                return out
            if bad:
                n = max(bad) + 1
                conds.append(driver_name(r["Driver"]) + ("未得分" if n == 11 else f"第{n}名或更后"))
        out.append((p, conds))
    return out


def find_driver(text: str, rows: list[dict]) -> dict | None:
    low = text.lower()
    return next((r for r in rows if r["Driver"]["code"].lower() in low or driver_name(r["Driver"]) in text), None)


TITLE_NOTE = "（同分按胜场数判定）"
SPRINT_NOTE = "（本站含冲刺赛，按最坏情况：本人0分、对手拿冲刺赛第1）"


def _req_lines(reqs: list) -> list[str]:
    """条件相同的相邻名次合并成一行，如“第6-9名”“第10名及更后”。"""
    groups = []  # [起始名次, 结束名次, 条件]
    for p, c in reqs:
        if groups and groups[-1][2] == c:
            groups[-1][1] = p
        else:
            groups.append([p, p, c])
    lines = []
    for a, b, c in groups:
        if a == 11:
            label = "未进前十"
        elif b == 11:
            label = f"第{a}名及更后"
        else:
            label = f"第{a}名" if a == b else f"第{a}-{b}名"
        lines.append(f"· {label}：" + ("、".join(c) or "无需对手配合"))
    return lines


def _already_locked(reqs: list) -> bool:
    """本站零分且不看对手也能锁定，说明已经锁定。"""
    return bool(reqs) and reqs[-1] == (11, [])


def format_title_outlook(st: dict, races: list[dict], target: dict | None = None) -> str:
    rows = st["DriverStandings"]
    me = target or rows[0]
    name, me_pts = driver_name(me["Driver"]), int(me["points"])
    left = [r for r in races if int(r["round"]) > int(st["round"])]
    max_all = sum(weekend_max(r) for r in left)
    head = f"车手总冠军形势（第{st['round']}站后）\n{name} {me_pts}分，剩余{len(left)}站最多再得{max_all}分"
    if not left:
        return f"{head}\n本赛季已结束。"
    reqs = clinch_requirements(me, rows, left[0], left[1:])
    if _already_locked(reqs):
        return f"{head}\n{name}已提前夺冠！"
    if me_pts + max_all < max(int(r["points"]) for r in rows if r is not me):
        return f"{head}\n{name}已无夺冠可能。"
    lines = [head, f"下一站 {gp_name(left[0])}，{name}提前夺冠的条件："]
    lines += _req_lines(reqs) or ["· 下一站无法提前锁定冠军"]
    return "\n".join(lines + ([SPRINT_NOTE] if "Sprint" in left[0] else []) + [TITLE_NOTE])


def _title_tiers(rows: list[dict], race: dict, after: list[dict]) -> tuple[list[dict], list[str]]:
    """依次判断冠军、亚军、季军：已锁定的记下来，遇到第一个未锁定的名次就算本站锁定它的条件并停止。"""
    locked, lines = [], []
    for label in TITLE_LABELS:
        pool = [r for r in rows if all(r is not x for x in locked)]
        reqs = [(r, clinch_requirements(r, pool, race, after)) for r in pool]
        done = next((r for r, q in reqs if _already_locked(q)), None)
        if done:
            locked.append(done)
            continue
        for r, q in reqs:
            if q:
                lines += [f"{driver_name(r['Driver'])}本站锁定{label}的条件："] + _req_lines(q)
        break
    return locked, lines


def format_clinch_preview(st: dict, races: list[dict], race: dict) -> str:
    """比赛周预告用：已锁定的冠/亚/季军和本站可能锁定下一个名次的条件；都没有时返回空串。
    积分榜没更新到上一站时不判断，避免用过期数据。"""
    if int(st["round"]) != int(race["round"]) - 1:
        return ""
    after = [r for r in races if int(r["round"]) > int(race["round"])]
    locked, lines = _title_tiers(st["DriverStandings"], race, after)
    names = [driver_name(r["Driver"]) for r in locked]
    if len(locked) == len(TITLE_LABELS):
        return "【争冠】\n车手总排名前三已确定：" + "、".join(f"{label[3:]} {n}" for label, n in zip(TITLE_LABELS, names))
    if not locked and not lines:
        return ""
    out = ["【争冠】"] + [f"{n}已锁定{label}" for label, n in zip(TITLE_LABELS, names)] + lines
    if lines:
        out += ([SPRINT_NOTE] if "Sprint" in race else []) + [TITLE_NOTE]
    return "\n".join(out)


def format_session(result: dict, kind: str) -> str:
    if kind == "qualifying":
        return format_qualifying(result)
    return format_results(result, title="冲刺赛" if kind == "sprint" else "正赛")


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
