import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ContentType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONTENT_TYPE_UNSPECIFIED: _ClassVar[ContentType]
    CONTENT_TYPE_TEXT: _ClassVar[ContentType]
    CONTENT_TYPE_IMAGE: _ClassVar[ContentType]
    CONTENT_TYPE_VIDEO: _ClassVar[ContentType]
    CONTENT_TYPE_AUDIO: _ClassVar[ContentType]

class SenderRole(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SENDER_ROLE_UNSPECIFIED: _ClassVar[SenderRole]
    SENDER_ROLE_CUSTOMER: _ClassVar[SenderRole]
    SENDER_ROLE_AI_ASSISTANT: _ClassVar[SenderRole]
    SENDER_ROLE_HUMAN_AGENT: _ClassVar[SenderRole]
    SENDER_ROLE_SYSTEM: _ClassVar[SenderRole]

class ControlDirectiveOutcome(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONTROL_DIRECTIVE_OUTCOME_UNSPECIFIED: _ClassVar[ControlDirectiveOutcome]
    CONTROL_DIRECTIVE_OUTCOME_SUCCEEDED: _ClassVar[ControlDirectiveOutcome]
    CONTROL_DIRECTIVE_OUTCOME_ALREADY_SUCCEEDED: _ClassVar[ControlDirectiveOutcome]
    CONTROL_DIRECTIVE_OUTCOME_REJECTED_STALE: _ClassVar[ControlDirectiveOutcome]
    CONTROL_DIRECTIVE_OUTCOME_FAILED_RETRYABLE: _ClassVar[ControlDirectiveOutcome]
    CONTROL_DIRECTIVE_OUTCOME_FAILED_FINAL: _ClassVar[ControlDirectiveOutcome]
    CONTROL_DIRECTIVE_OUTCOME_UNKNOWN_AFTER_EXTERNAL_SUCCESS: _ClassVar[ControlDirectiveOutcome]

class ServiceStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SERVICE_STATUS_UNSPECIFIED: _ClassVar[ServiceStatus]
    SERVICE_STATUS_BOT_ACTIVE: _ClassVar[ServiceStatus]
    SERVICE_STATUS_HANDOFF_PENDING: _ClassVar[ServiceStatus]
    SERVICE_STATUS_HUMAN_ACTIVE: _ClassVar[ServiceStatus]
    SERVICE_STATUS_CLOSED: _ClassVar[ServiceStatus]

class ControlDirectiveType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONTROL_DIRECTIVE_TYPE_UNSPECIFIED: _ClassVar[ControlDirectiveType]
    CONTROL_DIRECTIVE_TYPE_HANDOFF_REQUESTED: _ClassVar[ControlDirectiveType]
CONTENT_TYPE_UNSPECIFIED: ContentType
CONTENT_TYPE_TEXT: ContentType
CONTENT_TYPE_IMAGE: ContentType
CONTENT_TYPE_VIDEO: ContentType
CONTENT_TYPE_AUDIO: ContentType
SENDER_ROLE_UNSPECIFIED: SenderRole
SENDER_ROLE_CUSTOMER: SenderRole
SENDER_ROLE_AI_ASSISTANT: SenderRole
SENDER_ROLE_HUMAN_AGENT: SenderRole
SENDER_ROLE_SYSTEM: SenderRole
CONTROL_DIRECTIVE_OUTCOME_UNSPECIFIED: ControlDirectiveOutcome
CONTROL_DIRECTIVE_OUTCOME_SUCCEEDED: ControlDirectiveOutcome
CONTROL_DIRECTIVE_OUTCOME_ALREADY_SUCCEEDED: ControlDirectiveOutcome
CONTROL_DIRECTIVE_OUTCOME_REJECTED_STALE: ControlDirectiveOutcome
CONTROL_DIRECTIVE_OUTCOME_FAILED_RETRYABLE: ControlDirectiveOutcome
CONTROL_DIRECTIVE_OUTCOME_FAILED_FINAL: ControlDirectiveOutcome
CONTROL_DIRECTIVE_OUTCOME_UNKNOWN_AFTER_EXTERNAL_SUCCESS: ControlDirectiveOutcome
SERVICE_STATUS_UNSPECIFIED: ServiceStatus
SERVICE_STATUS_BOT_ACTIVE: ServiceStatus
SERVICE_STATUS_HANDOFF_PENDING: ServiceStatus
SERVICE_STATUS_HUMAN_ACTIVE: ServiceStatus
SERVICE_STATUS_CLOSED: ServiceStatus
CONTROL_DIRECTIVE_TYPE_UNSPECIFIED: ControlDirectiveType
CONTROL_DIRECTIVE_TYPE_HANDOFF_REQUESTED: ControlDirectiveType

class StreamFrame(_message.Message):
    __slots__ = ("handshake_request", "handshake_response", "client_event", "server_event", "event_ack")
    HANDSHAKE_REQUEST_FIELD_NUMBER: _ClassVar[int]
    HANDSHAKE_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    CLIENT_EVENT_FIELD_NUMBER: _ClassVar[int]
    SERVER_EVENT_FIELD_NUMBER: _ClassVar[int]
    EVENT_ACK_FIELD_NUMBER: _ClassVar[int]
    handshake_request: HandshakeRequest
    handshake_response: HandshakeResponse
    client_event: ClientEvent
    server_event: ServerEvent
    event_ack: EventAck
    def __init__(self, handshake_request: _Optional[_Union[HandshakeRequest, _Mapping]] = ..., handshake_response: _Optional[_Union[HandshakeResponse, _Mapping]] = ..., client_event: _Optional[_Union[ClientEvent, _Mapping]] = ..., server_event: _Optional[_Union[ServerEvent, _Mapping]] = ..., event_ack: _Optional[_Union[EventAck, _Mapping]] = ...) -> None: ...

class HandshakeRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class HandshakeResponse(_message.Message):
    __slots__ = ("stream_epoch",)
    STREAM_EPOCH_FIELD_NUMBER: _ClassVar[int]
    stream_epoch: str
    def __init__(self, stream_epoch: _Optional[str] = ...) -> None: ...

class EventAck(_message.Message):
    __slots__ = ("stream_epoch", "acked_event_id", "acked_sequence")
    STREAM_EPOCH_FIELD_NUMBER: _ClassVar[int]
    ACKED_EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    ACKED_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    stream_epoch: str
    acked_event_id: str
    acked_sequence: int
    def __init__(self, stream_epoch: _Optional[str] = ..., acked_event_id: _Optional[str] = ..., acked_sequence: _Optional[int] = ...) -> None: ...

class ClientEvent(_message.Message):
    __slots__ = ("event_id", "tenant_id", "agent_id", "conversation_id", "conversation_sequence", "occurred_at", "user_message", "control_directive_result", "conversation_status_update")
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    TENANT_ID_FIELD_NUMBER: _ClassVar[int]
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    USER_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    CONTROL_DIRECTIVE_RESULT_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_STATUS_UPDATE_FIELD_NUMBER: _ClassVar[int]
    event_id: str
    tenant_id: str
    agent_id: str
    conversation_id: str
    conversation_sequence: int
    occurred_at: _timestamp_pb2.Timestamp
    user_message: UserMessage
    control_directive_result: ControlDirectiveResult
    conversation_status_update: ConversationStatusUpdate
    def __init__(self, event_id: _Optional[str] = ..., tenant_id: _Optional[str] = ..., agent_id: _Optional[str] = ..., conversation_id: _Optional[str] = ..., conversation_sequence: _Optional[int] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., user_message: _Optional[_Union[UserMessage, _Mapping]] = ..., control_directive_result: _Optional[_Union[ControlDirectiveResult, _Mapping]] = ..., conversation_status_update: _Optional[_Union[ConversationStatusUpdate, _Mapping]] = ...) -> None: ...

class ServerEvent(_message.Message):
    __slots__ = ("event_id", "tenant_id", "agent_id", "conversation_id", "conversation_sequence", "occurred_at", "assistant_message", "control_directive", "service_error")
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    TENANT_ID_FIELD_NUMBER: _ClassVar[int]
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    ASSISTANT_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    CONTROL_DIRECTIVE_FIELD_NUMBER: _ClassVar[int]
    SERVICE_ERROR_FIELD_NUMBER: _ClassVar[int]
    event_id: str
    tenant_id: str
    agent_id: str
    conversation_id: str
    conversation_sequence: int
    occurred_at: _timestamp_pb2.Timestamp
    assistant_message: AssistantMessage
    control_directive: ControlDirective
    service_error: ServiceError
    def __init__(self, event_id: _Optional[str] = ..., tenant_id: _Optional[str] = ..., agent_id: _Optional[str] = ..., conversation_id: _Optional[str] = ..., conversation_sequence: _Optional[int] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., assistant_message: _Optional[_Union[AssistantMessage, _Mapping]] = ..., control_directive: _Optional[_Union[ControlDirective, _Mapping]] = ..., service_error: _Optional[_Union[ServiceError, _Mapping]] = ...) -> None: ...

class ContentPart(_message.Message):
    __slots__ = ("content_type", "content")
    CONTENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    content_type: ContentType
    content: str
    def __init__(self, content_type: _Optional[_Union[ContentType, str]] = ..., content: _Optional[str] = ...) -> None: ...

class ConversationMessage(_message.Message):
    __slots__ = ("message_id", "sender_role", "content_part", "sent_at")
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    SENDER_ROLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_PART_FIELD_NUMBER: _ClassVar[int]
    SENT_AT_FIELD_NUMBER: _ClassVar[int]
    message_id: str
    sender_role: SenderRole
    content_part: ContentPart
    sent_at: _timestamp_pb2.Timestamp
    def __init__(self, message_id: _Optional[str] = ..., sender_role: _Optional[_Union[SenderRole, str]] = ..., content_part: _Optional[_Union[ContentPart, _Mapping]] = ..., sent_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class UserMessage(_message.Message):
    __slots__ = ("message", "history_snapshot")
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    HISTORY_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    message: ConversationMessage
    history_snapshot: _containers.RepeatedCompositeFieldContainer[ConversationMessage]
    def __init__(self, message: _Optional[_Union[ConversationMessage, _Mapping]] = ..., history_snapshot: _Optional[_Iterable[_Union[ConversationMessage, _Mapping]]] = ...) -> None: ...

class ControlDirectiveResult(_message.Message):
    __slots__ = ("directive_id", "outcome", "completed_stages", "error_code")
    DIRECTIVE_ID_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_STAGES_FIELD_NUMBER: _ClassVar[int]
    ERROR_CODE_FIELD_NUMBER: _ClassVar[int]
    directive_id: str
    outcome: ControlDirectiveOutcome
    completed_stages: _containers.RepeatedScalarFieldContainer[str]
    error_code: str
    def __init__(self, directive_id: _Optional[str] = ..., outcome: _Optional[_Union[ControlDirectiveOutcome, str]] = ..., completed_stages: _Optional[_Iterable[str]] = ..., error_code: _Optional[str] = ...) -> None: ...

class ConversationStatusUpdate(_message.Message):
    __slots__ = ("previous_status", "new_status", "reason", "effective_at")
    PREVIOUS_STATUS_FIELD_NUMBER: _ClassVar[int]
    NEW_STATUS_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    EFFECTIVE_AT_FIELD_NUMBER: _ClassVar[int]
    previous_status: ServiceStatus
    new_status: ServiceStatus
    reason: str
    effective_at: _timestamp_pb2.Timestamp
    def __init__(self, previous_status: _Optional[_Union[ServiceStatus, str]] = ..., new_status: _Optional[_Union[ServiceStatus, str]] = ..., reason: _Optional[str] = ..., effective_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class AssistantMessage(_message.Message):
    __slots__ = ("text", "reply_language", "response_kind", "in_reply_to_event_ids")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    REPLY_LANGUAGE_FIELD_NUMBER: _ClassVar[int]
    RESPONSE_KIND_FIELD_NUMBER: _ClassVar[int]
    IN_REPLY_TO_EVENT_IDS_FIELD_NUMBER: _ClassVar[int]
    text: str
    reply_language: str
    response_kind: str
    in_reply_to_event_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, text: _Optional[str] = ..., reply_language: _Optional[str] = ..., response_kind: _Optional[str] = ..., in_reply_to_event_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class ControlDirective(_message.Message):
    __slots__ = ("directive_id", "directive_type", "customer_notice")
    DIRECTIVE_ID_FIELD_NUMBER: _ClassVar[int]
    DIRECTIVE_TYPE_FIELD_NUMBER: _ClassVar[int]
    CUSTOMER_NOTICE_FIELD_NUMBER: _ClassVar[int]
    directive_id: str
    directive_type: ControlDirectiveType
    customer_notice: str
    def __init__(self, directive_id: _Optional[str] = ..., directive_type: _Optional[_Union[ControlDirectiveType, str]] = ..., customer_notice: _Optional[str] = ...) -> None: ...

class ServiceError(_message.Message):
    __slots__ = ("error_code", "retryable", "related_event_id", "safe_message")
    ERROR_CODE_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    RELATED_EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    SAFE_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    error_code: str
    retryable: bool
    related_event_id: str
    safe_message: str
    def __init__(self, error_code: _Optional[str] = ..., retryable: _Optional[bool] = ..., related_event_id: _Optional[str] = ..., safe_message: _Optional[str] = ...) -> None: ...
