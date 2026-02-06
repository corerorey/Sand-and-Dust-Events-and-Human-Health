# -*- coding: utf-8 -*-
"""
zonghe.py  天气后报网：历史天气（月）+ AQI（月）合并爬取

依赖：
  pip install requests beautifulsoup4 lxml pandas

PyCharm 直接点运行：
  修改下面 CONFIG，然后运行即可生成 CSV。
"""

from __future__ import annotations

import random
import re
import time
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ================== 运行配置（PyCharm 点运行只改这里） ==================
CONFIG = {
    "city": "lanzhou",          # 城市拼音（URL 里用的）
    "start": "202001",          # 起始月份 YYYYMM
    "end": "202412",            # 结束月份 YYYYMM
    "out": "lanzhou_202001_202412.csv",  # 输出 CSV（可写绝对路径）
    "sleep_range": (1.0, 3.0),  # 每个月请求间隔（秒），友好一点
}
# =====================================================================

BASE = "https://www.tianqihoubao.com"


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.proxies = {"http": None, "https": None}

    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Referer": BASE + "/",
        }
    )
    return s


# def fetch_html(url: str, s: requests.Session, timeout: int = 25) -> str:
#     r = s.get(url, timeout=timeout)
#     r.raise_for_status()
#
#     # 中文站点可能是 gb2312/gbk；requests 有时会猜错
#     if not r.encoding or r.encoding.lower() in {"iso-8859-1", "ascii"}:
#         r.encoding = r.apparent_encoding or "utf-8"
#     return r.text
def fetch_html(url: str, s: requests.Session, timeout: int = 25) -> str:
    for attempt in range(1, 7):  # 最多 6 次
        try:
            r = s.get(url, timeout=timeout)
            r.raise_for_status()

            if not r.encoding or r.encoding.lower() in {"iso-8859-1", "ascii"}:
                r.encoding = r.apparent_encoding or "utf-8"
            return r.text

        except requests.RequestException as e:
            if attempt == 6:
                raise
            # 指数退避 + 抖动
            sleep_s = (2 ** (attempt - 1)) + random.uniform(0.3, 0.9)
            print(f"[WARN] 请求失败，第{attempt}次重试前等待 {sleep_s:.1f}s: {url}  {e}")
            time.sleep(sleep_s)


def iter_months(start_yyyymm: str, end_yyyymm: str) -> Iterable[str]:
    if not re.fullmatch(r"\d{6}", start_yyyymm) or not re.fullmatch(r"\d{6}", end_yyyymm):
        raise ValueError("start/end 必须是 YYYYMM，例如 202601")

    sy, sm = int(start_yyyymm[:4]), int(start_yyyymm[4:])
    ey, em = int(end_yyyymm[:4]), int(end_yyyymm[4:])
    y, m = sy, sm
    while (y, m) <= (ey, em):
        yield f"{y:04d}{m:02d}"
        m += 1
        if m == 13:
            y += 1
            m = 1


def month_url(city_slug: str, yyyymm: str) -> str:
    if not re.fullmatch(r"\d{6}", yyyymm):
        raise ValueError(f"yyyymm 需要是 6 位数字，如 202601；你传的是：{yyyymm}")
    return f"{BASE}/lishi/{city_slug}/month/{yyyymm}.html"


def aqi_month_url(city_slug: str, yyyymm: str) -> str:
    if not re.fullmatch(r"\d{6}", yyyymm):
        raise ValueError(f"yyyymm 需要是 6 位数字，如 202601；你传的是：{yyyymm}")
    return f"{BASE}/aqi/{city_slug}-{yyyymm}.html"


def _split_day_night(text: str) -> Tuple[Optional[str], Optional[str]]:
    parts = [p.strip() for p in re.split(r"\s*/\s*", text) if p.strip()]
    if len(parts) == 0:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]


def _clean_cell(x: str) -> Optional[str]:
    if x is None:
        return None
    x = str(x).strip()
    if x in {"", "-", "--", "—", "暂无"}:
        return None
    return x


def normalize_date(s: Optional[str]) -> Optional[str]:
    """
    把以下格式统一为 YYYY-MM-DD，便于合并：
      - 2025年12月01日 / 2025年12月1日
      - 2025-12-01 / 2025/12/1
    """
    if not s:
        return None
    s = str(s).strip()

    m = re.match(r"^\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*$", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mo:02d}-{d:02d}"

    m = re.match(r"^\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*$", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mo:02d}-{d:02d}"

    return s


# -------------------------- 历史天气（月）解析 --------------------------
def _pick_weather_table(soup: BeautifulSoup):
    tables = soup.find_all("table")
    for t in tables:
        first_tr = t.find("tr")
        if not first_tr:
            continue
        head_cells = [c.get_text(strip=True) for c in first_tr.find_all(["th", "td"])]
        head = "".join(head_cells)
        if ("日期" in head) and ("气温" in head or "最高" in head) and ("风" in head):
            return t
    return tables[0] if tables else None


def parse_month_page(html: str) -> List[Dict[str, Optional[str]]]:
    soup = BeautifulSoup(html, "lxml")
    table = _pick_weather_table(soup)
    if table is None:
        raise RuntimeError("历史天气页面没有找到数据表格（页面结构可能变了）")

    rows: List[Dict[str, Optional[str]]] = []
    trs = table.find_all("tr")
    for tr in trs[1:]:
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue

        date_raw = _clean_cell(tds[0].get_text(strip=True))  # 输出仍保留中文日期
        date_key = normalize_date(date_raw)                  # 内部用于合并

        weather = _clean_cell(tds[1].get_text(strip=True))
        temp = _clean_cell(tds[2].get_text(strip=True))
        wind = _clean_cell(tds[3].get_text(strip=True))

        w_day, w_night = _split_day_night(weather or "")
        t_high, t_low = _split_day_night(temp or "")
        wd_day, wd_night = _split_day_night(wind or "")

        rows.append(
            {
                "date": date_raw,
                "date_key": date_key,  # 临时列：最后会删掉，不会出现在 CSV
                "weather_day": _clean_cell(w_day),
                "weather_night": _clean_cell(w_night),
                "temp_high": _clean_cell(t_high),
                "temp_low": _clean_cell(t_low),
                "wind_day": _clean_cell(wd_day),
                "wind_night": _clean_cell(wd_night),
            }
        )

    return rows


# ------------------------------ AQI（月）解析 ------------------------------
def _pick_aqi_table(soup: BeautifulSoup):
    tables = soup.find_all("table")
    for t in tables:
        first_tr = t.find("tr")
        if not first_tr:
            continue
        head_cells = [c.get_text(strip=True) for c in first_tr.find_all(["th", "td"])]
        head = "".join(head_cells)
        if ("日期" in head) and ("AQI" in head):
            return t
    return tables[0] if tables else None


def _normalize_header(h: str) -> str:
    h = (h or "").strip().lower()
    h = h.replace(" ", "")
    h = h.replace("（", "(").replace("）", ")")
    return h


def parse_aqi_month_page(html: str) -> Dict[str, Dict[str, Optional[str]]]:
    """
    返回（key 一律是 YYYY-MM-DD）：
      {
        'YYYY-MM-DD': {
           'aqi': ...,
           'quality': ...,
           'aqi_rank': ...,
           'pm25': ...,
           'pm10': ...,
           'no2': ...,
           'so2': ...,
           'co': ...,
           'o3': ...
        },
        ...
      }
    """
    soup = BeautifulSoup(html, "lxml")
    table = _pick_aqi_table(soup)
    if table is None:
        raise RuntimeError("AQI 页面没有找到数据表格（页面结构可能变了）")

    header_tr = table.find_all("tr")[0]
    headers = [_normalize_header(c.get_text(strip=True)) for c in header_tr.find_all(["th", "td"])]

    want = {
        "date": {"日期", "date"},
        "aqi": {"aqi", "aqi指数", "aqi指数(aqi)"},
        "quality": {"质量等级", "空气质量", "质量", "等级"},
        "aqi_rank": {"当天aqi排名", "aqi排名", "排名"},
        "pm25": {"pm2.5", "pm25"},
        "pm10": {"pm10"},
        "no2": {"no2", "n02"},
        "so2": {"so2"},
        "co": {"co"},
        "o3": {"o3", "ozone"},
    }
    want_norm = {k: {_normalize_header(x) for x in v} for k, v in want.items()}

    idx_map: Dict[str, int] = {}
    for i, h in enumerate(headers):
        for key, aliases in want_norm.items():
            if h in aliases and key not in idx_map:
                idx_map[key] = i

    if "date" not in idx_map or "aqi" not in idx_map:
        raise RuntimeError(f"AQI 表头解析失败，headers={headers}")

    data: Dict[str, Dict[str, Optional[str]]] = {}
    trs = table.find_all("tr")
    for tr in trs[1:]:
        tds = tr.find_all("td")
        if not tds:
            continue

        def get(key: str) -> Optional[str]:
            j = idx_map.get(key)
            if j is None or j >= len(tds):
                return None
            return _clean_cell(tds[j].get_text(strip=True))

        date_raw = get("date")
        date_key = normalize_date(date_raw)
        if not date_key:
            continue

        data[date_key] = {
            "aqi": get("aqi"),
            "quality": get("quality"),
            "aqi_rank": get("aqi_rank"),
            "pm25": get("pm25"),
            "pm10": get("pm10"),
            "no2": get("no2"),
            "so2": get("so2"),
            "co": get("co"),
            "o3": get("o3"),
        }

    return data


# ------------------------------ 合并爬取 ------------------------------
def crawl(city_slug: str, start_yyyymm: str, end_yyyymm: str, sleep_range=(0.8, 1.8)) -> pd.DataFrame:
    s = _session()
    all_rows: List[Dict[str, Optional[str]]] = []

    for yyyymm in iter_months(start_yyyymm, end_yyyymm):
        # 1) 历史天气（月）
        url_weather = month_url(city_slug, yyyymm)
        html_weather = fetch_html(url_weather, s)
        rows = parse_month_page(html_weather)

        # 2) AQI（月）
        aqi_by_date: Dict[str, Dict[str, Optional[str]]] = {}
        try:
            url_aqi = aqi_month_url(city_slug, yyyymm)
            html_aqi = fetch_html(url_aqi, s)
            aqi_by_date = parse_aqi_month_page(html_aqi)
        except Exception as e:
            print(f"[WARN] AQI 获取失败: {city_slug} {yyyymm}  {e}")

        # 3) 按 date_key 合并（YYYY-MM-DD）
        for r in rows:
            r["city_slug"] = city_slug
            r["yyyymm"] = yyyymm

            key = r.get("date_key") or ""
            aqi_row = aqi_by_date.get(key, {})

            r["aqi"] = aqi_row.get("aqi")
            r["aqi_quality"] = aqi_row.get("quality")
            r["aqi_rank"] = aqi_row.get("aqi_rank")
            r["aqi_pm25"] = aqi_row.get("pm25")
            r["aqi_pm10"] = aqi_row.get("pm10")
            r["aqi_no2"] = aqi_row.get("no2")
            r["aqi_so2"] = aqi_row.get("so2")
            r["aqi_co"] = aqi_row.get("co")
            r["aqi_o3"] = aqi_row.get("o3")

            all_rows.append(r)

        time.sleep(random.uniform(*sleep_range))

    df = pd.DataFrame(all_rows)

    # 温度转数值（失败会变 NaN）
    def to_int_series(series: pd.Series) -> pd.Series:
        return pd.to_numeric(series.astype(str).str.extract(r"(-?\d+)")[0], errors="coerce")

    if "temp_high" in df.columns:
        df["temp_high_c"] = to_int_series(df["temp_high"])
    if "temp_low" in df.columns:
        df["temp_low_c"] = to_int_series(df["temp_low"])

    # AQI/污染物数值列
    for col in ["aqi", "aqi_rank", "aqi_pm25", "aqi_pm10", "aqi_no2", "aqi_so2", "aqi_o3"]:
        if col in df.columns:
            df[col + "_num"] = pd.to_numeric(df[col], errors="coerce")
    if "aqi_co" in df.columns:
        df["aqi_co_num"] = pd.to_numeric(df["aqi_co"], errors="coerce")

    # ======= 只保留你指定的列（删掉冗余列，比如 date_key 等） =======
    keep_cols = [
        "date", "weather_day", "weather_night", "temp_high", "temp_low", "wind_day", "wind_night",
        "city_slug", "yyyymm",
        "aqi", "aqi_quality", "aqi_rank", "aqi_pm25", "aqi_pm10", "aqi_no2", "aqi_so2", "aqi_co", "aqi_o3",
        "temp_high_c", "temp_low_c",
        "aqi_num", "aqi_rank_num", "aqi_pm25_num", "aqi_pm10_num", "aqi_no2_num", "aqi_so2_num", "aqi_o3_num", "aqi_co_num",
    ]

    # 若某些列不存在（比如 AQI 页某月缺列），补空列，保证输出列完整
    for c in keep_cols:
        if c not in df.columns:
            df[c] = pd.NA

    df = df[keep_cols]
    return df


def main():
    city = CONFIG["city"]
    start = CONFIG["start"]
    end = CONFIG["end"]
    out = CONFIG["out"]
    sleep_range = CONFIG.get("sleep_range", (0.8, 1.8))

    df = crawl(city, start, end, sleep_range=sleep_range)
    df.to_csv(out, index=False, encoding="utf-8-sig")

    print(f"Saved: {out}  rows={len(df)}")
    print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()


# 空气质量指数(AQI)数据： 数值单位：μg/m3(CO为mg/m3)