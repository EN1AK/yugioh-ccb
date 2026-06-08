#!/usr/bin/env python3
"""Update local card database, extra card metadata, and card images."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
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
CATEGORY_CDB_URL = "https://cdn02.moecube.com:444/ygopro-database/zh-CN/cards.cdb"
STRINGS_CONF_URL = "https://cdn02.moecube.com:444/ygopro-database/zh-CN/strings.conf"
CARD_DETAIL_URL = "https://ygocdb.com/api/v0/card/{id}?show=all"
SUPER_PRE_IMAGE_URL = "https://cdntx.moecube.com/ygopro-super-pre/data/pics/{id}.jpg"
MOMOBAKO_IMAGE_URL = "https://cdn.233.momobako.com/ygopro/pics/{id}.jpg"
CARD_IMAGE_URLS = [
    SUPER_PRE_IMAGE_URL,
    MOMOBAKO_IMAGE_URL,
]
HOT_API_URL = "https://sapi.moecube.com:444/ygopro/analytics/single/type"
HOT_API_PARAMS = {
    "type": "month",
    "lang": "cn",
    "extra": "name",
    "source": "mycard-athletic",
}

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = ROOT / "asset"
DATA_DIR = Path(os.environ.get("DATA_DIR", DEFAULT_DATA_DIR))
DB_PATH = DATA_DIR / "cards.cdb"
MD5_PATH = DATA_DIR / "cards.cdb.md5"
IMAGE_DIR = Path(os.environ.get("IMAGE_DIR", ROOT / "static" / "card"))
STRINGS_CONF_PATH = DATA_DIR / "strings.conf"


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


def collect_names(card: dict) -> list[str]:
    names = []
    for key in ("cn_name", "sc_name", "md_name", "nwbbs_n", "cnocg_n", "jp_name", "en_name"):
        value = card.get(key)
        if isinstance(value, str):
            value = value.strip()
            if value and value not in names:
                names.append(value)
    text = card.get("text")
    if isinstance(text, dict):
        for key in ("name", "title"):
            value = text.get(key)
            if isinstance(value, str):
                value = value.strip()
                if value and value not in names:
                    names.append(value)
    return names or [pick_name(card)]


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


def fetch_category_map(url: str = CATEGORY_CDB_URL) -> dict[int, int]:
    print(f"下载效果标签数据: {url}")
    response = requests.get(url, timeout=60)
    response.raise_for_status()

    fd, tmp_name = tempfile.mkstemp(prefix="category-", suffix=".cdb", dir=str(ROOT))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_bytes(response.content)
        conn = sqlite3.connect(str(tmp_path))
        cur = conn.cursor()
        cur.execute("SELECT id, category FROM datas;")
        categories = {int(card_id): int(category or 0) for card_id, category in cur.fetchall()}
        conn.close()
    finally:
        tmp_path.unlink(missing_ok=True)

    return categories


def fetch_strings_conf(url: str = STRINGS_CONF_URL, path: Path = STRINGS_CONF_PATH):
    print(f"下载效果标签名称: {url}")
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    tmp_path = path.with_suffix(".conf.tmp")
    tmp_path.write_bytes(response.content)
    tmp_path.replace(path)


def iter_card_rows(cards: dict, category_map: dict[int, int] | None = None):
    category_map = category_map or {}
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
            "category": category_map.get(card_id, int_value(data.get("category"))),
            "name": pick_name(card),
            "desc": (card.get("text") or {}).get("desc", "") if isinstance(card.get("text"), dict) else "",
        }


def iter_card_name_rows(cards: dict):
    for card in cards.values():
        if not isinstance(card, dict):
            continue
        card_id = int_value(card.get("id"))
        if card_id <= 0:
            continue
        for name in collect_names(card):
            yield {"id": card_id, "name": name}


def create_database(cards: dict, db_path: Path = DB_PATH, category_map: dict[int, int] | None = None) -> int:
    rows = list(iter_card_rows(cards, category_map))
    name_rows = list(iter_card_name_rows(cards))
    if not rows:
        raise RuntimeError("没有可写入数据库的卡片数据")

    existing_meta = load_existing_card_meta(db_path)
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
        cur.execute(
            """
            CREATE TABLE card_meta (
                id INTEGER PRIMARY KEY,
                first_jp_release TEXT DEFAULT '',
                jp_packs TEXT DEFAULT '[]'
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE card_names (
                id INTEGER,
                name TEXT,
                PRIMARY KEY (id, name)
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
        cur.executemany(
            """
            INSERT OR IGNORE INTO card_names
                (id, name)
            VALUES
                (:id, :name);
            """,
            name_rows,
        )
        cur.executemany(
            "INSERT OR IGNORE INTO card_meta (id, first_jp_release, jp_packs) VALUES (:id, '', '[]');",
            rows,
        )
        row_ids = {row["id"] for row in rows}
        preserved_meta = [
            (card_id, first_date, packs)
            for card_id, (first_date, packs) in existing_meta.items()
            if card_id in row_ids
        ]
        if preserved_meta:
            cur.executemany(
                """
                UPDATE card_meta
                   SET first_jp_release = ?,
                       jp_packs = ?
                 WHERE id = ?;
                """,
                [(first_date, packs, card_id) for card_id, first_date, packs in preserved_meta],
            )
        cur.execute("CREATE INDEX idx_texts_name ON texts(name);")
        cur.execute("CREATE INDEX idx_card_names_name ON card_names(name);")
        cur.execute("CREATE INDEX idx_card_names_id ON card_names(id);")
        conn.commit()
        conn.close()

        backup_existing_database(db_path)
        tmp_path.replace(db_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return len(rows)


def backup_existing_database(db_path: Path):
    if not db_path.exists():
        return
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.{timestamp}.bak")
    shutil.copy2(db_path, backup_path)
    print(f"已备份旧数据库: {backup_path}")


def load_existing_card_meta(db_path: Path) -> dict[int, tuple[str, str]]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, COALESCE(first_jp_release, ''), COALESCE(jp_packs, '[]')
              FROM card_meta
             WHERE COALESCE(first_jp_release, '') != ''
                OR COALESCE(jp_packs, '[]') != '[]';
            """
        )
        rows = {}
        for card_id, first_date, packs in cur.fetchall():
            raw_packs = packs or "[]"
            try:
                parsed_packs = json.loads(raw_packs)
            except json.JSONDecodeError:
                parsed_packs = []
            if isinstance(parsed_packs, list):
                raw_packs = json.dumps(normalize_pack_labels(parsed_packs), ensure_ascii=False)
            rows[int(card_id)] = (first_date or "", raw_packs)
    except sqlite3.OperationalError:
        rows = {}
    finally:
        conn.close()
    return rows


def ensure_card_meta_table(db_path: Path = DB_PATH):
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS card_meta (
            id INTEGER PRIMARY KEY,
            first_jp_release TEXT DEFAULT '',
            jp_packs TEXT DEFAULT '[]'
        );
        """
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO card_meta (id, first_jp_release, jp_packs)
        SELECT id, '', '[]' FROM datas;
        """
    )
    conn.commit()
    conn.close()


def load_card_ids_from_db(db_path: Path = DB_PATH) -> list[int]:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT id FROM datas ORDER BY id;")
    ids = [int(row[0]) for row in cur.fetchall()]
    conn.close()
    return ids


def load_missing_pack_info_ids(db_path: Path = DB_PATH) -> list[int]:
    ensure_card_meta_table(db_path)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        """
        SELECT d.id
          FROM datas d
          LEFT JOIN card_meta m ON m.id = d.id
         WHERE m.id IS NULL
            OR (COALESCE(m.first_jp_release, '') = '' AND COALESCE(m.jp_packs, '[]') = '[]')
         ORDER BY d.id;
        """
    )
    ids = [int(row[0]) for row in cur.fetchall()]
    conn.close()
    return ids


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


def normalize_jp_packs(payload: dict) -> tuple[str, list[str]]:
    packs = payload.get("jppacks") if isinstance(payload, dict) else None
    if not isinstance(packs, list):
        return "", []

    normalized = []
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        name = str(pack.get("name") or "").strip()
        setid = str(pack.get("setid") or "").strip()
        date = str(pack.get("date") or "").strip()
        if not name and not setid:
            continue
        setid = normalize_pack_setid(setid)
        label = f"{name} ({setid})" if setid else name
        normalized.append({"date": date, "label": label})

    normalized.sort(key=lambda item: item["date"] or "9999-99-99")
    first_date = next((item["date"] for item in normalized if item["date"]), "")

    seen = set()
    labels = []
    for item in normalized:
        label = item["label"]
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return first_date, labels


def normalize_pack_setid(setid: str) -> str:
    return setid.split("-", 1)[0].strip() if "-" in setid else setid.strip()


def normalize_pack_label(label: str) -> str:
    label = label.strip()
    if not label.endswith(")"):
        return label

    open_index = label.rfind("(")
    if open_index < 0:
        return label

    prefix = label[:open_index].rstrip()
    suffix = label[open_index + 1 : -1].strip()
    if "-" not in suffix:
        return label

    pack_code = normalize_pack_setid(suffix)
    return f"{prefix} ({pack_code})" if pack_code else prefix


def normalize_pack_labels(labels: list) -> list[str]:
    return [normalize_pack_label(label) for label in labels if isinstance(label, str)]


def normalize_existing_pack_info(db_path: Path = DB_PATH) -> int:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, jp_packs FROM card_meta WHERE COALESCE(jp_packs, '[]') != '[]';")
    except sqlite3.OperationalError:
        conn.close()
        return 0

    rows = []
    for card_id, raw_packs in cur.fetchall():
        try:
            packs = json.loads(raw_packs or "[]")
        except json.JSONDecodeError:
            continue
        if not isinstance(packs, list):
            continue
        normalized = normalize_pack_labels(packs)
        new_raw = json.dumps(normalized, ensure_ascii=False)
        if new_raw != raw_packs:
            rows.append((new_raw, card_id))

    if rows:
        cur.executemany("UPDATE card_meta SET jp_packs = ? WHERE id = ?;", rows)
        conn.commit()
    conn.close()
    return len(rows)


def fetch_one_pack_info(session: requests.Session, card_id: int, timeout: int) -> tuple[int, str, list[str]]:
    url = CARD_DETAIL_URL.format(id=card_id)
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return (card_id, *normalize_jp_packs(response.json()))


def update_pack_info(db_path: Path, card_ids: list[int], workers: int = 8, timeout: int = 20) -> dict:
    ensure_card_meta_table(db_path)
    stats = {"updated": 0, "failed": 0}
    total = len(card_ids)
    started = time.time()

    def task(card_id: int):
        with requests.Session() as session:
            return fetch_one_pack_info(session, card_id, timeout)

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(task, card_id) for card_id in card_ids]
        for index, future in enumerate(as_completed(futures), start=1):
            try:
                card_id, first_date, labels = future.result()
                pack_payload = labels if labels else None
                rows.append((card_id, first_date, json.dumps(pack_payload, ensure_ascii=False)))
                stats["updated"] += 1
            except Exception:
                stats["failed"] += 1

            if len(rows) >= 100:
                save_pack_info_rows(db_path, rows)
                rows.clear()

            if index % 200 == 0 or index == total:
                elapsed = max(time.time() - started, 0.1)
                print(
                    f"卡包信息进度 {index}/{total}, "
                    f"更新 {stats['updated']}, 失败 {stats['failed']}, "
                    f"{index / elapsed:.1f}/s"
                )

    if rows:
        save_pack_info_rows(db_path, rows)

    return stats


def save_pack_info_rows(db_path: Path, rows: list[tuple[int, str, str]]):
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.executemany(
        """
        INSERT INTO card_meta (id, first_jp_release, jp_packs)
        VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            first_jp_release = excluded.first_jp_release,
            jp_packs = excluded.jp_packs;
        """,
        rows,
    )
    conn.commit()
    conn.close()


def download_one_image(session: requests.Session, card_id: int, image_dir: Path, timeout: int) -> str:
    target = image_dir / f"{card_id}.jpg"
    has_local_image = target.exists() and target.stat().st_size > 0

    failed = False
    content = None
    used_super_pre = False
    for url_template in CARD_IMAGE_URLS:
        url = url_template.format(id=card_id)
        try:
            response = session.get(url, timeout=timeout)
            if response.status_code == 404:
                continue
            response.raise_for_status()
        except requests.RequestException:
            failed = True
            continue
        if response.content:
            content = response.content
            used_super_pre = url_template == SUPER_PRE_IMAGE_URL
            break

    if has_local_image and not used_super_pre:
        return "skipped"

    if not content:
        return "failed" if failed else "missing"

    tmp = target.with_suffix(".jpg.tmp")
    tmp.write_bytes(content)
    tmp.replace(target)
    return "updated" if has_local_image else "downloaded"


def download_images(card_ids: list[int], image_dir: Path = IMAGE_DIR, workers: int = 12, timeout: int = 20) -> dict:
    image_dir.mkdir(parents=True, exist_ok=True)
    stats = {"downloaded": 0, "updated": 0, "skipped": 0, "missing": 0, "failed": 0}
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
    parser = argparse.ArgumentParser(description="Update cards.cdb, pack metadata, and local card images.")
    parser.add_argument("--db", default=str(DB_PATH), help="output cards.cdb path")
    parser.add_argument("--image-dir", default=str(IMAGE_DIR), help="directory for card images")
    parser.add_argument("--skip-hot", action="store_true", help="do not update hot marker")
    parser.add_argument("--skip-category", action="store_true", help="do not download legacy cards.cdb for category tags")
    parser.add_argument("--skip-pack-info", action="store_true", help="do not fetch jppacks metadata")
    parser.add_argument("--skip-images", action="store_true", help="do not download card images")
    parser.add_argument("--normalize-pack-info", action="store_true", help="normalize stored jppacks labels and exit")
    parser.add_argument("--pack-limit", type=int, default=0, help="fetch pack info for first N missing cards")
    parser.add_argument("--image-id", type=int, action="append", default=[], help="download only the specified card id; can be used more than once")
    parser.add_argument("--image-limit", type=int, default=0, help="download only first N images for testing")
    parser.add_argument("--pack-workers", type=int, default=8, help="parallel pack-info workers")
    parser.add_argument("--workers", type=int, default=12, help="parallel image download workers")
    parser.add_argument("--force", action="store_true", help="ignore saved MD5 and rebuild database")
    return parser.parse_args()


def main():
    args = parse_args()
    db_path = Path(args.db).resolve()
    image_dir = Path(args.image_dir).resolve()
    md5_path = db_path.with_name(db_path.name + ".md5")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    if args.normalize_pack_info:
        count = normalize_existing_pack_info(db_path)
        print(f"normalized pack info rows: {count}")
        return

    remote_md5 = fetch_remote_md5()
    local_md5 = read_local_md5(md5_path)
    cards = None
    db_current = db_path.exists() and local_md5 == remote_md5 and not args.force

    if db_current:
        print(f"卡片数据未变化: {remote_md5}")
        ensure_card_meta_table(db_path)
        if not args.skip_category and not STRINGS_CONF_PATH.exists():
            try:
                fetch_strings_conf()
            except Exception as exc:
                print(f"效果标签名称更新失败，使用内置标签名: {exc}", file=sys.stderr)
    else:
        zip_content = fetch_cards_zip()
        cards = parse_cards_json(zip_content, remote_md5)
        category_map = {}
        if not args.skip_category:
            try:
                category_map = fetch_category_map()
                fetch_strings_conf()
                print(f"效果标签数据: {len(category_map)} 张")
            except Exception as exc:
                print(f"效果标签数据更新失败，category 将使用 0: {exc}", file=sys.stderr)
        count = create_database(cards, db_path, category_map)
        write_local_md5(remote_md5, md5_path)
        print(f"写入数据库: {db_path} ({count} 张卡), MD5={remote_md5}")

    if not args.skip_hot:
        hot_count = mark_hot_cards(db_path)
        print(f"热门卡标记: {hot_count} 张")

    if not args.skip_pack_info:
        pack_ids = load_missing_pack_info_ids(db_path)
        if args.pack_limit > 0:
            pack_ids = pack_ids[: args.pack_limit]
        if pack_ids:
            print(f"开始更新日文收录卡包信息: {len(pack_ids)} 张")
            stats = update_pack_info(db_path, pack_ids, max(args.pack_workers, 1))
            print(f"卡包信息完成: {stats}")
        else:
            print("日文收录卡包信息已是最新")

    if args.skip_images:
        print("跳过卡图下载")
        return

    if cards is None:
        card_ids = load_card_ids_from_db(db_path)
    else:
        card_ids = sorted({int_value(card.get("id")) for card in cards.values() if isinstance(card, dict)})
        card_ids = [card_id for card_id in card_ids if card_id > 0]
    if args.image_id:
        requested_ids = {card_id for card_id in args.image_id if card_id > 0}
        card_ids = [card_id for card_id in card_ids if card_id in requested_ids]
    if args.image_limit > 0:
        card_ids = card_ids[: args.image_limit]

    print(f"开始下载卡图: {image_dir} ({len(card_ids)} 张)")
    stats = download_images(card_ids, image_dir, max(args.workers, 1))
    print(f"卡图完成: {stats}")


if __name__ == "__main__":
    main()
