import asyncio
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "py_src/vllm_router"))
from rank_authority import Ledger, RankAuthority


class AuthorityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "rank.sqlite"
        self.ledger = Ledger(self.path, 16)
        self.addCleanup(self.close)

    def close(self):
        self.ledger.db.close()
        self.ledger.lock.close()

    def test_replay_fencing_and_restart(self):
        ledger = self.ledger
        old = ledger.epoch
        first, second = (str(uuid.uuid4()) for _ in range(2))
        self.assertEqual(ledger.claim(first, old, "hash", 12), 201)
        self.assertEqual(ledger.claim(first, old, "hash", 12), 200)
        self.assertEqual(ledger.claim(second, old, "hash", 12), 409)
        self.assertFalse(ledger.redeem(first, old, "changed"))
        self.assertTrue(ledger.redeem(first, old, "hash"))
        self.assertFalse(ledger.redeem(first, old, "hash"))
        ledger.release(first, old, "reserved")
        self.assertEqual(ledger.available(), 4)
        self.close()
        self.ledger = Ledger(self.path, 16)
        self.assertNotEqual(self.ledger.epoch, old)
        self.assertEqual(self.ledger.available(), 4)
        self.assertEqual(self.ledger.claim(second, old, "hash", 1), 409)
        self.ledger.release(first, self.ledger.epoch, "running")
        self.assertEqual(self.ledger.available(), 4)

    async def test_rank_requires_matching_engine_limits(self):
        authority = object.__new__(RankAuthority)
        authority.block, authority.limit, authority.ledger = 16, 1024, self.ledger
        authority.verify_lock = asyncio.Lock()
        for block, total, model_len, expected in [
            (16, 15, 1024, False),
            (8, 16, 1024, False),
            (16, 16, 2048, False),
            (16, 16, 1024, True),
        ]:
            with self.subTest(block=block, total=total, model_len=model_len):
                authority.verified = False

                async def inspect(scope, path):
                    if path == "/metrics":
                        return (
                            200,
                            f'vllm:cache_config_info{{block_size="{block}",num_gpu_blocks="{total}"}} 1'.encode(),
                        )
                    return 200, json.dumps(
                        {"data": [{"max_model_len": model_len}]}
                    ).encode()

                authority.inspect_engine = inspect
                self.assertEqual(await authority.verify({}), expected)

    async def test_stream_requires_every_finish_and_drains_disconnect(self):
        frames = [
            ([{"choices": [{"index": 0, "finish_reason": "stop"}]}], 4),
            ([{"error": {"message": "failed"}}], 4),
            (
                [
                    {
                        "choices": [
                            {"index": 0, "finish_reason": "length"},
                            {"index": 1, "finish_reason": "stop"},
                        ]
                    }
                ],
                16,
            ),
        ]
        for payloads, expected in frames:
            with self.subTest(expected=expected):
                ledger = self.ledger
                event = asyncio.Event()

                async def engine(scope, receive, send):
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 200,
                            "headers": [(b"content-type", b"text/event-stream")],
                        }
                    )
                    await event.wait()
                    data = (
                        b"".join(
                            b"data: " + json.dumps(obj).encode() + b"\n\n"
                            for obj in payloads
                        )
                        + b"data: [DONE]\n\n"
                    )
                    for chunk in (data[:8], data[8:]):
                        await send(
                            {
                                "type": "http.response.body",
                                "body": chunk,
                                "more_body": True,
                            }
                        )
                    await send({"type": "http.response.body", "body": b""})

                async def disconnected(msg):
                    raise OSError("peer closed")

                authority = object.__new__(RankAuthority)
                authority.ledger, authority.app = ledger, engine
                attempt = str(uuid.uuid4())
                ledger.claim(attempt, ledger.epoch, "d", 12)
                ledger.redeem(attempt, ledger.epoch, "d")
                drain = asyncio.create_task(
                    authority.run_leased(
                        {}, b'{"n":2}', disconnected, attempt, ledger.epoch
                    )
                )
                await asyncio.sleep(0)
                ledger.release(attempt, ledger.epoch, "reserved")
                self.assertEqual(ledger.available(), 4)
                event.set()
                await drain
                self.assertEqual(ledger.available(), expected)
                ledger.release(attempt, ledger.epoch, "running")
                ledger.release(attempt, ledger.epoch, "running")
                self.assertEqual(ledger.available(), 16)


if __name__ == "__main__":
    unittest.main()
