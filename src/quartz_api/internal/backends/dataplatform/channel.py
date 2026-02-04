"""GRPC channel with enriched metadata."""

from grpclib.client import Channel, Stream
from grpclib.const import Cardinality
from grpclib.metadata import Deadline, _MetadataLike
from grpclib.stream import _RecvType, _SendType
from typing_extensions import override


class EnrichedChannel(Channel):
    """GRPC channel implementation that adds traceid to metadata of calls."""

    def __init__(self, host: str, port: int) -> None:
        """Initialize EnrichedChannel with host and port."""
        super().__init__(host, port)

    @override
    def request(
        self,
        name: str,
        cardinality: Cardinality,
        request_type: type[_SendType],
        reply_type: type[_RecvType],
        *,
        timeout: float | None = None,
        deadline: Deadline | None = None,
        metadata: _MetadataLike | None = None,
    ) -> Stream[_SendType, _RecvType]:
        from quartz_api.internal.middleware.trace import get_trace_id

        trace_id = get_trace_id()
        if metadata is not None:
            metadata["traceid"] = trace_id
        return super().request(
            name=name,
            cardinality=cardinality,
            request_type=request_type,
            reply_type=reply_type,
            timeout=timeout,
            deadline=deadline,
            metadata=metadata,
        )
