import httpx
import pytest

from backend.receiver.app import create_app


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=create_app(fail_first_n=2))
    async with httpx.AsyncClient(transport=transport, base_url="http://receiver") as c:
        yield c


async def test_fails_first_n_per_batch_then_accepts(client):
    codes = [
        (await client.post("/callbacks/receive", json={"batch_id": "b1"})).status_code
        for _ in range(3)
    ]
    assert codes == [500, 500, 200]
    other = await client.post("/callbacks/receive", json={"batch_id": "b2"})
    assert other.status_code == 500  # counted per batch
    got = (await client.get("/callbacks/received")).json()
    assert got["attempts"] == {"b1": 3, "b2": 1} and len(got["received"]) == 1


async def test_config_and_reset(client):
    assert (await client.put("/callbacks/config", json={"fail_first_n": 0})).status_code == 200
    assert (await client.post("/callbacks/receive", json={"batch_id": "b"})).status_code == 200
    await client.post("/callbacks/reset")
    assert (await client.get("/callbacks/received")).json() == {"attempts": {}, "received": []}
    assert (await client.get("/healthz")).json() == {"status": "ok"}
