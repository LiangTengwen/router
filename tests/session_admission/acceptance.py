"""Process-level Router acceptance. Pass --vllm and --model for two real CUDA ranks."""

import argparse
import concurrent.futures
import contextlib
import http.client
import json
import os
import secrets
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1]


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def call(url, body=None, headers=None, timeout=45):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def wait(check, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.025)
    raise TimeoutError(f"condition still false after {seconds}s")


@contextlib.contextmanager
def process(args, env, log, url, timeout):
    with log.open("xb") as output:
        proc = subprocess.Popen(
            args,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:

            def healthy():
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"{args[0]} exited ({proc.returncode}): {log.read_text()[-2500:]}"
                    )
                try:
                    return call(url + "/health", timeout=2)[0] == 200
                except OSError:
                    return False

            wait(healthy, timeout)
            yield
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()


class Run:
    def __init__(self, args):
        self.args = args
        self.out = args.output.resolve()
        self.out.mkdir(exist_ok=False)
        self.events = self.out / "worker-events.jsonl"
        self.events.touch()
        self.secret = secrets.token_hex(32)
        self.env = dict(
            os.environ,
            VLLM_SESSION_RANK_KEY=self.secret,
            PYTHONPATH=os.pathsep.join(
                [
                    str(HERE),
                    str(SRC / "py_src/vllm_router"),
                    os.environ.get("PYTHONPATH", ""),
                ]
            ).rstrip(os.pathsep),
        )
        self.env.pop("VLLM_ROUTER_SESSION_CONFIG", None)
        self.results = []

    def record(self, name, **evidence):
        self.results.append({"check": name, **evidence})
        (self.out / "results.json").write_text(
            json.dumps(self.results, indent=2) + "\n"
        )
        print(name, evidence, flush=True)

    def payload(self, session, prompt, output, stream=False):
        obj = {
            "model": self.args.served_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": output,
            "stream": stream,
            "temperature": 0,
        }
        if self.args.vllm and stream:
            obj["ignore_eos"] = True
        if session is not None:
            obj["session_params"] = {"session_id": session}
        return obj

    def send(
        self, label, session, prompt="hi", output=1, delay=0, stream=False, extra=None
    ):
        return call(
            self.router + "/v1/chat/completions",
            self.payload(session, prompt, output, stream),
            {"x-test-request": label, "x-test-delay": str(delay), **(extra or {})},
        )

    def entries(self, kind):
        return [
            entry
            for line in self.events.read_text().splitlines()
            if (entry := json.loads(line))["kind"] == kind
        ]

    @contextlib.contextmanager
    def ranks(self, hard, arm):
        if self.args.vllm and "SESSION_H20_RUN" in self.env:
            run = Path(self.env["SESSION_H20_RUN"])
            with (self.out / (arm + "-preflight.log")).open("xb") as log:
                subprocess.run(
                    ["bash", str(HERE / "h20-health.sh"), "compute", str(run)],
                    check=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
        self.urls = []
        with contextlib.ExitStack() as stack:
            for i in range(2):
                p = port()
                url = f"http://127.0.0.1:{p}"
                self.urls.append(url)
                name = f"{arm}-{i}"
                real = self.args.vllm
                total = ([8, 64] if real else [8, 16])[i]
                env = dict(
                    self.env,
                    SESSION_FIXTURE_EVENTS=str(self.events),
                    SESSION_FIXTURE_RANK=str(i),
                    SESSION_FIXTURE_HARD=str(int(hard)),
                    VLLM_SESSION_BLOCK_SIZE="16" if real else "1",
                    VLLM_SESSION_MODEL_LEN="1024" if real else "256",
                    VLLM_SESSION_RANK_BLOCKS=str(total),
                    VLLM_SESSION_RANK_JOURNAL=str(self.out / (name + ".db")),
                )
                if real:
                    env["CUDA_VISIBLE_DEVICES"] = os.environ[
                        "CUDA_VISIBLE_DEVICES"
                    ].split(",")[i]
                    cmd = [
                        real,
                        "serve",
                        self.args.model,
                        "--served-model-name",
                        self.args.served_name,
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(p),
                        "--tensor-parallel-size",
                        "1",
                        "--max-model-len",
                        "1024",
                        "--block-size",
                        "16",
                        "--num-gpu-blocks-override",
                        "256",
                        "--enable-prefix-caching",
                        "--enable-prompt-tokens-details",
                        "--gpu-memory-utilization",
                        "0.90",
                        "--enforce-eager",
                    ]
                    if hard:
                        cmd += ["--middleware", "rank_authority.RankAuthority"]
                else:
                    cmd = [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "fixture_engine:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(p),
                        "--lifespan",
                        "off",
                    ]
                stack.enter_context(
                    process(
                        cmd, env, self.out / (name + ".log"), url, 900 if real else 20
                    )
                )
            yield

    @contextlib.contextmanager
    def routing(self, label, mode, policy="cache_aware", active=1, deadline=45):
        env = dict(self.env, RUST_LOG="info,vllm_router_rs::session_admission=debug")
        if mode != "off":
            conf = self.out / (label + ".json")
            conf.write_text(
                json.dumps(
                    dict(
                        active=active,
                        queued_per_session=1,
                        resident_requests=32,
                        resident_bytes=1 << 20,
                        trusted_session_header="x-trusted-session",
                        rank_capacity=mode == "hard",
                        profiles={"cache": "cache_aware", "batch": "round_robin"},
                    )
                )
            )
            env["VLLM_ROUTER_SESSION_CONFIG"] = str(conf)
        p, mp = port(), port()
        while mp == p:
            mp = port()
        self.metrics = f"http://127.0.0.1:{mp}/metrics"
        self.router = f"http://127.0.0.1:{p}"
        self.routerlog = self.out / (label + ".log")
        cmd = [
            self.args.router,
            "--host",
            "127.0.0.1",
            "--port",
            str(p),
            "--prometheus-host",
            "127.0.0.1",
            "--prometheus-port",
            str(mp),
            "--worker-urls",
            *self.urls,
            "--policy",
            policy,
            "--request-timeout-secs",
            str(deadline),
        ]
        with process(cmd, env, self.routerlog, self.router, 60):
            yield

    def prefix_hits(self):
        rows = call(self.metrics)[1].decode().splitlines()
        return sum(
            int(float(row.rsplit(" ", 1)[1]))
            for row in rows
            if row.startswith("vllm_router_prefix_routing_decisions_total{")
            and 'outcome="prefix_hit"' in row
        )

    def cached(self, label):
        before = self.prefix_hits()
        prompt = "Prefix reuse acceptance. " * (65 if self.args.vllm else 6)
        values = []
        for i in range(4):
            status, raw = self.send(label + str(i), str(i), prompt)
            assert status == 200, (status, raw)
            usage = json.loads(raw)["usage"]
            values.append(usage["prompt_tokens_details"]["cached_tokens"])
        assert max(values[1:]) > 0, values
        delta = self.prefix_hits() - before
        assert delta >= 1, delta
        self.record(label, cached_tokens=values, policy_prefix_hits=delta)

    def fairness(self):
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            a1 = pool.submit(self.send, "A1", "A", "long", 1, 0.65, True)
            wait(lambda: any(e["label"] == "A1" for e in self.entries("start")))
            a2 = pool.submit(self.send, "A2", "A")
            wait(lambda: self.routerlog.read_text().count("session_enqueued") >= 2)
            assert self.send("overflow", "A")[0] == 429
            b1 = pool.submit(self.send, "B1", "B")
            assert b"[DONE]" in a1.result()[1]
            assert a2.result()[0] == b1.result()[0] == 200
        starts = [
            e["label"]
            for e in self.entries("start")
            if e["label"] in {"A1", "A2", "B1", "overflow"}
        ]
        assert starts == ["A1", "B1", "A2"], starts
        assert self.send("conflict", "A", extra={"x-trusted-session": "B"})[0] == 400
        assert (
            self.send("unknown", "A", extra={"x-router-profile": "missing"})[0] == 400
        )
        assert (
            self.send("anonymous", None, extra={"x-router-profile": "batch"})[0] == 200
        )
        self.record("fairness_stream_queue", starts=starts, overflow=429, conflict=400)

    def cancel_and_retry(self):
        logged = self.routerlog.read_text().count("session_enqueued")
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            running = pool.submit(self.send, "running", "cancel", "work", 1, 1.0, True)
            wait(lambda: any(e["label"] == "running" for e in self.entries("start")))
            conn = http.client.HTTPConnection(self.router.split("//")[1])
            conn.request(
                "POST",
                "/v1/chat/completions",
                json.dumps(self.payload("cancel", "discard", 1)),
                {"Content-Type": "application/json", "x-test-request": "abandoned"},
            )
            wait(
                lambda: (
                    self.routerlog.read_text().count("session_enqueued") == logged + 2
                )
            )
            conn.close()
            assert b"[DONE]" in running.result()[1]
        assert self.send("reused", "cancel")[0] == 200
        assert not any(e["label"] == "abandoned" for e in self.entries("start"))
        before = self.routerlog.read_text().count("session_granted")
        assert self.send("retry", "retry-session")[0] == 200
        assert self.routerlog.read_text().count("session_granted") == before + 1
        assert sum(e["label"] == "retry" for e in self.entries("start")) == 2
        self.record("cancel_and_retry", cancelled_sends=0, backend_attempts=2, grants=1)

    def deadline(self):
        body = self.payload("hold", "hi", 10)
        auth = {"x-router-capacity-key": self.secret}
        quote = json.loads(call(self.urls[1] + "/_session/quote", body, auth)[1])
        import uuid

        attempt = str(uuid.uuid4())
        assert (
            call(self.urls[1] + "/_session/lease", dict(quote, id=attempt), auth)[0]
            == 201
        )
        assert self.send("timed-out", "waiting", "hi", 10)[0] == 504
        assert not any(e["label"] == "timed-out" for e in self.entries("start"))
        release = urllib.request.Request(
            self.urls[1] + "/_session/lease/" + attempt,
            method="DELETE",
            headers={**auth, "x-router-epoch": quote["epoch"]},
        )
        with urllib.request.urlopen(release) as response:
            assert response.status == 200
        assert self.send("resumed", "waiting", "hi", 10)[0] == 200
        self.record(
            "capacity_deadline", status=504, expired_sends=0, reused_session=200
        )

    def real_fairness(self):
        def ids(event):
            rows = (
                re.sub(r"\x1b\[[0-9;]*m", "", line)
                for line in self.routerlog.read_text().splitlines()
                if event in line
            )
            return [re.search(r"request=([a-f0-9-]{36})", line)[1] for line in rows]

        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            a1 = pool.submit(self.send, "A1", "A", "count to 800", 700, 0, True)
            wait(lambda: len(ids("session_granted")) == 1)
            a2 = pool.submit(self.send, "A2", "A")
            wait(lambda: len(ids("session_enqueued")) == 2)
            b = pool.submit(self.send, "B1", "B")
            wait(lambda: len(ids("session_enqueued")) == 3)
            assert b"[DONE]" in a1.result()[1]
            assert b.result()[0] == a2.result()[0] == 200
        arrivals = ids("session_enqueued")
        assert ids("session_granted") == [arrivals[0], arrivals[2], arrivals[1]]
        self.record("real_fairness", arrival=arrivals, grant=ids("session_granted"))

    def disconnect(self):
        body = self.payload("drop", "hi", 10, True)
        req = urllib.request.Request(
            self.router + "/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "x-test-delay": ".7",
                "x-test-request": "disconnect",
            },
        )
        with urllib.request.urlopen(req) as response:
            assert response.readline().startswith(b"data:")
        auth = {"x-router-capacity-key": self.secret}

        def free():
            return json.loads(call(self.urls[1] + "/_session/quote", body, auth)[1])[
                "free"
            ]

        assert free() == 4
        assert self.send("after-drop", "drop", "a", 1)[0] == 200
        starts = next(e for e in self.entries("start") if e["label"] == "after-drop")
        wait(lambda: free() == 16)
        ended = next(e for e in self.entries("end") if e["label"] == "disconnect")
        assert starts["t"] < ended["t"]
        self.record("disconnect", free_after_close=4, free_after_finish=16)

    def capacity(self):
        output = 620 if self.args.vllm else 10
        demand = self.payload("one", "hi", output)
        auth = {"x-router-capacity-key": self.secret}
        quotes = [
            json.loads(call(url + "/_session/quote", demand, auth)[1])
            for url in self.urls
        ]
        assert (
            quotes[0]["total"] < quotes[0]["required"]
            and 0 < quotes[1]["required"] <= quotes[1]["free"]
        ), quotes
        if self.args.vllm:
            # The output alone fits the model limit. Adding the chat template exceeds it.
            # vLLM may reject during render, before it returns any token ids.
            before = self.routerlog.read_text().count("session_rank_claim")
            assert self.send("invalid-context", "invalid", "hi", 1024)[0] == 400
            unbounded = self.payload("invalid", "context " * 1500, 1)
            del unbounded["max_tokens"]
            assert call(self.router + "/v1/chat/completions", unbounded)[0] == 400
            assert self.routerlog.read_text().count("session_rank_claim") == before
            free = [
                json.loads(call(url + "/_session/quote", demand, auth)[1])["free"]
                for url in self.urls
            ]
            assert free == [q["total"] for q in quotes]
            self.record("render_rejection", bounded=400, unbounded=400, new_claims=0, free=free)
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                fs = [
                    pool.submit(
                        self.send, "large" + str(i), str(i), "hi", output, 0, True
                    )
                    for i in range(2)
                ]
                for f in fs:
                    status, raw = f.result()
                    assert status == 200 and b"[DONE]" in raw, (status, raw)
                assert "session_rank_park" in self.routerlog.read_text()
        else:
            with concurrent.futures.ThreadPoolExecutor(3) as pool:
                f = pool.submit(self.send, "large1", "one", "hi", output, 0.7, True)
                wait(lambda: any(e["label"] == "large1" for e in self.entries("start")))
                other = pool.submit(self.send, "large2", "two", "hi", output)
                wait(lambda: "session_rank_park" in self.routerlog.read_text())
                small = pool.submit(self.send, "small", "three", "a", 2)
                assert small.result()[0] == 200
                assert f.result()[0] == other.result()[0] == 200
            starts = {
                e["label"]: e
                for e in self.entries("start")
                if e["label"] in {"large1", "large2", "small"}
            }
            end = next(e for e in self.entries("end") if e["label"] == "large1")
            assert (
                starts["small"]["t"] < end["t"] <= starts["large2"]["t"]
                and starts["small"]["rank"] == 0
            ), starts
            assert starts["large1"]["rank"] == starts["large2"]["rank"] == 1
        assert self.send("oversize", "other", "hi", 2048)[0] == 400
        assert call(self.urls[1] + "/v1/chat/completions", demand)[0] == 409

        def settled():
            return all(
                json.loads(call(url + "/_session/quote", demand, auth)[1])["free"]
                == q["total"]
                for url, q in zip(self.urls, quotes)
            )

        wait(settled)
        claims = [
            line
            for line in self.routerlog.read_text().splitlines()
            if "session_rank_claim" in line
        ]
        assert sum(self.urls[1] in line for line in claims) >= 2, claims
        self.record(
            "capacity",
            total=[q["total"] for q in quotes],
            required=[q["required"] for q in quotes],
            observed_winner=self.urls[1],
            oversize=400,
            unleased=409,
            fully_reclaimed=True,
        )

    def run(self):
        with self.ranks(False, "native-rank"):
            with self.routing("native", "off"):
                self.cached("native-cache")
        with self.ranks(False, "session-rank"):
            with self.routing("phase1", "session"):
                if not self.args.vllm:
                    self.fairness()
                    self.cancel_and_retry()
                else:
                    self.real_fairness()
                self.cached("admitted-cache")
        with self.ranks(True, "capacity-rank"):
            with self.routing("phase2", "hard", active=2):
                self.capacity()
                if not self.args.vllm:
                    self.disconnect()
            if not self.args.vllm:
                with self.routing("phase2-timeout", "hard", deadline=1):
                    self.deadline()
            with self.routing("phase2-kv", "hard", policy="kv_aware", active=2):
                status, raw = self.send("kv", "kv", "hi", 10)
                assert status == 200, (status, raw)
                assert (
                    self.send(
                        "secondary-cache",
                        "two",
                        "hi",
                        10,
                        extra={"x-router-profile": "cache"},
                    )[0]
                    == 200
                )
                self.record("kv_aware_capacity", status=status, secondary_cache=200)
        self.record("completed", backend="vllm" if self.args.vllm else "fixture")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--router", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--vllm")
    parser.add_argument("--model")
    parser.add_argument("--served-name", default="test-model")
    args = parser.parse_args()
    if args.vllm and not args.model:
        parser.error("--model is required with --vllm")
    Run(args).run()
