"""ASGI middleware for an exclusively addressed, single-process vLLM rank.

Mount with --middleware vllm_router.rank_authority.RankAuthority on each worker.
The process owns its SQLite journal and its engine. Capacity is measured in whole
GPU KV blocks. Only a backend completion or an unredeemed lease releases blocks.
"""

import asyncio
import contextlib
import fcntl
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("uvicorn.error")


class Ledger:
    def __init__(self, path, total):
        self.total = total
        self.epoch = str(uuid.uuid4())
        self.lock = open(str(path) + ".lock", "a+b")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS leases
            (id TEXT PRIMARY KEY, epoch TEXT NOT NULL, digest TEXT NOT NULL,
             blocks INTEGER NOT NULL, state TEXT NOT NULL, expires REAL NOT NULL)""")
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS lease_state ON leases(state, expires)"
        )
        # Old reserved leases cannot be redeemed by this generation. Running
        # ones remain charged until their owning engine is proven stopped.
        with self.db:
            self.db.execute("DELETE FROM leases WHERE state != 'running'")
        self.version = 0
        self.changed = asyncio.Event()

    def notify(self):
        self.version += 1
        self.changed.set()
        self.changed = asyncio.Event()

    def available(self):
        now = time.monotonic()
        with self.db:
            changed = self.db.execute(
                "UPDATE leases SET state='closed',expires=? WHERE state='reserved' AND expires<=?",
                (now + 600, now),
            ).rowcount
            self.db.execute(
                "DELETE FROM leases WHERE state='closed' AND expires<=?", (now,)
            )
        if changed:
            self.notify()
        used = self.db.execute(
            "SELECT COALESCE(SUM(blocks),0) FROM leases WHERE state IN ('reserved','running')"
        ).fetchone()[0]
        return max(0, self.total - used)

    def claim(self, attempt, epoch, digest, blocks):
        free = self.available()
        if epoch != self.epoch or blocks <= 0:
            return 409
        prior = self.db.execute(
            "SELECT epoch,digest,blocks,state FROM leases WHERE id=?", (attempt,)
        ).fetchone()
        if prior:
            return 200 if prior == (epoch, digest, blocks, "reserved") else 409
        if blocks > free:
            return 409
        with self.db:
            self.db.execute(
                "INSERT INTO leases VALUES (?,?,?,?,?,?)",
                (attempt, epoch, digest, blocks, "reserved", time.monotonic() + 30),
            )
        self.notify()
        return 201

    def redeem(self, attempt, epoch, digest):
        self.available()
        if epoch != self.epoch:
            return False
        with self.db:
            return bool(
                self.db.execute(
                    "UPDATE leases SET state='running' WHERE id=? AND epoch=? AND digest=? AND state='reserved'",
                    (attempt, epoch, digest),
                ).rowcount
            )

    def release(self, attempt, epoch, before):
        with self.db:
            changed = self.db.execute(
                "UPDATE leases SET state='closed',expires=? WHERE id=? AND epoch=? AND state=?",
                (time.monotonic() + 600, attempt, epoch, before),
            ).rowcount
        if changed:
            self.notify()


class RankAuthority:
    def __init__(self, app):
        self.app = app
        self.secret = os.environ["VLLM_SESSION_RANK_KEY"].encode()
        self.block = int(os.environ["VLLM_SESSION_BLOCK_SIZE"])
        self.limit = int(os.environ["VLLM_SESSION_MODEL_LEN"])
        total = int(os.environ["VLLM_SESSION_RANK_BLOCKS"])
        if not self.secret or min(self.block, self.limit, total) <= 0:
            raise ValueError("invalid session rank configuration")
        self.ledger = Ledger(Path(os.environ["VLLM_SESSION_RANK_JOURNAL"]), total)
        self.draining = set()
        self.verified = False
        self.verify_lock = asyncio.Lock()

    def stamp(self, digest, required, until):
        value = f"{self.ledger.epoch}/{digest}/{required}/{until}".encode()
        return hmac.new(self.secret, value, hashlib.sha256).hexdigest()

    async def inspect_engine(self, scope, path, raw=None):
        body = bytearray()
        status = 0

        async def send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                body.extend(message.get("body", b""))
                if len(body) > 16 * 1024 * 1024:
                    raise ValueError("engine metadata exceeds inspection limit")

        delivered = False

        async def receive():
            nonlocal delivered
            if delivered:
                await asyncio.Event().wait()
            delivered = True
            return {"type": "http.request", "body": raw or b"", "more_body": False}

        inner = dict(
            scope,
            path=path,
            raw_path=path.encode(),
            query_string=b"",
            method="POST" if raw is not None else "GET",
        )
        await asyncio.wait_for(self.app(inner, receive, send), 2)
        return status, body

    async def verify(self, scope):
        """Check the configured slice against the engine backing this generation."""
        async with self.verify_lock:
            if self.verified:
                return True
            status, metrics = await self.inspect_engine(scope, "/metrics")
            if status != 200:
                return False
            rows = [
                line
                for line in metrics.decode().splitlines()
                if line.startswith("vllm:cache_config_info{")
            ]
            if len(rows) != 1:
                return False
            labels = dict(re.findall(r'(\w+)="([^"]*)"', rows[0]))
            if (
                int(labels.get("block_size", 0)) != self.block
                or int(labels.get("num_gpu_blocks", 0)) < self.ledger.total
            ):
                return False
            status, models = await self.inspect_engine(scope, "/v1/models")
            entries = json.loads(models)["data"] if status == 200 else []
            self.verified = bool(entries) and all(
                type(entry.get("max_model_len")) is int
                and 0 < entry["max_model_len"] <= self.limit
                for entry in entries
            )
            return self.verified

    async def render_length(self, scope, raw):
        """Price the exact engine template. An unsupported render has no credit."""
        status, body = await self.inspect_engine(
            scope, "/v1/chat/completions/render", raw
        )
        if status != 200:
            return None
        result = json.loads(body)
        if isinstance(result, list):
            if len(result) != 1:
                return None
            result = result[0]
        if not isinstance(result, dict) or result.get("features"):
            return None
        ids = result.get("token_ids")
        return (
            len(ids)
            if isinstance(ids, list) and ids and all(type(i) is int for i in ids)
            else None
        )

    async def quote(self, scope, raw):
        req = json.loads(raw)
        if req.get("use_beam_search") or req.get("best_of") not in (None, 1):
            raise ValueError("only ordinary chat sampling is supported")
        choices = req.get("n") or 1
        if type(choices) is not int or choices < 1 or choices > 128:
            raise ValueError("unsupported number of sequences")
        output = req.get("max_completion_tokens") or req.get("max_tokens")
        if output is not None and (type(output) is not int or output < 1):
            raise ValueError("invalid output limit")
        # Only plain text with exact engine rendering earns a shorter bound.
        # Multimodal expansion, missing templates and unbounded output reserve
        # the configured maximum context per sequence, without prefix credit.
        length = None
        if output and all(
            isinstance(m.get("content"), str) for m in req.get("messages", [])
        ):
            with contextlib.suppress(
                ValueError, KeyError, TypeError, asyncio.TimeoutError
            ):
                length = await asyncio.wait_for(self.render_length(scope, raw), 2)
        per = self.limit if length is None else length + output
        blocks = ((per + self.block - 1) // self.block) * choices
        if per > self.limit or (output and output > self.limit):
            blocks = self.ledger.total + 1
        digest = hashlib.sha256(raw).hexdigest()
        until = int(time.monotonic()) + 15
        return dict(
            epoch=self.ledger.epoch,
            total=self.ledger.total,
            free=self.ledger.available(),
            version=self.ledger.version,
            required=blocks,
            digest=digest,
            stamp=f"{until}.{self.stamp(digest, blocks, until)}",
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        request = Request(scope, receive)
        headers = request.headers

        async def respond(status, value=None):
            await JSONResponse(value or {}, status_code=status)(scope, receive, send)

        if path.startswith("/_session/"):
            if not hmac.compare_digest(
                headers.get("x-router-capacity-key", "").encode(), self.secret
            ):
                return await respond(403)
            try:
                if path == "/_session/quote" and request.method == "POST":
                    if not await self.verify(scope):
                        return await respond(
                            503, {"error": "unverified engine capacity"}
                        )
                    return await respond(
                        200, await self.quote(scope, await request.body())
                    )
                if path == "/_session/lease" and request.method == "POST":
                    data = await request.json()
                    attempt = str(uuid.UUID(data["id"]))
                    blocks = data["required"]
                    until_text, signature = data["stamp"].split(".", 1)
                    until = int(until_text)
                    if (
                        type(blocks) is not int
                        or time.monotonic() > until
                        or not hmac.compare_digest(
                            self.stamp(data["digest"], blocks, until), signature
                        )
                    ):
                        return await respond(409)
                    return await respond(
                        self.ledger.claim(
                            attempt, data["epoch"], data["digest"], blocks
                        )
                    )
                if path.startswith("/_session/lease/") and request.method == "DELETE":
                    self.ledger.release(
                        path.rsplit("/", 1)[1],
                        headers.get("x-router-epoch"),
                        "reserved",
                    )
                    return await respond(200)
                if path == "/_session/wait" and request.method == "GET":
                    params = parse_qs(scope["query_string"].decode())
                    self.ledger.available()
                    if (
                        params["epoch"][0] == self.ledger.epoch
                        and int(params["version"][0]) == self.ledger.version
                    ):
                        with contextlib.suppress(asyncio.TimeoutError):
                            await asyncio.wait_for(self.ledger.changed.wait(), 3)
                    return await respond(200, {"version": self.ledger.version})
            except (ValueError, KeyError, TypeError, OverflowError):
                return await respond(400)
            return await respond(404)
        if path != "/v1/chat/completions":
            if path in {
                "/health",
                "/version",
                "/v1/models",
                "/metrics",
                "/get_model_info",
                "/get_server_info",
                "/server_info",
                "/kv_event_sources",
                "/tokenize",
                "/v1/chat/completions/render",
            }:
                return await self.app(scope, receive, send)
            return await respond(403)
        raw = await request.body()
        digest = hashlib.sha256(raw).hexdigest()
        attempt, epoch = headers.get("x-router-lease"), headers.get("x-router-epoch")
        if not self.ledger.redeem(attempt, epoch, digest):
            return await respond(409)
        logger.info("session_rank_redeem epoch=%s attempt=%s", epoch, attempt)
        task = asyncio.create_task(self.run_leased(scope, raw, send, attempt, epoch))
        self.draining.add(task)
        task.add_done_callback(self.draining.discard)
        await asyncio.shield(task)

    async def run_leased(self, scope, raw, send, attempt, epoch):
        """Keep consuming the bounded generation after a client goes away."""
        status, streaming, disconnected, got_body, done = 0, False, False, False, False
        buffer = bytearray()
        expected = set(range(json.loads(raw).get("n") or 1))
        finished = set()
        invalid = False

        def record(payload):
            nonlocal invalid
            try:
                value = json.loads(payload)
                if "error" in value:
                    invalid = True
                for choice in value["choices"]:
                    if choice.get("finish_reason") in {
                        "stop",
                        "length",
                        "tool_calls",
                        "content_filter",
                        "function_call",
                    }:
                        finished.add(choice["index"])
            except (KeyError, ValueError, TypeError):
                invalid = True

        async def receive():
            nonlocal got_body
            if got_body:
                await asyncio.Event().wait()
            got_body = True
            return {"type": "http.request", "body": raw, "more_body": False}

        async def observe(msg):
            nonlocal status, streaming, disconnected, done, invalid
            if msg["type"] == "http.response.start":
                status = msg["status"]
                streaming = b"text/event-stream" in dict(msg["headers"]).get(
                    b"content-type", b""
                )
            elif msg["type"] == "http.response.body" and status == 200 and not invalid:
                buffer.extend(msg.get("body", b""))
                if len(buffer) > 16 * 1024 * 1024:
                    invalid = True
                    buffer.clear()
                elif streaming:
                    while (pos := buffer.find(b"\n")) >= 0:
                        line = bytes(buffer[:pos]).strip()
                        del buffer[: pos + 1]
                        if line == b"data: [DONE]":
                            done = True
                        elif line.startswith(b"data:"):
                            record(line[5:])
                elif not msg.get("more_body", False):
                    record(buffer)
                    done = True
            if not disconnected:
                try:
                    await send(msg)
                except (OSError, RuntimeError):
                    disconnected = True

        await self.app(scope, receive, observe)
        if done and not invalid and finished == expected:
            self.ledger.release(attempt, epoch, "running")
            logger.info("session_rank_settle epoch=%s attempt=%s", epoch, attempt)
