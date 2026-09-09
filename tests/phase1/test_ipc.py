import threading

import pytest

from inspection_platform.ipc import (
    AuthenticatedPipeServer,
    IpcEnvelope,
    IpcMessageTooLarge,
    MessageType,
    connect_pipe,
    decode_envelope,
    encode_envelope,
)


def test_authenticated_named_pipe_transfers_typed_json() -> None:
    server = AuthenticatedPipeServer()
    received = []

    def serve() -> None:
        with server.accept() as connection:
            incoming = connection.receive()
            received.append(incoming)
            connection.send(
                IpcEnvelope(
                    message_type=MessageType.HEALTH,
                    request_id=incoming.request_id,
                    cycle_id=incoming.cycle_id,
                    request_sequence=incoming.request_sequence,
                    payload={"status": "healthy"},
                )
            )

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        with connect_pipe(server.address, server.auth_key) as client:
            outgoing = IpcEnvelope(
                message_type=MessageType.HEALTH,
                request_id="health-1",
                cycle_id="cycle-1",
                request_sequence=0,
            )
            client.send(outgoing)
            response = client.receive()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert received == [outgoing]
        assert response.request_id == outgoing.request_id
        assert response.payload == {"status": "healthy"}
    finally:
        server.close()


def test_pipe_message_is_bounded_before_send_or_decode() -> None:
    envelope = IpcEnvelope(
        message_type=MessageType.EVENT,
        request_id="large-1",
        request_sequence=0,
        payload={"value": "x" * (1024**2)},
    )
    with pytest.raises(IpcMessageTooLarge):
        encode_envelope(envelope)
    with pytest.raises(IpcMessageTooLarge):
        decode_envelope(b"x" * (1024**2 + 1))


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(Exception):
        decode_envelope(
            b'{"contract_version":"1.0","message_type":"health",'
            b'"request_id":"x","request_sequence":0,"payload":{},"extra":true}'
        )
