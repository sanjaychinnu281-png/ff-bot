# -*- coding: utf-8 -*-
"""
FreeFire Level Up Bot - Professional Web Dashboard & Real-Time EXP Tracker
Embedded Async Web Server (aiohttp)
"""

import asyncio
import json
import os
import time
from typing import Dict, List, Any, Optional
from aiohttp import web

# Global bot state shared between Main.py and Web Dashboard
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
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
            "uid": uid
        }
        self.logs.append(entry)
        if len(self.logs) > self.max_logs:
            self.logs.pop(0)

    def register_account(self, uid: str, nickname: str, region: str, level: int, exp: int, likes: int = 0):
        uid_str = str(uid)
        lvl_val = max(1, int(level or 1))
        default_mode = "BR"
        if uid_str not in self.accounts:
            self.accounts[uid_str] = {
                "uid": uid_str,
                "nickname": nickname or f"Player_{uid_str[:6]}",
                "region": region or "BD",
                "level": lvl_val,
                "initial_exp": exp,
                "current_exp": exp,
                "gained_exp": 0,
                "likes": likes or 0,
                "mode": default_mode,
                "mode_manual": False,
                "status": "ONLINE",
                "matches_played": 0,
                "active_matches": 0,
                "last_match_time": None,
                "last_updated": time.strftime("%H:%M:%S")
            }
        else:
            acc = self.accounts[uid_str]
            old_level = int(acc.get("level", 1) or 1)
            if nickname:
                acc["nickname"] = nickname
            if region:
                acc["region"] = region
            if level:
                acc["level"] = lvl_val
            acc["current_exp"] = exp
            acc["gained_exp"] = max(0, exp - acc["initial_exp"])
            acc["likes"] = likes
            if not acc.get("mode_manual", False):
                acc["mode"] = "BR"
            acc["status"] = "ONLINE"
            acc["last_updated"] = time.strftime("%H:%M:%S")
        self.recalc_totals()

    def update_exp(self, uid: str, current_exp: int, level: Optional[int] = None):
        uid_str = str(uid)
        if uid_str in self.accounts:
            acc = self.accounts[uid_str]
            old_exp = acc["current_exp"]
            old_level = int(acc.get("level", 1) or 1)
            acc["current_exp"] = current_exp
            if level is not None and level > 0:
                acc["level"] = int(level)
            current_level = int(acc.get("level", 1) or 1)

            # BR remains the automatic/default mode forever. LONE_WOLF
            # is entered only through an explicit manual mode switch.
            if not acc.get("mode_manual", False):
                acc["mode"] = "BR"

            acc["gained_exp"] = max(0, current_exp - acc["initial_exp"])
            acc["last_updated"] = time.strftime("%H:%M:%S")
            diff = current_exp - old_exp
            if diff > 0:
                self.log(
                    f"Account {acc['nickname']} ({uid_str}) gained +{diff} EXP! "
                    f"Total Gained: +{acc['gained_exp']} | Mode: {acc['mode']}",
                    "success", uid_str
                )
            self.recalc_totals()

    def get_account_level(self, uid: str) -> int:
        acc = self.accounts.get(str(uid))
        return int(acc.get("level", 1) or 1) if acc else 1

    def get_account_mode(self, uid: str) -> str:
        acc = self.accounts.get(str(uid))
        if not acc:
            return "BR"
        # BR is the default through Level 10. Once the account is above Level 10,
        # expose the effective automatic mode as Lone Wolf regardless of the saved preference.
        level = int(acc.get("level", 1) or 1)
        if level > 10:
            return "LONE_WOLF"
        mode = str(acc.get("mode", "BR") or "BR").upper()
        return "LONE_WOLF" if mode == "LONE_WOLF" and acc.get("mode_manual", False) else "BR"

    def set_account_mode(self, uid: str, mode: str):
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
        if level < 5 and mode != "BR":
            return False, "Lone Wolf is locked until Level 5"

        acc["mode"] = mode
        acc["mode_manual"] = True
        acc["last_updated"] = time.strftime("%H:%M:%S")
        self.log(
            f"UID {uid_str} ({acc.get('nickname', 'Unknown')}) switched to {mode}",
            "success", uid_str
        )
        return True, mode

    def update_status(self, uid: str, status: str, active_matches: Optional[int] = None):
        uid_str = str(uid)
        if uid_str in self.accounts:
            self.accounts[uid_str]["status"] = status
            if active_matches is not None:
                self.accounts[uid_str]["active_matches"] = active_matches
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")

    def increment_match(self, uid: str):
        uid_str = str(uid)
        self.total_matches += 1
        if uid_str in self.accounts:
            self.accounts[uid_str]["matches_played"] += 1
            self.accounts[uid_str]["last_match_time"] = time.strftime("%H:%M:%S")
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")
            self.log(f"Account {self.accounts[uid_str]['nickname']} finished Match #{self.accounts[uid_str]['matches_played']}", "info", uid_str)

    def recalc_totals(self):
        self.total_gained_exp = sum(acc.get("gained_exp", 0) for acc in self.accounts.values())


bot_state = BotState()

# Convenience cursor for the dashboard batch importer.
batch_import_state = {"last_start": 0, "last_count": 0, "last_end": 0, "total": 0}


# ==================== HTTP HANDLERS ====================

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "index.html")

async def handle_index(request: web.Request) -> web.Response:
    if os.path.exists(TEMPLATE_PATH):
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = "<h1>templates/index.html not found!</h1>"
    return web.Response(text=content, content_type="text/html", charset="utf-8")


async def handle_get_stats(request: web.Request) -> web.Response:
    accounts_data = list(bot_state.accounts.values())
    accounts_data.sort(key=lambda x: x.get("gained_exp", 0), reverse=True)
    return web.json_response({
        "total_accounts": len(bot_state.accounts),
        "total_matches": bot_state.total_matches,
        "total_gained_exp": bot_state.total_gained_exp,
        "accounts": accounts_data,
        "logs": bot_state.logs[-60:],
        "uptime": int(time.time() - bot_state.start_time),
        "import_state": dict(batch_import_state)
    })


async def handle_add_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        accounts_file = "accounts.json"
        existing = []
        if os.path.exists(accounts_file):
            try:
                with open(accounts_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = []

        if "uid" in data and "password" in data:
            uid = str(data["uid"]).strip()
            pwd = str(data["password"]).strip()
            if not uid or not pwd:
                return web.json_response({"status": "error", "error": "UID and Password are required"})
            existing = [acc for acc in existing if str(acc.get("uid")) != uid]
            existing.append({"uid": uid, "password": pwd})
        elif "token" in data:
            token = str(data["token"]).strip()
            if not token:
                return web.json_response({"status": "error", "error": "Token is required"})
            existing = [acc for acc in existing if acc.get("token") != token]
            existing.append({"token": token})
        else:
            return web.json_response({"status": "error", "error": "Invalid payload"})

        with open(accounts_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)

        bot_state.log(f"New account added: {data.get('uid') or 'Token'}", "success")
        
        # Trigger dynamic worker launch
        if "on_account_added" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_account_added"](data))

        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_delete_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        token = str(data.get("token", "")).strip()
        accounts_file = "accounts.json"
        if os.path.exists(accounts_file):
            with open(accounts_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if token:
                existing = [acc for acc in existing if str(acc.get("token", "")).strip() != token]
            elif uid:
                existing = [acc for acc in existing if str(acc.get("uid", "")).strip() != uid]
            with open(accounts_file, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)

        worker_keys = []
        if uid:
            worker_keys.append(uid)
        if token:
            worker_keys.append(token[:10])
        for key in worker_keys:
            worker = bot_state.account_workers.get(key)
            if worker:
                if not worker.done():
                    worker.cancel()
                bot_state.account_workers.pop(key, None)

        if uid and uid in bot_state.accounts:
            del bot_state.accounts[uid]
        bot_state.log(f"Account {uid or token[:10] or 'unknown'} removed from rotation.", "warning", uid or None)
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def _load_accounts_file() -> List[Dict[str, Any]]:
    if not os.path.exists("accounts.json"):
        return []
    try:
        with open("accounts.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_accounts_file(accounts: List[Dict[str, Any]]) -> None:
    with open("accounts.json", "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2, ensure_ascii=False)


def _normalize_uploaded_accounts(payload: Any) -> tuple[List[Dict[str, str]], Optional[str]]:
    if isinstance(payload, dict):
        for key in ("accounts", "data", "users"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        return [], "JSON must contain an account list (array or accounts/data/users array)"

    result: List[Dict[str, str]] = []
    for pos, item in enumerate(payload, 1):
        if not isinstance(item, dict):
            return [], f"Account #{pos} is not an object"
        uid = str(item.get("uid", "") or "").strip()
        password = str(item.get("password", "") or item.get("pass", "") or "").strip()
        token = str(item.get("token", "") or "").strip()
        if uid and password:
            result.append({"uid": uid, "password": password})
        elif token:
            result.append({"token": token})
        else:
            return [], f"Account #{pos} needs uid + password, or token"
    return result, None


async def handle_batch_import(request: web.Request) -> web.Response:
    global batch_import_state
    try:
        reader = await request.multipart()
        file_bytes = None
        file_filename = ""
        start_raw, count_raw = "1", "1"
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "file":
                file_filename = part.filename or ""
                file_bytes = await part.read(decode=False)
            elif part.name == "start":
                start_raw = (await part.text()).strip() or "1"
            elif part.name == "count":
                count_raw = (await part.text()).strip() or "1"

        if file_bytes is None or not file_filename:
            return web.json_response({"status": "error", "error": "Please select a JSON file"}, status=400)
        try:
            start, count = int(start_raw), int(count_raw)
        except ValueError:
            return web.json_response({"status": "error", "error": "Start and count must be whole numbers"}, status=400)
        if start < 1 or count < 1:
            return web.json_response({"status": "error", "error": "Start must be >= 1 and count must be >= 1"}, status=400)
        if count > 500:
            return web.json_response({"status": "error", "error": "Maximum 500 accounts per batch"}, status=400)

        raw = file_bytes
        if len(raw) > 2 * 1024 * 1024:
            return web.json_response({"status": "error", "error": "JSON file is too large (max 2 MB)"}, status=400)
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except Exception as e:
            return web.json_response({"status": "error", "error": f"Invalid JSON: {e}"}, status=400)

        source, err = _normalize_uploaded_accounts(payload)
        if err:
            return web.json_response({"status": "error", "error": err}, status=400)
        total = len(source)
        if total == 0:
            return web.json_response({"status": "error", "error": "JSON contains no accounts"}, status=400)
        if start > total:
            return web.json_response({"status": "error", "error": f"Start account #{start} is outside the JSON (total {total})"}, status=400)

        end = min(total, start + count - 1)
        selected = source[start - 1:end]
        existing = await _load_accounts_file()
        for acc in selected:
            if "uid" in acc:
                existing = [x for x in existing if str(x.get("uid", "")).strip() != acc["uid"]]
            else:
                existing = [x for x in existing if str(x.get("token", "")).strip() != acc["token"]]
            existing.append(acc)
        _save_accounts_file(existing)

        batch_import_state = {"last_start": start, "last_count": len(selected), "last_end": end, "total": total}
        callback = bot_state.refresh_callbacks.get("on_account_added")
        started = 0
        if callback:
            for acc in selected:
                await callback(acc)
                started += 1
                if started < len(selected):
                    await asyncio.sleep(0.20)

        bot_state.log(f"Batch import: accounts {start}-{end} selected from {total}; {len(selected)} loaded.", "success")
        return web.json_response({"status": "ok", "total": total, "start": start, "count": len(selected), "end": end, "next_start": end + 1 if end < total else None, "started": started, "message": f"Accounts {start}-{end} imported"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=500)


async def handle_delete_all_accounts(request: web.Request) -> web.Response:
    try:
        accounts = await _load_accounts_file()
        removed = len(accounts)
        workers = list(bot_state.account_workers.values())
        for worker in workers:
            if worker and not worker.done():
                worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        bot_state.account_workers.clear()
        _save_accounts_file([])
        bot_state.accounts.clear()
        bot_state.account_credentials.clear()
        bot_state.recalc_totals()
        bot_state.log(f"All accounts deleted. {removed} account(s) removed from rotation.", "warning")
        return web.json_response({"status": "ok", "removed": removed})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=500)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "accounts": len(bot_state.accounts), "uptime": int(time.time() - bot_state.start_time)})


async def handle_refresh_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid")).strip()
        if "on_refresh_account" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_refresh_account"](uid))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_mode_switch(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        mode = str(data.get("mode", "")).strip()
        ok, result = bot_state.set_account_mode(uid, mode)
        if not ok:
            return web.json_response({"status": "error", "error": result}, status=400)
        return web.json_response({
            "status": "ok",
            "uid": uid,
            "mode": result,
            "level": bot_state.get_account_level(uid)
        })
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=400)


async def start_web_dashboard(host: str = "0.0.0.0", port: int = 5000):
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/stats", handle_get_stats)
    app.router.add_post("/api/account/add", handle_add_account)
    app.router.add_post("/api/account/delete", handle_delete_account)
    app.router.add_post("/api/account/import", handle_batch_import)
    app.router.add_post("/api/account/delete-all", handle_delete_all_accounts)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/api/account/refresh", handle_refresh_account)
    app.router.add_post("/api/account/mode", handle_mode_switch)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(f"\033[92m[+] Web Dashboard running on http://localhost:{port}\033[0m")
