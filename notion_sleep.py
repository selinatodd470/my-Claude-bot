"""Notion API client — 醒后数据记录 读写。"""

import os
import json
import logging
import requests

logger = logging.getLogger(__name__)

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_SLEEP_DB_ID = os.environ.get(
    "NOTION_SLEEP_DB_ID", "fd77d0d8e81a4ed68250861481abd0fc"
)

NOTION_API_BASE = "https://api.notion.com/v1"


def _headers():
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }


def _to_list(v):
    """防御：LLM 有时传 string，确保转成 list。"""
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return v
    return []


def _find_page_by_date(date_str: str) -> str | None:
    """查询该日期是否已有记录，有则返回 page_id。"""
    url = f"{NOTION_API_BASE}/databases/{NOTION_SLEEP_DB_ID}/query"
    payload = {
        "filter": {"property": "日期", "date": {"equals": date_str}},
        "page_size": 1,
    }
    resp = requests.post(url, headers=_headers(), json=payload)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0]["id"] if results else None


def _build_properties(data: dict) -> dict:
    """将 LLM 提取的数据转为 Notion properties dict。只包含 data 中存在的字段。"""
    props = {}

    if date_val := data.get("date"):
        props["日期"] = {"date": {"start": date_val}}
        props["本列留空"] = {"title": [{"text": {"content": date_val}}]}

    if data.get("alertness") is not None:
        props["清醒度"] = {"number": data["alertness"]}

    if data.get("fatigue") is not None:
        props["疲劳度"] = {"number": data["fatigue"]}

    if data.get("pitolisant") is not None:
        props["替洛利生"] = {"checkbox": data["pitolisant"]}

    if pit_time := _to_list(data.get("pitolisant_time")):
        props["服药时间段"] = {
            "multi_select": [{"name": t} for t in pit_time]
        }

    if data.get("caffeine") is not None:
        props["咖啡因"] = {"checkbox": data["caffeine"]}

    if caf_time := _to_list(data.get("caffeine_time")):
        props["咖啡因时段"] = {
            "multi_select": [{"name": t} for t in caf_time]
        }

    if data.get("allergy_med") is not None:
        props["过敏药"] = {"checkbox": data["allergy_med"]}

    if am_time := _to_list(data.get("allergy_med_time")):
        props["过敏药时段"] = {
            "multi_select": [{"name": t} for t in am_time]
        }

    if notes := data.get("notes"):
        props["特殊情况"] = {
            "rich_text": [{"text": {"content": notes}}]
        }

    return props


def log_wakeup_record(data: dict) -> dict:
    """写入（或更新）一条醒后记录。已有同日记录则合并更新，否则新建。"""
    if not NOTION_API_KEY:
        raise RuntimeError("NOTION_API_KEY 未设置")

    date_str = data.get("date", "")
    if not date_str:
        raise ValueError("缺少 date 字段")

    properties = _build_properties(data)
    if not properties:
        raise ValueError("没有可写入的数据")

    existing_id = _find_page_by_date(date_str)

    if existing_id:
        logger.info(f"更新已有记录 {existing_id[:8]}… 日期={date_str}")
        url = f"{NOTION_API_BASE}/pages/{existing_id}"
        resp = requests.patch(url, headers=_headers(), json={"properties": properties})
        resp.raise_for_status()
    else:
        logger.info(f"创建新记录 日期={date_str}")
        url = f"{NOTION_API_BASE}/pages"
        payload = {
            "parent": {"database_id": NOTION_SLEEP_DB_ID},
            "properties": properties,
        }
        resp = requests.post(url, headers=_headers(), json=payload)
        resp.raise_for_status()

    return resp.json()
