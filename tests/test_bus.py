from __future__ import annotations

import asyncio
from contextlib import aclosing

from rulecam.bus import ALL_TOPICS, Bus, Message, Topic


async def _collect(bus: Bus, topics: frozenset[Topic] | Topic, n: int) -> list[Message]:
    received: list[Message] = []
    async with aclosing(bus.subscribe(topics)) as messages:
        async for message in messages:
            received.append(message)
            if len(received) == n:
                break
    return received


async def test_subscriber_receives_messages_in_order() -> None:
    bus = Bus()
    task = asyncio.create_task(_collect(bus, Topic.CHUNK_READY, 2))
    await asyncio.sleep(0)

    bus.publish(Topic.CHUNK_READY, "a")
    bus.publish(Topic.CANDIDATE_NEW, "ignored")
    bus.publish(Topic.CHUNK_READY, "b")

    messages = await asyncio.wait_for(task, timeout=1)
    assert [m.payload for m in messages] == ["a", "b"]
    assert all(m.topic is Topic.CHUNK_READY for m in messages)


async def test_all_topics_subscription_mirrors_everything() -> None:
    bus = Bus()
    task = asyncio.create_task(_collect(bus, ALL_TOPICS, 2))
    await asyncio.sleep(0)

    bus.publish(Topic.RELAY_MODEL_USED, {"model": "glimmer"})
    bus.publish(Topic.MODE_CHANGED, {"mode": "replay"})

    messages = await asyncio.wait_for(task, timeout=1)
    assert [m.topic for m in messages] == [Topic.RELAY_MODEL_USED, Topic.MODE_CHANGED]


async def test_unsubscribes_when_consumer_stops() -> None:
    bus = Bus()
    task = asyncio.create_task(_collect(bus, Topic.PLAN_UPDATED, 1))
    await asyncio.sleep(0)
    assert bus.subscriber_count(Topic.PLAN_UPDATED) == 1

    bus.publish(Topic.PLAN_UPDATED, "plan")
    await asyncio.wait_for(task, timeout=1)

    assert bus.subscriber_count(Topic.PLAN_UPDATED) == 0


async def test_full_subscriber_drops_instead_of_blocking() -> None:
    bus = Bus(max_queue=1)
    iterator = bus.subscribe(Topic.CI_RESULT)

    async def next_message() -> Message:
        return await anext(iterator)

    first = asyncio.create_task(next_message())
    await asyncio.sleep(0)

    bus.publish(Topic.CI_RESULT, 1)
    bus.publish(Topic.CI_RESULT, 2)  # queue may be full: must not raise or block

    message = await asyncio.wait_for(first, timeout=1)
    assert message.payload == 1
    await iterator.aclose()


def test_publish_without_subscribers_is_a_no_op() -> None:
    Bus().publish(Topic.SURPRISE_NEW, "nobody listening")
