"""In-process async publish/subscribe bus with typed topics (task book §4.8).

The API mirrors every topic to the UI over a WebSocket by subscribing with `ALL_TOPICS`.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from enum import StrEnum

from rulecam.log import get_logger

_log = get_logger(__name__)


class Topic(StrEnum):
    """Every topic on the bus. Payload types are documented in task book §4.8."""

    CHUNK_READY = "chunk.ready"
    TRACKS_UPDATED = "tracks.updated"
    CANDIDATE_NEW = "candidate.new"
    VERDICT_NEW = "verdict.new"
    ACTION_DONE = "action.done"
    COVERAGE_CHANGED = "coverage.changed"
    PLAN_UPDATED = "plan.updated"
    CI_RESULT = "ci.result"
    SURPRISE_NEW = "surprise.new"
    ADVICE_NEW = "advice.new"
    RELAY_MODEL_USED = "relay.model_used"
    MODE_CHANGED = "mode.changed"
    ALERT_NEW = "alert.new"


ALL_TOPICS: frozenset[Topic] = frozenset(Topic)


def _on_loop(loop: asyncio.AbstractEventLoop) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


@dataclass(frozen=True)
class Message:
    """A published message: its topic and payload."""

    topic: Topic
    payload: object


class Bus:
    """Fan-out bus: each subscriber gets every message published after it subscribed.

    Publishing never blocks. A subscriber whose queue is full drops the message and a warning is
    logged, so one slow consumer cannot stall the pipeline.
    """

    def __init__(self, max_queue: int = 1000) -> None:
        """Create an empty bus.

        Args:
            max_queue: Per-subscriber queue capacity.
        """
        self._max_queue = max_queue
        self._queues: dict[Topic, list[asyncio.Queue[Message]]] = defaultdict(list)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        """Make `publish` safe to call from worker threads by delivering on `loop`.

        Args:
            loop: The event loop the subscribers run on (`None` unbinds).
        """
        self._loop = loop

    def publish(self, topic: Topic, payload: object) -> None:
        """Deliver `payload` to every current subscriber of `topic`.

        When the bus is bound to a running loop and this is called from another thread, delivery
        is handed to that loop with `call_soon_threadsafe` (asyncio queues are not thread-safe).

        Args:
            topic: The topic to publish on.
            payload: Usually a contract model; see §4.8 for the type per topic.
        """
        message = Message(topic=topic, payload=payload)
        loop = self._loop
        if loop is not None and loop.is_running() and not _on_loop(loop):
            loop.call_soon_threadsafe(self._deliver, message)
            return
        self._deliver(message)

    def _deliver(self, message: Message) -> None:
        topic = message.topic
        for queue in self._queues[topic]:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                _log.warning("bus_subscriber_full", topic=str(topic))

    def subscriber_count(self, topic: Topic) -> int:
        """Return how many subscribers `topic` currently has."""
        return len(self._queues[topic])

    async def subscribe(self, topics: frozenset[Topic] | Topic) -> AsyncGenerator[Message, None]:
        """Yield messages for one or more topics until the consumer stops iterating.

        Wrap the call in `contextlib.aclosing(...)` so the subscription is removed as soon as
        the consumer stops; a bare `break` leaves cleanup to garbage collection.

        Args:
            topics: A single topic or a set of topics (e.g. `ALL_TOPICS`).

        Yields:
            Messages in publish order.
        """
        wanted = frozenset({topics}) if isinstance(topics, Topic) else topics
        queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=self._max_queue)
        for topic in wanted:
            self._queues[topic].append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            for topic in wanted:
                self._queues[topic].remove(queue)
