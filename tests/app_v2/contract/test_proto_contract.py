from pathlib import Path
import subprocess
import sys

from google.protobuf.descriptor import FieldDescriptor


ROOT = Path(__file__).resolve().parents[3]
PROTO_FILE = ROOT / "proto" / "v2" / "customer_service.proto"
GENERATED_DIR = ROOT / "src" / "app_v2" / "transport" / "grpc" / "generated"


def test_generated_python_matches_the_committed_proto(tmp_path):
    generated_root = tmp_path / "src"
    generated_root.mkdir()
    virtual_proto = "app_v2/transport/grpc/generated/customer_service.proto"

    subprocess.run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"--proto_path=app_v2/transport/grpc/generated={ROOT / 'proto' / 'v2'}",
            f"--python_out={generated_root}",
            f"--grpc_python_out={generated_root}",
            f"--pyi_out={generated_root}",
            virtual_proto,
        ],
        check=True,
    )

    for filename in (
        "customer_service_pb2.py",
        "customer_service_pb2.pyi",
        "customer_service_pb2_grpc.py",
    ):
        regenerated_file = generated_root / "app_v2" / "transport" / "grpc" / "generated" / filename
        assert regenerated_file.read_bytes() == (GENERATED_DIR / filename).read_bytes()


def test_stream_frame_separates_control_frames_from_business_events():
    from app_v2.transport.grpc.generated import customer_service_pb2 as pb

    frame = pb.StreamFrame.DESCRIPTOR

    assert [oneof.name for oneof in frame.oneofs] == ["frame"]
    assert {field.name for field in frame.oneofs_by_name["frame"].fields} == {
        "handshake_request",
        "handshake_response",
        "client_event",
        "server_event",
        "event_ack",
    }

    ack = pb.EventAck.DESCRIPTOR
    assert {field.name for field in ack.fields} == {
        "stream_epoch",
        "acked_event_id",
        "acked_sequence",
    }
    assert "conversation_sequence" not in ack.fields_by_name

    assert not pb.HandshakeRequest.DESCRIPTOR.fields
    assert set(pb.HandshakeResponse.DESCRIPTOR.fields_by_name) == {"stream_epoch"}


def test_only_cross_wire_business_events_are_exposed():
    from app_v2.transport.grpc.generated import customer_service_pb2 as pb

    client_payloads = pb.ClientEvent.DESCRIPTOR.oneofs_by_name["payload"].fields
    server_payloads = pb.ServerEvent.DESCRIPTOR.oneofs_by_name["payload"].fields

    assert {field.name for field in client_payloads} == {
        "user_message",
        "control_directive_result",
        "conversation_status_update",
    }
    assert {field.name for field in server_payloads} == {
        "assistant_message",
        "control_directive",
        "service_error",
    }

    wire_message_names = set(pb.DESCRIPTOR.message_types_by_name)
    assert wire_message_names.isdisjoint(
        {
            "AssistantMessageSendResult",
            "IdleFollowupDueEvent",
            "IdleCloseDueEvent",
            "CapabilityResultEvent",
            "CapabilityPendingDueEvent",
            "ResumeDeferredMessagesEvent",
        }
    )


def test_event_envelopes_carry_agent_but_not_runtime_or_trace_correlation():
    from app_v2.transport.grpc.generated import customer_service_pb2 as pb

    expected_common_fields = {
        "event_id",
        "tenant_id",
        "agent_id",
        "conversation_id",
        "conversation_sequence",
        "occurred_at",
    }

    for event_descriptor in (pb.ClientEvent.DESCRIPTOR, pb.ServerEvent.DESCRIPTOR):
        payload_fields = {field.name for field in event_descriptor.oneofs_by_name["payload"].fields}
        actual_common_fields = {field.name for field in event_descriptor.fields} - payload_fields
        assert actual_common_fields == expected_common_fields
        assert event_descriptor.fields_by_name["occurred_at"].message_type.full_name == (
            "google.protobuf.Timestamp"
        )
        assert "runtime_version" not in actual_common_fields
        assert "correlation_id" not in actual_common_fields
        assert "causation_id" not in actual_common_fields


def test_content_part_is_one_typed_object_with_string_content():
    from app_v2.transport.grpc.generated import customer_service_pb2 as pb

    message = pb.ConversationMessage.DESCRIPTOR
    content_field = message.fields_by_name["content_part"]
    assert not content_field.is_repeated
    assert content_field.message_type.full_name == "ai.customer_service.v2.ContentPart"

    content = pb.ContentPart.DESCRIPTOR
    assert set(content.fields_by_name) == {"content_type", "content"}
    assert not content.oneofs
    assert content.fields_by_name["content"].type == FieldDescriptor.TYPE_STRING
    assert {value.name for value in pb.ContentType.DESCRIPTOR.values} == {
        "CONTENT_TYPE_UNSPECIFIED",
        "CONTENT_TYPE_TEXT",
        "CONTENT_TYPE_IMAGE",
        "CONTENT_TYPE_VIDEO",
        "CONTENT_TYPE_AUDIO",
    }


def test_wire_contract_uses_no_dynamic_payload_container():
    from app_v2.transport.grpc.generated import customer_service_pb2 as pb

    dependency_names = {dependency.name for dependency in pb.DESCRIPTOR.dependencies}

    assert "google/protobuf/any.proto" not in dependency_names
    assert "google/protobuf/struct.proto" not in dependency_names
