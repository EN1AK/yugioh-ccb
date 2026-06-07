import os
import random
import sys
import time
import uuid
import json
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response
from flask_session import Session
import redis

from data_utils import load_card_database, card_to_tags, compare_tags

base_path = getattr(sys, "_MEIPASS", os.path.dirname(__file__))
template_folder = os.path.join(base_path, "templates")
app = Flask(__name__, template_folder=template_folder)
db = None
target_row = None
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

db = load_card_database()
rooms = {}
SITE_URL = os.environ.get("SITE_URL", "https://www.ygoccb.site").rstrip("/")
FEEDBACK_PATH = os.environ.get("FEEDBACK_PATH", os.path.join(os.environ.get("DATA_DIR", os.path.join(base_path, "asset")), "feedback.jsonl"))
DEFAULT_ROOM_DURATION_SECONDS = 5 * 60
MIN_ROOM_DURATION_SECONDS = 60
MAX_ROOM_DURATION_SECONDS = 30 * 60
MAX_ROOM_PLAYERS = 4
ROOM_SCORE_BY_RANK = [4, 3, 2, 1]
NEXT_ROUND_DELAY_SECONDS = 5

redis_url = os.getenv("REDIS_URL", None)
if redis_url:
    app.config['SESSION_TYPE'] = 'redis'
    app.config['SESSION_REDIS'] = redis.from_url(redis_url)
    app.config['SESSION_PERMANENT'] = False
    app.config['SESSION_USE_SIGNER'] = True
    app.config['SESSION_KEY_PREFIX'] = 'session:'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)
    Session(app)
    print("Redis session 已启用")
else:
    print("未设置 REDIS_URL，使用默认 cookie session")


def filter_db(mode):
    """
    mode: 'monster' | 'spell' | 'trap' | 'hot' | 'all'
    """
    if mode == 'monster':
        mask = ((db['type'] & 0x1) > 0) & ((db['type'] & 0x10) == 0)
        return db[mask]
    if mode == 'spell':
        return db[(db['type'] & 0x2) > 0]
    if mode == 'trap':
        return db[(db['type'] & 0x4) > 0]
    if mode == 'hot':
        mask = ((db['type'] & 0x1) > 0) & ((db['type'] & 0x10) == 0) & (db['hot'] == 1)
        return db[mask]
    # all
    return db


def cleanup_rooms():
    now = time.time()
    expired = [
        room_id for room_id, room in rooms.items()
        if now - room.get("created_at", now) > 2 * 60 * 60
    ]
    for room_id in expired:
        rooms.pop(room_id, None)


def create_room_id():
    while True:
        room_id = uuid.uuid4().hex[:6].upper()
        if room_id not in rooms:
            return room_id


def get_or_create_player_id():
    player_id = session.get("multi_player_id")
    if not player_id:
        player_id = uuid.uuid4().hex
        session["multi_player_id"] = player_id
    return player_id


def room_remaining(room):
    if room.get("status") != "playing" or not room.get("deadline"):
        return int(room.get("duration", DEFAULT_ROOM_DURATION_SECONDS))
    return max(0, int(room["deadline"] - time.time()))


def room_next_round_remaining(room):
    if room.get("status") != "revealing" or not room.get("next_round_at"):
        return None
    return max(0, int(room["next_round_at"] - time.time()))


def room_is_revealing(room):
    return room.get("status") == "revealing"


def room_answer(room):
    if not room.get("target_id"):
        return None
    return db.loc[room["target_id"]]["name"]


def join_room(room, name):
    player_id = get_or_create_player_id()
    players = room["players"]
    if player_id in players:
        if name:
            players[player_id]["name"] = name
        return player_id, None

    if len(players) >= MAX_ROOM_PLAYERS:
        return None, "房间已满，最多 4 人。"

    display_name = (name or "").strip() or f"玩家{len(players) + 1}"
    players[player_id] = {
        "id": player_id,
        "name": display_name[:20],
        "score": 0,
        "rank": None,
        "ready": False,
        "is_owner": len(players) == 0,
        "surrendered": False,
        "surrendered_round": None,
        "round": room.get("round", 0),
        "history": [],
        "feedback": None,
    }
    return player_id, None


def parse_room_duration(raw_value):
    try:
        minutes = int(raw_value)
    except (TypeError, ValueError):
        minutes = 5
    seconds = minutes * 60
    return max(MIN_ROOM_DURATION_SECONDS, min(MAX_ROOM_DURATION_SECONDS, seconds))


def room_ready_count(room):
    return sum(1 for p in room["players"].values() if p.get("ready"))


def room_can_start(room):
    players = list(room["players"].values())
    if not players:
        return False
    return all(p.get("is_owner") or p.get("ready") for p in players)


def start_next_round(room, duration_seconds=None):
    pool = filter_db(room["mode"])
    if pool.empty:
        return "题库为空，无法开始。"
    now = time.time()
    duration = duration_seconds or room.get("duration", DEFAULT_ROOM_DURATION_SECONDS)
    room["target_id"] = int(pool.sample(1).index[0])
    room["duration"] = duration
    room["deadline"] = now + duration
    room["started_at"] = now
    room["status"] = "playing"
    room["winners"] = []
    room["next_round_at"] = None
    room["reveal_reason"] = None
    room["round"] = int(room.get("round", 0)) + 1
    room["events"].append({
        "name": "系统",
        "message": f"第 {room['round']} 题开始，限时 {duration // 60} 分钟",
        "time": int(now),
    })
    for p in room["players"].values():
        p["history"] = []
        p["feedback"] = None
        p["rank"] = None
        p["surrendered"] = False
        p["surrendered_round"] = None
        p["round"] = room["round"]
    return None


def player_surrendered_this_round(room, player):
    return player.get("surrendered_round") == room.get("round")


def sync_player_round_state(room):
    if room.get("status") != "playing":
        return
    current_round = room.get("round", 0)
    for p in room["players"].values():
        if p.get("round") == current_round:
            p["surrendered"] = player_surrendered_this_round(room, p)
            continue
        p["history"] = []
        p["feedback"] = None
        p["rank"] = None
        p["surrendered"] = False
        p["surrendered_round"] = None
        p["round"] = current_round


def reveal_round(room, reason):
    if room.get("status") != "playing":
        return
    now = time.time()
    room["status"] = "revealing"
    room["reveal_reason"] = reason
    room["next_round_at"] = now + NEXT_ROUND_DELAY_SECONDS
    room["events"].append({
        "name": "系统",
        "message": f"本题揭晓，答案是 {room_answer(room)}，{NEXT_ROUND_DELAY_SECONDS} 秒后进入下一题",
        "time": int(now),
    })


def room_active_players(room):
    return [
        p for p in room["players"].values()
        if p.get("rank") is None and not player_surrendered_this_round(room, p)
    ]


def update_room_round(room):
    if room.get("status") == "revealing":
        next_round_at = room.get("next_round_at")
        if next_round_at and time.time() >= next_round_at:
            start_next_round(room)
            sync_player_round_state(room)
        return
    if room.get("status") != "playing":
        return
    sync_player_round_state(room)
    if room_remaining(room) <= 0:
        reveal_round(room, "timeout")
        return
    if not room_active_players(room):
        reveal_round(room, "completed")


def start_room(room, duration_seconds):
    return start_next_round(room, duration_seconds)


def room_scoreboard(room):
    return sorted(
        room["players"].values(),
        key=lambda p: (p["rank"] is None, p["rank"] or 99, -p["score"], p["name"])
    )


@app.route("/robots.txt")
def robots_txt():
    body = f"""User-agent: *
Allow: /
Sitemap: {SITE_URL}/sitemap.xml
"""
    return Response(body, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    urls = [
        ("", "daily", "1.0"),
        ("/game", "weekly", "0.7"),
    ]
    items = "\n".join(
        f"""  <url>
    <loc>{SITE_URL}{path}</loc>
    <changefreq>{changefreq}</changefreq>
    <priority>{priority}</priority>
  </url>"""
        for path, changefreq, priority in urls
    )
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{items}
</urlset>
"""
    return Response(body, mimetype="application/xml")


@app.route("/feedback", methods=["POST"])
def feedback():
    payload = request.get_json(silent=True) or request.form
    message = str(payload.get("message", "")).strip()
    contact = str(payload.get("contact", "")).strip()
    page = str(payload.get("page", "")).strip()

    if not message:
        return jsonify({"ok": False, "error": "反馈内容不能为空。"}), 400

    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "message": message[:2000],
        "contact": contact[:200],
        "page": page[:500],
        "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
        "user_agent": request.headers.get("User-Agent", "")[:500],
    }

    os.makedirs(os.path.dirname(FEEDBACK_PATH), exist_ok=True)
    with open(FEEDBACK_PATH, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")

    return jsonify({"ok": True})


@app.route("/", methods=["GET", "POST"])
def start():
    """游戏开始前，选择卡牌范围和猜测次数"""
    if request.method == "POST":
        # 1. 读卡片类型
        mode = request.form["mode"]

        # 2. 读猜测次数（range 滑块传回的是字符串）
        try:
            max_attempts = int(request.form.get("attempts", 5))
        except ValueError:
            max_attempts = 5

        if redis_url:
            session.permanent = True
        # 3. 初始化 session
        session.clear()
        session["mode"] = mode
        session["max_attempts"] = max_attempts
        session["guess_count"] = 0
        session["hints_shown"] = []

        # 4. 随机选一个目标卡片 ID
        pool = filter_db(mode)
        session["target_id"] = int(pool.sample(1).index[0])

        return redirect(url_for("game"))

    # GET：渲染 start.html（包含滑块）
    return render_template("start.html")


@app.route("/multiplayer/create", methods=["POST"])
def multiplayer_create():
    cleanup_rooms()
    mode = request.form.get("mode", "hot")
    name = request.form.get("name", "").strip()
    duration = parse_room_duration(request.form.get("duration", 5))
    pool = filter_db(mode)
    if pool.empty:
        return redirect(url_for("start"))

    room_id = create_room_id()
    now = time.time()
    rooms[room_id] = {
        "id": room_id,
        "mode": mode,
        "status": "waiting",
        "target_id": None,
        "duration": duration,
        "started_at": None,
        "created_at": now,
        "deadline": None,
        "next_round_at": None,
        "reveal_reason": None,
        "round": 0,
        "players": {},
        "winners": [],
        "events": [],
    }
    join_room(rooms[room_id], name)
    session["multi_room_id"] = room_id
    return redirect(url_for("multiplayer_room", room_id=room_id))


@app.route("/multiplayer/join", methods=["POST"])
def multiplayer_join_by_code():
    cleanup_rooms()
    room_id = request.form.get("room_id", "").strip().upper()
    name = request.form.get("name", "").strip()
    if not room_id:
        return redirect(url_for("start"))

    room = rooms.get(room_id)
    if room and room.get("status") == "waiting" and name:
        player_id, error = join_room(room, name)
        if not error:
            session["multi_room_id"] = room_id
    return redirect(url_for("multiplayer_room", room_id=room_id))


@app.route("/multiplayer/<room_id>", methods=["GET", "POST"])
def multiplayer_room(room_id):
    cleanup_rooms()
    room_id = room_id.upper()
    room = rooms.get(room_id)
    if not room:
        return render_template("multiplayer.html", room=None, error="房间不存在或已过期。")
    update_room_round(room)

    player_id = session.get("multi_player_id")
    player = room["players"].get(player_id) if player_id else None
    feedback = None
    error = None

    if request.method == "POST":
        action = request.form.get("action", "guess")

        if action == "join":
            if room.get("status") != "waiting":
                error = "游戏已经开始，无法加入。"
            else:
                player_id, error = join_room(room, request.form.get("name", ""))
            if not error:
                session["multi_room_id"] = room_id
                return redirect(url_for("multiplayer_room", room_id=room_id))

        elif action == "ready":
            if not player:
                error = "请先加入房间。"
            elif room.get("status") != "waiting":
                error = "游戏已经开始。"
            else:
                player["ready"] = not bool(player.get("ready"))
                return redirect(url_for("multiplayer_room", room_id=room_id))

        elif action == "start":
            if not player:
                error = "请先加入房间。"
            elif not player.get("is_owner"):
                error = "只有房主可以开始游戏。"
            elif room.get("status") != "waiting":
                error = "游戏已经开始。"
            elif not room_can_start(room):
                error = "还有玩家未准备。"
            else:
                duration = parse_room_duration(request.form.get("duration", room.get("duration", DEFAULT_ROOM_DURATION_SECONDS) // 60))
                error = start_room(room, duration)
                if not error:
                    return redirect(url_for("multiplayer_room", room_id=room_id))

        elif action == "guess":
            if not player:
                error = "请先加入房间。"
            elif room.get("status") != "playing":
                error = "当前不能猜测。"
            elif player["rank"] is not None:
                error = "你已经猜中，等待其他玩家完成。"
            elif player_surrendered_this_round(room, player):
                error = "你已经放弃本题，等待下一题。"
            else:
                guess_id = request.form.get("guess_id")
                guess = None
                if guess_id:
                    try:
                        guess = db.loc[int(guess_id)]
                    except Exception:
                        guess = None
                if guess is None:
                    user_input = request.form.get("guess", "").strip()
                    match = db[db["name"].str.contains(user_input, case=False, na=False, regex=False)]
                    if not match.empty:
                        guess = match.iloc[0]

                if guess is None:
                    feedback = {"error": "未找到有效卡片。"}
                else:
                    target = db.loc[room["target_id"]]
                    compare = compare_tags(card_to_tags(guess), card_to_tags(target))
                    player["history"].append({"guess_name": guess["name"], "compare": compare})

                    if guess.name == target.name:
                        rank = len(room["winners"]) + 1
                        points = ROOM_SCORE_BY_RANK[rank - 1] if rank <= len(ROOM_SCORE_BY_RANK) else 0
                        player["rank"] = rank
                        player["score"] += points
                        player["feedback"] = {"success": f"猜中！第 {rank} 名，获得 {points} 分。"}
                        room["winners"].append(player_id)
                        room["events"].append({
                            "name": player["name"],
                            "message": f"第 {rank} 名猜中，+{points} 分",
                            "time": int(time.time()),
                        })
                        feedback = player["feedback"]
                        update_room_round(room)
                        return redirect(url_for("multiplayer_room", room_id=room_id))
                    else:
                        feedback = {"compare": compare, "guess_name": guess["name"]}
                        player["feedback"] = feedback
                        return redirect(url_for("multiplayer_room", room_id=room_id))

        elif action == "surrender":
            if not player:
                error = "请先加入房间。"
            elif room.get("status") != "playing":
                error = "当前不能放弃。"
            elif player["rank"] is not None:
                error = "你已经猜中，不需要放弃。"
            elif player_surrendered_this_round(room, player):
                error = "你已经放弃本题。"
            else:
                player["surrendered"] = True
                player["surrendered_round"] = room.get("round")
                player["round"] = room.get("round")
                player["feedback"] = {"success": "已放弃本题，等待其他玩家。"}
                feedback = player["feedback"]
                room["events"].append({
                    "name": player["name"],
                    "message": "放弃本题",
                    "time": int(time.time()),
                })
                update_room_round(room)
                return redirect(url_for("multiplayer_room", room_id=room_id))

    update_room_round(room)
    player_id = session.get("multi_player_id")
    player = room["players"].get(player_id) if player_id else None
    revealing = room_is_revealing(room)
    target = db.loc[room["target_id"]] if room.get("target_id") else None
    return render_template(
        "multiplayer.html",
        room=room,
        player=player,
        error=error,
        feedback=feedback or (player or {}).get("feedback"),
        history=(player or {}).get("history", []),
        scoreboard=room_scoreboard(room),
        remaining=room_remaining(room),
        finished=revealing,
        revealing=revealing,
        answer=target["name"] if revealing and target is not None else None,
        next_round_in=room_next_round_remaining(room),
        max_players=MAX_ROOM_PLAYERS,
        ready_count=room_ready_count(room),
        can_start=room_can_start(room),
    )


@app.route("/multiplayer/<room_id>/state")
def multiplayer_state(room_id):
    room = rooms.get(room_id.upper())
    if not room:
        return jsonify({"exists": False})
    update_room_round(room)
    revealing = room_is_revealing(room)
    target = db.loc[room["target_id"]] if room.get("target_id") else None
    next_round_in = room_next_round_remaining(room)
    player_id = session.get("multi_player_id")
    player = room["players"].get(player_id) if player_id else None
    return jsonify({
        "exists": True,
        "status": room.get("status"),
        "remaining": next_round_in if revealing else room_remaining(room),
        "finished": revealing,
        "revealing": revealing,
        "answer": target["name"] if revealing and target is not None else None,
        "round": room.get("round", 0),
        "next_round_in": next_round_in,
        "current_player": {
            "surrendered": player_surrendered_this_round(room, player),
            "rank": player.get("rank"),
            "round": player.get("round", room.get("round", 0)),
        } if player else None,
        "ready_count": room_ready_count(room),
        "can_start": room_can_start(room),
        "scoreboard": [
            {
                "name": p["name"],
                "score": p["score"],
                "rank": p["rank"],
                "ready": p.get("ready", False),
                "is_owner": p.get("is_owner", False),
                "surrendered": player_surrendered_this_round(room, p),
            }
            for p in room_scoreboard(room)
        ],
        "events": room["events"][-8:],
    })


@app.route("/game", methods=["GET", "POST"])
def game():
    feedback = None
    mode = session.get('mode')
    if not mode:
        return redirect(url_for("start"))

    if 'target_id' not in session:
        pool = filter_db(mode)
        session['target_id'] = int(pool.sample(1).index[0])
        session['history'] = []
        session['hints'] = []
        session['hinted_tags'] = []
    max_attempts = session.get('max_attempts', 5)
    guess_count = session.get('guess_count', 0)

    filtered = filter_db(mode)
    target = db.loc[session['target_id']]

    # 本局历史记录和提示
    history = session.get('history', [])
    hints = session.get('hints', [])
    hinted_tags = session.get('hinted_tags', [])

    def hint_opportunities():
        return max(0, len(history) // 3 - len(hinted_tags))

    def reveal_tag_hint():
        target_tags = list(card_to_tags(target)["效果标签"])
        remaining = [tag for tag in target_tags if tag not in hinted_tags]
        if not remaining:
            return {"error": "这张卡没有更多可提示的效果标签。", "hints": hints}

        tag_hint = random.choice(remaining)
        hinted_tags.append(tag_hint)
        hints.append(f"提示：目标卡有效果标签 “{tag_hint}”")
        session['hints'] = hints
        session['hinted_tags'] = hinted_tags
        return {"success": "已给出一个正确标签提示。", "hints": hints}

    if request.method == "POST":
        action = request.form.get("action", "guess")

        if action == "change_mode":
            new_mode = request.form.get("mode")
            session['mode'] = new_mode
            # 直接把上一行 target_id 删掉，触发上面自动重置
            session.pop('target_id', None)
            session.pop('guess_count', None)
            return redirect(url_for("game"))

        if action == "hint":
            if hint_opportunities() > 0:
                feedback = reveal_tag_hint()
            else:
                feedback = {"error": "每答错 3 次可获得 1 次提示机会。", "hints": hints}

        elif action == "surrender":
            # 认输
            # 1. 先做一次对比
            compare = compare_tags(card_to_tags(target), card_to_tags(target))
            # 2. 把这条全绿记录追加到本局历史
            history.append({
                "guess_name": target['name'],
                "compare": compare
            })
            # 3. 带上 compare 和 hints 给模板渲染
            feedback = {"giveup": True, "answer": target["name"], "compare": compare, "hints": hints}
            session.pop('target_id', None)
            session.pop('history', None)
            session.pop('hints', None)
            session.pop('hinted_tags', None)
            session.pop('guess_count', None)

        elif action == "restart":
            # 重新开始
            session.pop('target_id', None)
            session.pop('mode', None)
            session.pop('history', None)
            session.pop('hints', None)
            session.pop('hinted_tags', None)
            session.pop('guess_count', None)
            return redirect(url_for("game"))

        else:
            # 普通猜测
            guess_count = session.get('guess_count', 0) + 1
            session['guess_count'] = guess_count

            guess_id = request.form.get("guess_id")
            if guess_id:
                try:
                    guess = db.loc[int(guess_id)]
                except Exception:
                    guess = None
                    feedback = {"error": "无效的卡片选择。", "hints": hints}
            else:

                user_input = request.form.get("guess", "").strip()
                match = filtered[filtered["name"]
                                  .str.contains(user_input, case=False, na=False, regex=False)]
                if match.empty:
                    guess = None
                    feedback = {"error": f"未找到包含“{user_input}”的卡片。", "hints": hints}
                else:
                    guess = match.iloc[0]

            # 如果 guess 还是 None，直接跳过下面逻辑
            if guess is None:
                return render_template(
                    "index.html",
                    feedback=feedback,
                    history=history,
                    hints=hints,
                    hint_available=hint_opportunities() > 0,
                    hint_opportunities=hint_opportunities(),
                    mode=mode,
                    guess_count=guess_count,
                    max_attempts=max_attempts,
                )
            else:
                if guess.name == target.name:
                    # 1. 先做一次对比
                    compare = compare_tags(card_to_tags(guess), card_to_tags(target))
                    # 2. 把这条全绿记录追加到本局历史
                    history.append({
                        "guess_name": guess['name'],
                        "compare": compare
                    })
                    # 3. 带上 compare 和 hints 给模板渲染
                    feedback = {
                        "success": f"🎉 恭喜你猜中了！答案就是【{guess['name']}】",
                        "compare": compare,
                        "hints": hints
                    }
                    # 清理本局 session
                    session.pop('target_id', None)
                    session.pop('history', None)
                    session.pop('hints', None)
                    session.pop('hinted_tags', None)
                    session.pop('guess_count', None)

                else:
                    if guess_count >= max_attempts:
                        feedback = {
                            "error": f"😢 猜测次数已用尽！答案是【{target['name']}】",
                            "giveup": True,
                            "answer": target["name"],
                            "hints": hints
                        }
                        for key in ('target_id', 'history', 'hints', 'hinted_tags', 'guess_count'):
                            session.pop(key, None)
                    else:

                        compare = compare_tags(card_to_tags(guess), card_to_tags(target))
                        history.append({
                            "guess_name": guess['name'],
                            "compare": compare
                        })

                        # 更新 session。提示不再自动发放，由按钮按机会领取。
                        session['history'] = history
                        session['hints'] = hints
                        session['hinted_tags'] = hinted_tags

                        feedback = {
                            "compare": compare,
                            "guess_name": guess['name'],
                            "hints": hints
                        }

    return render_template(
        "index.html",
        feedback=feedback,
        history=history,
        hints=hints,
        hint_available=hint_opportunities() > 0,
        hint_opportunities=hint_opportunities(),
        mode=mode,
        guess_count=guess_count,
        max_attempts=max_attempts
    )


@app.route("/suggest")
def suggest():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    pool = db
    # 联想始终使用全卡池，避免当前题库限制导致无法选择其它卡作为猜测。
    df = pool[pool["name"].str.contains(q, case=False, na=False, regex=False)][["name"]].reset_index()
    records = [{"id": int(r["id"]), "name": r["name"]} for _, r in df.iterrows()]
    return jsonify(records)

if __name__ == "__main__":
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", 7860))

    app.run(host=host, port=port, debug=False)
