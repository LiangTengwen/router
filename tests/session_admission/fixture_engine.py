"""Deterministic HTTP engine for checking the real Router's lifecycle and placement."""

import asyncio
import json
import os
import time
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse, PlainTextResponse

events = Path(os.environ["SESSION_FIXTURE_EVENTS"])
rank = int(os.environ["SESSION_FIXTURE_RANK"])
prefixes = set()


async def engine(scope, receive, send):
    request = Request(scope, receive)
    path = scope["path"]
    if path in ("/health", "/health_generate"):
        result = JSONResponse({})
    elif path == "/metrics":
        result = PlainTextResponse(
            'vllm:cache_config_info{block_size="1",num_gpu_blocks="256",engine="0"} 1.0'
        )
    elif path == "/v1/models":
        result = JSONResponse(
            {
                "object": "list",
                "data": [{"id": "test-model", "object": "model", "max_model_len": 256}],
            }
        )
    elif path in ("/get_server_info", "/server_info", "/get_model_info"):
        result = JSONResponse({"model_path": "test-model", "max_model_len": 256})
    elif path.endswith("/render") or path == "/tokenize":
        body = await request.json()
        prompt = body.get("messages", [{}])[-1].get("content", "")
        ids = list(prompt.encode()) or [1]
        result = JSONResponse({"token_ids": ids, "tokens": ids, "features": None})
    elif path == "/v1/chat/completions":
        body = await request.json()
        prompt = body["messages"][-1]["content"]
        label = request.headers.get("x-test-request", "unnamed")

        def event(kind, **extra):
            with events.open("a") as log:
                print(
                    json.dumps(
                        dict(
                            kind=kind,
                            rank=rank,
                            label=label,
                            t=time.monotonic(),
                            **extra,
                        )
                    ),
                    file=log,
                )

        cached = len(prompt.encode()) if prompt in prefixes else 0
        event("start", cached=cached)
        if label == "retry" and not events.with_suffix(".retry").exists():
            events.with_suffix(".retry").touch()
            return await JSONResponse({}, status_code=503)(scope, receive, send)
        delay = float(request.headers.get("x-test-delay", "0"))
        choice = {
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "ok"},
        }
        if body.get("stream"):

            async def generate():
                yield b'data: {"choices":[{"index":0,"delta":{"content":"x"},"finish_reason":null}]}\n\n'
                await asyncio.sleep(delay)
                event("end")
                prefixes.add(prompt)
                yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'

            result = StreamingResponse(generate(), media_type="text/event-stream")
        else:
            await asyncio.sleep(delay)
            prefixes.add(prompt)
            event("end")
            result = JSONResponse(
                {
                    "model": "test-model",
                    "choices": [choice],
                    "usage": {"prompt_tokens_details": {"cached_tokens": cached}},
                }
            )
    else:
        result = JSONResponse({}, status_code=404)
    await result(scope, receive, send)


app = engine
if os.environ.get("SESSION_FIXTURE_HARD") == "1":
    from rank_authority import RankAuthority

    app = RankAuthority(engine)
