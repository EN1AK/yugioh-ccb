import sqlite3
import pandas as pd
import numbers
from pathlib import Path
import sys
from map import RACE_MAP, TYPE_MAP, CATEGORY_TAGS, TYPE_LINK, LINK_MARKERS, SETNAME_MAP, ATTR_MAP, TYPE_PENDULUM,TYPE_MONSTER


def parse_flags(value, mapping):
    return [name for bit, name in mapping.items() if value & bit]


def parse_category(cat):
    return [CATEGORY_TAGS[1100 + i] for i in range(64) if (cat >> i) & 1 and (1100 + i) in CATEGORY_TAGS]


def parse_setcode(setcode, name_map):
    # 1. 转成大写十六进制字符串
    hex_str = f"{setcode:X}"
    # 2. 左侧补零，使长度成为 4 的倍数
    pad_len = (-len(hex_str)) % 4
    if pad_len:
        hex_str = hex_str.zfill(len(hex_str) + pad_len)
    # 3. 每 4 位一组
    names = []
    for i in range(0, len(hex_str), 4):
        segment = hex_str[i:i + 4]
        # 全 0 的段跳过
        if segment == "0000":
            continue
        code = int(segment, 16)
        if code in name_map:
            names.append(name_map[code])
    return names


def extract_arrows(def_value):
    """
    从 link_marker 的整数值中提取出 所有 生效的箭头符号，返回一个列表。
    """
    return [sym for bit, sym in LINK_MARKERS.items() if def_value & bit]


def load_card_database(path: str = None) -> pd.DataFrame:
    """
    加载 cards.cdb 里的 datas 和 texts 两张表，
    合并、去重、按 id 排序后返回一个 DataFrame。

    如果不传入 path，则自动：
      · 在 PyInstaller 打包后的环境中，从 sys._MEIPASS 找到临时目录里的 cards.cdb
      · 否则从当前脚本同级目录下加载 cards.cdb
    """
    # 1. 自动定位数据库文件
    if path is None:
        # PyInstaller 打包后会把数据放到 _MEIPASS 里
        base = getattr(sys, "_MEIPASS", None)
        if base is None:
            # 普通脚本运行，数据库和脚本在同一个目录
            base = Path(__file__).parent
        else:
            # 打包执行时，_MEIPASS 已经是一个 str 临时目录
            base = Path(base)
        db_file = base / "cards.cdb"
    else:
        db_file = Path(path)

    if not db_file.exists():
        raise FileNotFoundError(f"找不到数据库文件：{db_file}")

    # 2. 连接并读取表
    conn = sqlite3.connect(str(db_file))
    datas = pd.read_sql_query(
        "SELECT id, type, atk, def, level, race, attribute, category, hot, setcode FROM datas",
        conn, index_col="id"
    )
    texts = pd.read_sql_query(
        "SELECT id, name FROM texts",
        conn, index_col="id"
    )
    conn.close()

    # 3. 合并去重并返回
    df = datas.join(texts, how="inner").reset_index()
    df = (
        df
        .sort_values("id")
        .drop_duplicates(subset="name", keep="first")
        .set_index("id")
    )
    return df


def card_to_tags(row):
    type_names = parse_flags(row["type"], TYPE_MAP)
    is_link = bool(row["type"] & TYPE_LINK)
    is_pendulum = bool(row["type"] & TYPE_PENDULUM)
    is_monster = bool(row["type"] & TYPE_MONSTER)
    if not is_monster:
        atk_val = ""
        def_val = ""
        level = ""
        scale = ""
        attr = ""
        race = ""
    else:
        # 怪兽卡才处理 -2 → “？”
        atk_val = "？" if row["atk"] == -2 else row["atk"]
        # 链接怪兽没有守备，其它怪兽按 -2 转换
        if is_link:
            def_val = ""
        else:
            def_val = "？" if row["def"] == -2 else row["def"]
        # 等级/阶级
        level = row["level"] & 0xFF
        # 刻度只有灵摆怪兽才有
        scale = (row["level"] >> 24) & 0xFF if is_pendulum else ""
        attr = ATTR_MAP.get(row["attribute"], f"0x{row['attribute']:X}")
        race = RACE_MAP.get(row["race"], f"0x{row['race']:X}")
    arrows = extract_arrows(row["def"]) if is_link else []
    return {
        "卡名": row["name"],
        "攻击": atk_val,
        "守备": def_val,
        "等级/阶级": level,
        "箭头": arrows,
        "刻度": scale,
        "类型": type_names,
        "属性": attr,
        "种族": race,
        "效果标签": parse_category(row["category"]),
        "系列": parse_setcode(row["setcode"], SETNAME_MAP),
    }


def compare_tags(guess_tags, answer_tags):
    def cmp(key, val1, val2):
        if (val1 == "" or val1 is None) and (val2 == "" or val2 is None):
            return '<span class="tag tag-gray">—</span>'
        if (val1 == "" or val1 is None) and (val2 != "" or val2 is not None):
            return '<span class="tag tag-gray">—</span>'
        if (val1 != "" or val1 is not None) and (val2 == "" or val2 is None):
            num = val1
            return f'<span class="tag tag-gray">{num}</span>'



        if key == "箭头":
            pills = []
            # 对八个方向都展示一个小标签
            for bit, sym in LINK_MARKERS.items():
                if sym in val1:
                    # 猜的里有
                    cls = "tag-green" if sym in val2 else "tag-red"
                else:
                    # 猜的里没有
                    cls = "tag-gray"
                pills.append(f'<span class="tag {cls}">{sym}</span>')
            return " ".join(pills)
        # 数值型字段：攻击、守备、等级、刻度
        if isinstance(val1, numbers.Number):
            diff = abs(val1 - val2)
            # 先判断完全相等
            if diff == 0:
                cls = "tag-green"
            else:
                if key in ("攻击", "守备"):
                    if diff <= 500:
                        cls = "tag-yellow"
                    else:
                        cls = "tag-gray"
                elif key in ("等级/阶级", "刻度"):
                    if diff <= 2:
                        cls = "tag-yellow"
                    else:
                        cls = "tag-gray"
                else:
                    cls = "tag-gray"
            # 箭头
            arrow = "" if diff == 0 else ("↑" if val1 < val2 else "↓")
            return f'<span class="tag {cls}">{val1}{arrow}</span>'

        # 列表型字段：如 类型、效果标签……
        elif isinstance(val1, list):
            pills = []
            for t in val1:
                # 猜的 tag 在目标里才 green，否则 red
                cls = "tag-green" if t in val2 else "tag-gray"
                pills.append(f'<span class="tag {cls}">{t}</span>')
            return " ".join(pills) or '<span class="tag tag-gray">—</span>'

        # 其它（字符串等）完全匹配才 green，否则 gray
        else:
            cls = "tag-green" if val1 == val2 else "tag-gray    "
            return f'<span class="tag {cls}">{val1}</span>'

    return {
        key: cmp(key, guess_tags[key], answer_tags[key])
        for key in guess_tags
    }
