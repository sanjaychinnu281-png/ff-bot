# -*- coding: utf-8 -*-
"""
MASTER OFFICIAL - Dashboard Server (IND Region)
No-auth edition: dashboard loads directly, all endpoints open.
Auto-Switch: Lv1-2 = BR | Lv3+ = LW (manual override allowed)
"""

import asyncio
import json
import os
import time
import secrets
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from aiohttp import web


# ==================== CONFIG ====================
TELEGRAM_LINK = "https://t.me/MASTER_FF_01"
BULK_STAGGER_DELAY = 1.5
AUTO_LW_LEVEL = 3

# Kept for reference / optional admin gating if you re-enable auth later
ADMIN_KEY = os.environ.get("ADMIN_KEY", "TOXICFF")
USER_KEY = os.environ.get("USER_KEY", "freebytoxic")
SESSION_COOKIE = "master_session"
SESSION_TTL = 86400 * 7

ADMIN_ONLY_PATHS = set()  # No auth → no restriction


# ==================== BOT STATE ====================
class BotState:
    def __init__(self):
        self.accounts: Dict[str, Dict[str, Any]] = {}
        self.logs: List[Dict[str, Any]] = []
        self.max_logs = 200
        self.total_matches = 0
        self.total_gained_exp = 0
        self.start_time = time.time()
        self.account_workers: Dict[str, asyncio.Task] = {}
        self.refresh_callbacks: Dict[str, Any] = {}
        self.account_credentials: Dict[str, Dict[str, Any]] = {}

    def log(self, message: str, level: str = "info", uid: Optional[str] = None):
        entry = {"time": time.strftime("%H:%M:%S"), "level": level, "message": message, "uid": uid}
        self.logs.append(entry)
        if len(self.logs) > self.max_logs:
            self.logs.pop(0)

    def register_account(self, uid, nickname, region, level, exp, likes=0):
        uid_str = str(uid)
        lvl_val = max(1, int(level or 1))
        now = time.time()
        if uid_str not in self.accounts:
            auto_mode = "LONE_WOLF" if lvl_val >= AUTO_LW_LEVEL else "BR"
            self.accounts[uid_str] = {
                "uid": uid_str,
                "nickname": nickname or f"Player_{uid_str[:6]}",
                "region": region or "IND",
                "level": lvl_val,
                "initial_exp": exp, "current_exp": exp, "gained_exp": 0,
                "likes": likes or 0,
                "mode": auto_mode, "mode_manual": False,
                "status": "ONLINE", "is_paused": False,
                "matches_played": 0, "matches_completed": 0, "active_matches": 0,
                "uptime_seconds": 0, "login_start_time": now,
                "consecutive_failures": 0,
                "pause_until": None, "pause_till_4am": False,
                "last_match_time": None, "last_match_failed": None,
                "last_updated": time.strftime("%H:%M:%S"),
                "_last_lvl": lvl_val,
            }
        else:
            acc = self.accounts[uid_str]
            if nickname: acc["nickname"] = nickname
            if region: acc["region"] = "IND"
            if level: acc["level"] = lvl_val
            acc["current_exp"] = exp
            acc["gained_exp"] = max(0, exp - acc["initial_exp"])
            acc["likes"] = likes
            if not acc.get("mode_manual", False):
                old_lvl = int(acc.get("_last_lvl", 1) or 1)
                acc["mode"] = "LONE_WOLF" if lvl_val >= AUTO_LW_LEVEL else "BR"
                if old_lvl < AUTO_LW_LEVEL <= lvl_val:
                    self.log(
                        f"{acc.get('nickname','Unknown')} reached Lv{lvl_val} — auto switched to LONE WOLF",
                        "success", uid_str
                    )
                acc["_last_lvl"] = lvl_val
            acc["status"] = "ONLINE"
            acc["login_start_time"] = now
            acc["consecutive_failures"] = 0
            acc["last_updated"] = time.strftime("%H:%M:%S")
        self.recalc_totals()

    def update_exp(self, uid, current_exp, level=None):
        uid_str = str(uid)
        if uid_str in self.accounts:
            acc = self.accounts[uid_str]
            old = acc["current_exp"]
            acc["current_exp"] = current_exp
            if level is not None and level > 0:
                old_lvl = int(acc.get("level", 1) or 1)
                acc["level"] = int(level)
                if not acc.get("mode_manual", False):
                    new_lvl = int(level)
                    acc["mode"] = "LONE_WOLF" if new_lvl >= AUTO_LW_LEVEL else "BR"
                    if old_lvl < AUTO_LW_LEVEL <= new_lvl:
                        self.log(
                            f"{acc.get('nickname','Unknown')} reached Lv{new_lvl} — auto switched to LONE WOLF",
                            "success", uid_str
                        )
                    acc["_last_lvl"] = new_lvl
            acc["gained_exp"] = max(0, current_exp - acc["initial_exp"])
            acc["last_updated"] = time.strftime("%H:%M:%S")
            d = current_exp - old
            if d > 0:
                self.log(f"{acc['nickname']} ({uid_str}) +{d} EXP! Total: +{acc['gained_exp']}", "success", uid_str)
            self.recalc_totals()

    def get_account_level(self, uid):
        acc = self.accounts.get(str(uid))
        return int(acc.get("level", 1) or 1) if acc else 1

    def get_account_mode(self, uid):
        acc = self.accounts.get(str(uid))
        if not acc:
            return "BR"
        if acc.get("mode_manual", False):
            mode = str(acc.get("mode", "BR") or "BR").upper()
            return "LONE_WOLF" if mode == "LONE_WOLF" else "BR"
        level = int(acc.get("level", 1) or 1)
        return "LONE_WOLF" if level >= AUTO_LW_LEVEL else "BR"

    def set_account_mode(self, uid, mode):
        uid_str = str(uid)
        mode = str(mode or "").upper().replace("-", "_").replace(" ", "_")
        if mode in ("LW", "LONEWOLF", "LONE_WOLF"):
            mode = "LONE_WOLF"
        elif mode == "BR":
            mode = "BR"
        else:
            return False, "Invalid mode"
        acc = self.accounts.get(uid_str)
        if not acc:
            return False, "Account not found"
        level = int(acc.get("level", 1) or 1)
        if level < AUTO_LW_LEVEL and mode != "BR":
            return False, f"Lone Wolf is locked until Level {AUTO_LW_LEVEL}"
        acc["mode"] = mode
        acc["mode_manual"] = True
        acc["last_updated"] = time.strftime("%H:%M:%S")
        self.log(f"{acc.get('nickname','Unknown')} ({uid_str}) switched to {mode} (manual)", "success", uid_str)
        return True, mode

    def update_status(self, uid, status, active_matches=None):
        uid_str = str(uid)
        if uid_str in self.accounts:
            self.accounts[uid_str]["status"] = status
            if active_matches is not None:
                self.accounts[uid_str]["active_matches"] = active_matches
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")

    def is_paused(self, uid):
        acc = self.accounts.get(str(uid))
        if not acc: return False
        return bool(acc.get("is_paused", False) or acc.get("status") == "PAUSED")

    def set_paused(self, uid, paused):
        uid_str = str(uid)
        if uid_str in self.accounts:
            self.accounts[uid_str]["is_paused"] = bool(paused)
            self.accounts[uid_str]["status"] = "PAUSED" if paused else "ONLINE"
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")
            return True
        return False

    def increment_match_started(self, uid):
        uid_str = str(uid)
        if uid_str in self.accounts:
            self.accounts[uid_str]["matches_played"] = self.accounts[uid_str].get("matches_played", 0) + 1
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")

    def increment_match(self, uid):
        uid_str = str(uid)
        self.total_matches += 1
        if uid_str in self.accounts:
            self.accounts[uid_str]["matches_completed"] = self.accounts[uid_str].get("matches_completed", 0) + 1
            self.accounts[uid_str]["last_match_time"] = time.strftime("%H:%M:%S")
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")
            self.accounts[uid_str]["consecutive_failures"] = 0

    def match_failed(self, uid):
        uid_str = str(uid)
        if uid_str in self.accounts:
            self.accounts[uid_str]["last_match_failed"] = time.strftime("%H:%M:%S")
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")

    def mark_login_failure(self, uid):
        uid_str = str(uid)
        if uid_str in self.accounts:
            self.accounts[uid_str]["consecutive_failures"] = self.accounts[uid_str].get("consecutive_failures", 0) + 1
            fails = self.accounts[uid_str]["consecutive_failures"]
            if fails >= 10:
                self.accounts[uid_str]["status"] = "DISABLED"
                self.accounts[uid_str]["is_paused"] = True
                self.log(f"{uid_str} disabled after {fails} failures", "error", uid_str)

    def recalc_totals(self):
        self.total_gained_exp = sum(acc.get("gained_exp", 0) for acc in self.accounts.values())


bot_state = BotState()


# ==================== PATHS ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
TEMPLATE_PATH = os.path.join(BASE_DIR, "templates", "index.html")
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")


# ==================== HANDLERS ====================
async def handle_index(request):
    if os.path.exists(TEMPLATE_PATH):
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = "<h1>templates/index.html not found!</h1>"
    return web.Response(text=content, content_type="text/html", charset="utf-8")


async def handle_session_info(request):
    """Always returns admin — no auth."""
    return web.json_response({
        "status": "ok",
        "role": "admin",
        "is_admin": True,
        "auto_lw_level": AUTO_LW_LEVEL,
    })


async def handle_login(request):
    """Kept as a no-op for compatibility."""
    return web.json_response({"status": "ok", "role": "admin"})


async def handle_logout(request):
    """No-op — no session to clear."""
    return web.json_response({"status": "ok"})


async def handle_get_stats(request):
    accounts_data = list(bot_state.accounts.values())
    accounts_data.sort(key=lambda x: x.get("gained_exp", 0), reverse=True)
    now = time.time()
    for acc in accounts_data:
        start = acc.get("login_start_time", 0)
        acc["uptime_seconds"] = int(now - start) if start else 0
    return web.json_response({
        "total_accounts": len(bot_state.accounts),
        "total_matches": bot_state.total_matches,
        "total_gained_exp": bot_state.total_gained_exp,
        "accounts": accounts_data,
        "logs": bot_state.logs[-60:],
        "uptime": int(time.time() - bot_state.start_time),
        "auto_lw_level": AUTO_LW_LEVEL,
    })


async def handle_add_account(request):
    try:
        data = await request.json()
        existing = _load_accounts_file()
        if "uid" in data and "password" in data:
            uid = str(data["uid"]).strip()
            pwd = str(data["password"]).strip()
            if not uid or not pwd:
                return web.json_response({"status": "error", "error": "UID and Password required"})
            existing = [acc for acc in existing if str(acc.get("uid")) != uid]
            existing.append({"uid": uid, "password": pwd})
        elif "token" in data:
            token = str(data["token"]).strip()
            if not token:
                return web.json_response({"status": "error", "error": "Token required"})
            existing = [acc for acc in existing if acc.get("token") != token]
            existing.append({"token": token})
        else:
            return web.json_response({"status": "error", "error": "Invalid payload"})
        _save_accounts_file(existing)
        bot_state.log(f"New account added: {data.get('uid') or 'Token'}", "success")
        if "on_account_added" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_account_added"](data))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_bulk_add(request):
    try:
        data = await request.json()
        new_accounts = data.get("accounts", [])
        if not isinstance(new_accounts, list):
            return web.json_response({"status": "error", "error": "Invalid accounts list"})
        existing = _load_accounts_file()
        existing_keys = set()
        for acc in existing:
            if "uid" in acc and acc.get("uid"): existing_keys.add(f"uid:{acc['uid']}")
            elif "token" in acc and acc.get("token"): existing_keys.add(f"tok:{str(acc['token'])[:20]}")
        added = 0; skipped = 0; invalid = 0; added_entries = []
        for acc in new_accounts:
            if not isinstance(acc, dict): invalid += 1; continue
            if "uid" in acc and "password" in acc:
                uid = str(acc["uid"]).strip()
                pwd = str(acc["password"]).strip()
                if not uid or not pwd: invalid += 1; continue
                key = f"uid:{uid}"
                if key in existing_keys: skipped += 1; continue
                existing_keys.add(key)
                entry = {"uid": uid, "password": pwd}
                existing.append(entry); added_entries.append(entry); added += 1
            elif "token" in acc:
                token = str(acc["token"]).strip()
                if not token: invalid += 1; continue
                key = f"tok:{token[:20]}"
                if key in existing_keys: skipped += 1; continue
                existing_keys.add(key)
                entry = {"token": token}
                existing.append(entry); added_entries.append(entry); added += 1
            else: invalid += 1
        _save_accounts_file(existing)
        if added > 0:
            bot_state.log(f"Bulk add: +{added} accounts, {skipped} skipped, {invalid} invalid", "success")
            if "on_account_added" in bot_state.refresh_callbacks:
                asyncio.create_task(_staggered_worker_start(added_entries))
        return web.json_response({"status": "ok", "added": added, "skipped": skipped, "invalid": invalid})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def _staggered_worker_start(entries):
    callback = bot_state.refresh_callbacks.get("on_account_added")
    if not callback: return
    for i, entry in enumerate(entries):
        try:
            if i > 0: await asyncio.sleep(BULK_STAGGER_DELAY)
            await callback(entry)
        except Exception as e:
            bot_state.log(f"Worker start failed: {e}", "error")


async def handle_delete_account(request):
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        token = str(data.get("token", "")).strip()
        existing = _load_accounts_file()
        if token: existing = [acc for acc in existing if str(acc.get("token", "")).strip() != token]
        elif uid: existing = [acc for acc in existing if str(acc.get("uid", "")).strip() != uid]
        _save_accounts_file(existing)
        worker_keys = []
        if uid: worker_keys.append(uid)
        if token: worker_keys.append(token[:10])
        for key in worker_keys:
            worker = bot_state.account_workers.get(key)
            if worker:
                if not worker.done(): worker.cancel()
                bot_state.account_workers.pop(key, None)
        if uid and uid in bot_state.accounts: del bot_state.accounts[uid]
        bot_state.log(f"Account {uid or token[:10] or 'unknown'} removed", "warning", uid or None)
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_refresh_account(request):
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        if "on_refresh_account" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_refresh_account"](uid))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_mode_switch(request):
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        mode = str(data.get("mode", "")).strip()
        ok, result = bot_state.set_account_mode(uid, mode)
        if not ok:
            return web.json_response({"status": "error", "error": result}, status=400)
        return web.json_response({"status": "ok", "uid": uid, "mode": result, "level": bot_state.get_account_level(uid)})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=400)


async def handle_bulk_mode_switch(request):
    try:
        data = await request.json()
        mode = str(data.get("mode", "BR")).strip()
        changed = 0; skipped = 0
        for uid in list(bot_state.accounts.keys()):
            ok, _ = bot_state.set_account_mode(uid, mode)
            if ok: changed += 1
            else: skipped += 1
        bot_state.log(f"Bulk mode switch to {mode}: {changed} changed, {skipped} skipped", "success")
        return web.json_response({"status": "ok", "changed": changed, "skipped": skipped, "mode": mode})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=400)


async def handle_pause_account(request):
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        if not uid:
            return web.json_response({"status": "error", "error": "UID required"}, status=400)
        acc = bot_state.accounts.get(uid)
        if not acc:
            return web.json_response({"status": "error", "error": "Account not found"}, status=404)
        currently_paused = bot_state.is_paused(uid)
        new_state = not currently_paused
        bot_state.set_paused(uid, new_state)
        if not new_state:
            acc["pause_until"] = None
            acc["pause_till_4am"] = False
        bot_state.log(f"{acc.get('nickname','Unknown')} ({uid}) {'paused' if new_state else 'resumed'}",
                      "warning" if new_state else "success", uid)
        return web.json_response({"status": "ok", "uid": uid, "is_paused": new_state})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=400)


async def handle_pause_till_4am(request):
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        if not uid:
            return web.json_response({"status": "error", "error": "UID required"}, status=400)
        acc = bot_state.accounts.get(uid)
        if not acc:
            return web.json_response({"status": "error", "error": "Account not found"}, status=404)
        until_ts = _next_4am_timestamp()
        acc["pause_until"] = until_ts
        acc["pause_till_4am"] = True
        bot_state.set_paused(uid, True)
        until_str = datetime.fromtimestamp(until_ts).strftime("%d %b %I:%M %p")
        bot_state.log(f"{acc.get('nickname','Unknown')} ({uid}) paused till {until_str}", "warning", uid)
        return web.json_response({"status": "ok", "uid": uid, "pause_until": until_ts})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=400)


async def handle_resume_now(request):
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        if not uid:
            return web.json_response({"status": "error", "error": "UID required"}, status=400)
        acc = bot_state.accounts.get(uid)
        if not acc:
            return web.json_response({"status": "error", "error": "Account not found"}, status=404)
        acc["pause_until"] = None
        acc["pause_till_4am"] = False
        bot_state.set_paused(uid, False)
        bot_state.log(f"{acc.get('nickname','Unknown')} ({uid}) manually resumed", "success", uid)
        return web.json_response({"status": "ok", "uid": uid})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=400)


async def handle_pause_all(request):
    try:
        if not bot_state.accounts:
            return web.json_response({"status": "error", "error": "No accounts"}, status=400)
        any_running = any(not bot_state.is_paused(uid) for uid in bot_state.accounts.keys())
        new_state = any_running
        for uid in bot_state.accounts.keys():
            bot_state.set_paused(uid, new_state)
            if not new_state:
                bot_state.accounts[uid]["pause_until"] = None
                bot_state.accounts[uid]["pause_till_4am"] = False
        bot_state.log(f"All accounts {'paused' if new_state else 'resumed'}",
                      "warning" if new_state else "success")
        return web.json_response({"status": "ok", "all_paused": new_state})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=400)


async def handle_clear_logs(request):
    bot_state.logs.clear()
    return web.json_response({"status": "ok"})


# ==================== HELPERS ====================
def _next_4am_timestamp() -> float:
    now = datetime.now()
    target = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target.timestamp()


def _load_accounts_file():
    if not os.path.exists(ACCOUNTS_FILE): return []
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_accounts_file(accounts):
    try:
        tmp = ACCOUNTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(accounts, f, indent=2)
        os.replace(tmp, ACCOUNTS_FILE)
    except Exception as e:
        print(f"Save accounts failed: {e}")


# ==================== AUTO RESUME TASK ====================
async def auto_resume_task():
    while True:
        try:
            now = time.time()
            for uid, acc in list(bot_state.accounts.items()):
                until = acc.get("pause_until")
                if until and now >= until:
                    acc["pause_until"] = None
                    acc["pause_till_4am"] = False
                    bot_state.set_paused(uid, False)
                    bot_state.log(
                        f"{acc.get('nickname','Unknown')} ({uid}) auto-resumed (4 AM)",
                        "success", uid
                    )
        except Exception as e:
            print(f"Auto-resume error: {e}")
        await asyncio.sleep(30)


# ==================== START SERVER ====================
async def start_web_dashboard(host="0.0.0.0", port=5000):
    # No middleware — everything is public
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/login", handle_index)            # legacy → same dashboard
    app.router.add_post("/api/login", handle_login)       # no-op
    app.router.add_post("/api/logout", handle_logout)     # no-op
    app.router.add_get("/api/session", handle_session_info)
    app.router.add_get("/api/stats", handle_get_stats)
    app.router.add_post("/api/account/add", handle_add_account)
    app.router.add_post("/api/account/bulk_add", handle_bulk_add)
    app.router.add_post("/api/account/delete", handle_delete_account)
    app.router.add_post("/api/account/refresh", handle_refresh_account)
    app.router.add_post("/api/account/mode", handle_mode_switch)
    app.router.add_post("/api/account/bulk_mode", handle_bulk_mode_switch)
    app.router.add_post("/api/account/pause", handle_pause_account)
    app.router.add_post("/api/account/pause_till_4am", handle_pause_till_4am)
    app.router.add_post("/api/account/resume_now", handle_resume_now)
    app.router.add_post("/api/account/pause_all", handle_pause_all)
    app.router.add_post("/api/logs/clear", handle_clear_logs)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    asyncio.create_task(auto_resume_task())

    print(f"\033[92m[+] Dashboard: http://localhost:{port}\033[0m")
    print(f"\033[93m[+] Telegram : {TELEGRAM_LINK}\033[0m")
    print(f"\033[93m[+] Auto LW  : Lv{AUTO_LW_LEVEL}+\033[0m")
    print(f"\033[93m[+] Auth     : DISABLED (public access)\033[0m")