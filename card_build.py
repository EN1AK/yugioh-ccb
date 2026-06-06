#!/usr/bin/env python3
"""Update local card database and card images.

Data source:
  https://ygocdb.com/api/v0/cards.zip

The zip contains cards.json. This script converts it to the ygopro-compatible
SQLite schema used by the app and downloads card pictures to static/card/.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests


CARDS_ZIP_URL = "https://ygocdb.com/api/v0/cards.zip"
CARDS_ZIP_MD5_URL = "https://ygocdb.com/api/v0/cards.zip.md5"
CARD_IMAGE_URL = "https://cdn.233.momobako.com/ygopro/pics/{id}.jpg"
HOT_API_URL = "https://sapi.moecube.com:444/ygopro/analytics/single/type"
HOT_API_PARAMS = {
    "type": "month",
    "lang": "cn",
    "extra": "name",
    "source": "mycard-athletic",
}

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "cards.cdb"
MD5_PATH = ROOT / "cards.cdb.md5"
IMAGE_DIR = ROOT / "static" / "card"


def int_value(value, default=0):
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def pick_name(card: dict) -> str:
    for key in ("cn_name", "sc_name", "md_name", "nwbbs_n", "cnocg_n", "jp_name", "en_name"):
        value = card.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(card.get("id", "")).strip()


def fetch_remote_md5(url: str = CARDS_ZIP_MD5_URL) -> str:
    print(f"获取远端 MD5: {url}")
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    match = re.search(r"[0-9a-fA-F]{32}", response.text)
    if not match:
        raise RuntimeError(f"无法解析 MD5 响应: {response.text[:100]!r}")
    return match.group(0).lower()


def read_local_md5(md5_path: Path = MD5_PATH) -> str | None:
    if not md5_path.exists():
        return None
    match = re.search(r"[0-9a-fA-F]{32}", md5_path.read_text(encoding="utf-8", errors="ignore"))
    return match.group(0).lower() if match else None


def write_local_md5(md5: str, md5_path: Path = MD5_PATH):
    md5_path.write_text(md5 + "\n", encoding="utf-8")


def fetch_cards_zip(url: str = CARDS_ZIP_URL) -> bytes:
    print(f"下载卡片数据: {url}")
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def parse_cards_json(zip_content: bytes, expected_md5: str | None = None) -> dict:
    with zipfile.ZipFile(io.BytesIO(zip_content)) as archive:
        if "cards.json" not in archive.namelist():
            raise RuntimeError("cards.zip 中找不到 cards.json")
        json_content = archive.read("cards.json")

    actual_md5 = hashlib.md5(json_content).hexdigest()
    if expected_md5 and actual_md5 != expected_md5:
        raise RuntimeError(f"cards.json MD5 校验失败: expected={expected_md5}, actual={actual_md5}")

    data = json.loads(json_content)

    if not isinstance(data, dict):
        raise RuntimeError("cards.json 顶层结构不是对象")
    return data


def fetch_cards_json(url: str = CARDS_ZIP_URL, expected_md5: str | None = None) -> dict:
    return parse_cards_json(fetch_cards_zip(url), expected_md5)


def iter_card_rows(cards: dict):
    for card in cards.values():
        if not isinstance(card, dict):
            continue
        card_id = int_value(card.get("id"))
        if card_id <= 0:
            continue

        data = card.get("data") or {}
        if not isinstance(data, dict):
            data = {}

        yield {
            "id": card_id,
            "ot": int_value(data.get("ot")),
            "alias": int_value(data.get("alias")),
            "setcode": int_value(data.get("setcode")),
            "type": int_value(data.get("type")),
            "atk": int_value(data.get("atk"), -2),
            "def": int_value(data.get("def"), -2),
            "level": int_value(data.get("level")),
            "race": int_value(data.get("race")),
            "attribute": int_value(data.get("attribute")),
            "category": int_value(data.get("category")),
            "name": pick_name(card),
            "desc": (card.get("text") or {}).get("desc", "") if isinstance(card.get("text"), dict) else "",
        }


def create_database(cards: dict, db_path: Path = DB_PATH) -> int:
    rows = list(iter_card_rows(cards))
    if not rows:
        raise RuntimeError("没有可写入数据库的卡片数据")

    fd, tmp_name = tempfile.mkstemp(prefix="cards-", suffix=".cdb", dir=str(db_path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)

    try:
        conn = sqlite3.connect(str(tmp_path))
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE datas (
                id INTEGER PRIMARY KEY,
                ot INTEGER,
                alias INTEGER,
                setcode INTEGER,
                type INTEGER,
                atk INTEGER,
                def INTEGER,
                level INTEGER,
                race INTEGER,
                attribute INTEGER,
                category INTEGER,
                hot INTEGER DEFAULT 0
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE texts (
                id INTEGER PRIMARY KEY,
                name TEXT,
                desc TEXT,
                str1 TEXT,
                str2 TEXT,
                str3 TEXT,
                str4 TEXT,
                str5 TEXT,
                str6 TEXT,
                str7 TEXT,
                str8 TEXT,
                str9 TEXT,
                str10 TEXT,
                str11 TEXT,
                str12 TEXT,
                str13 TEXT,
                str14 TEXT,
                str15 TEXT,
                str16 TEXT
            );
            """
        )
        cur.executemany(
            """
            INSERT OR REPLACE INTO datas
                (id, ot, alias, setcode, type, atk, def, level, race, attribute, category, hot)
            VALUES
                (:id, :ot, :alias, :setcode, :type, :atk, :def, :level, :race, :attribute, :category, 0);
            """,
            rows,
        )
        cur.executemany(
            """
            INSERT OR REPLACE INTO texts
                (id, name, desc, str1, str2, str3, str4, str5, str6, str7, str8,
                 str9, str10, str11, str12, str13, str14, str15, str16)
            VALUES
                (:id, :name, :desc, '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '');
            """,
            rows,
        )
        cur.execute("CREATE INDEX idx_texts_name ON texts(name);")
        conn.commit()
        conn.close()

        tmp_path.replace(db_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return len(rows)


def fetch_hot_names() -> set[str]:
    print("获取热门卡列表")
    response = requests.get(HOT_API_URL, params=HOT_API_PARAMS, timeout=20)
    response.raise_for_status()
    payload = response.json()

    names = set()
    if isinstance(payload, dict):
        for items in payload.values():
            if not isinstance(items, list):
                continue
            for entry in items:
                if not isinstance(entry, dict):
                    continue
                name = (entry.get("name") or {}).get("zh-CN")
                if name:
                    names.add(name)
    return names


def mark_hot_cards(db_path: Path = DB_PATH) -> int:
    try:
        hot_names = fetch_hot_names()
    except Exception as exc:
        print(f"热门卡更新失败，跳过: {exc}", file=sys.stderr)
        return 0

    if not hot_names:
        return 0

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("UPDATE datas SET hot = 0;")
    cur.executemany(
        """
        UPDATE datas
           SET hot = 1
         WHERE id IN (SELECT id FROM texts WHERE name = ?);
        """,
        [(name,) for name in hot_names],
    )
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM datas WHERE hot = 1;")
    count = int(cur.fetchone()[0])
    conn.close()
    return count


def load_card_ids_from_db(db_path: Path = DB_PATH) -> list[int]:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT id FROM datas ORDER BY id;")
    ids = [int(row[0]) for row in cur.fetchall()]
    conn.close()
    return ids


def download_one_image(session: requests.Session, card_id: int, image_dir: Path, timeout: int) -> str:
    target = image_dir / f"{card_id}.jpg"
    if target.exists() and target.stat().st_size > 0:
        return "skipped"

    url = CARD_IMAGE_URL.format(id=card_id)
    response = session.get(url, timeout=timeout)
    if response.status_code == 404:
        return "missing"
    response.raise_for_status()
    if not response.content:
        return "missing"

    tmp = target.with_suffix(".jpg.tmp")
    tmp.write_bytes(response.content)
    tmp.replace(target)
    return "downloaded"


def download_images(card_ids: list[int], image_dir: Path = IMAGE_DIR, workers: int = 12, timeout: int = 20) -> dict:
    image_dir.mkdir(parents=True, exist_ok=True)
    stats = {"downloaded": 0, "skipped": 0, "missing": 0, "failed": 0}
    total = len(card_ids)
    started = time.time()

    def task(card_id: int):
        with requests.Session() as session:
            return card_id, download_one_image(session, card_id, image_dir, timeout)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(task, card_id) for card_id in card_ids]
        for index, future in enumerate(as_completed(futures), start=1):
            try:
                _, status = future.result()
            except Exception:
                status = "failed"
            stats[status] = stats.get(status, 0) + 1

            if index % 200 == 0 or index == total:
                elapsed = max(time.time() - started, 0.1)
                print(
                    f"卡图进度 {index}/{total}, "
                    f"下载 {stats['downloaded']}, 已有 {stats['skipped']}, "
                    f"缺失 {stats['missing']}, 失败 {stats['failed']}, "
                    f"{index / elapsed:.1f}/s"
                )

    return stats


def parse_args():
    parser = argparse.ArgumentParser(description="Update cards.cdb and local card images.")
    parser.add_argument("--db", default=str(DB_PATH), help="output cards.cdb path")
    parser.add_argument("--image-dir", default=str(IMAGE_DIR), help="directory for card images")
    parser.add_argument("--skip-images", action="store_true", help="do not download card images")
    parser.add_argument("--skip-hot", action="store_true", help="do not update hot marker")
    parser.add_argument("--image-limit", type=int, default=0, help="download only first N images for testing")
    parser.add_argument("--workers", type=int, default=12, help="parallel image download workers")
    parser.add_argument("--force", action="store_true", help="ignore saved MD5 and rebuild database")
    return parser.parse_args()


def main():
    args = parse_args()
    db_path = Path(args.db).resolve()
    image_dir = Path(args.image_dir).resolve()
    md5_path = db_path.with_name(db_path.name + ".md5")

    remote_md5 = fetch_remote_md5()
    local_md5 = read_local_md5(md5_path)
    cards = None
    db_current = db_path.exists() and local_md5 == remote_md5 and not args.force

    if db_current:
        print(f"卡片数据未变化: {remote_md5}")
    else:
        zip_content = fetch_cards_zip()
        cards = parse_cards_json(zip_content, remote_md5)
        count = create_database(cards, db_path)
        write_local_md5(remote_md5, md5_path)
        print(f"写入数据库: {db_path} ({count} 张卡), MD5={remote_md5}")

    if not args.skip_hot:
        hot_count = mark_hot_cards(db_path)
        print(f"热门卡标记: {hot_count} 张")

    if args.skip_images:
        print("跳过卡图下载")
        return

    if cards is None:
        card_ids = load_card_ids_from_db(db_path)
    else:
        card_ids = sorted({int_value(card.get("id")) for card in cards.values() if isinstance(card, dict)})
        card_ids = [card_id for card_id in card_ids if card_id > 0]
    if args.image_limit > 0:
        card_ids = card_ids[: args.image_limit]

    print(f"开始下载卡图: {image_dir} ({len(card_ids)} 张)")
    stats = download_images(card_ids, image_dir, max(args.workers, 1))
    print(f"卡图完成: {stats}")


if __name__ == "__main__":
    main()
