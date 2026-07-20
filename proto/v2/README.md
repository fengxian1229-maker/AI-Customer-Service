# V2 protobuf generation

`customer_service.proto` is the V2 backend/AI wire contract. The AI service owns the
gRPC server and a backend connects to the bidirectional `CustomerService.Stream` RPC.

`StreamFrame` keeps handshake and ACK control frames outside `ClientEvent` and
`ServerEvent`. Only backend-to-AI and AI-to-backend business events are declared in
those event payload oneofs; AI-internal mailbox events are deliberately absent.
The V2 package and service path are the major-version boundary. Handshakes and
events do not carry a separate `protocol_version`; an incompatible future protocol
uses a new package/service path.

The generated Python modules will live under:

```text
src/app_v2/transport/grpc/generated/
```

Use a virtual proto path so generated imports retain the full Python package path:

```bash
uv run python -m grpc_tools.protoc \
  --proto_path=app_v2/transport/grpc/generated=proto/v2 \
  --python_out=src \
  --grpc_python_out=src \
  --pyi_out=src \
  app_v2/transport/grpc/generated/customer_service.proto
```

Generated `*_pb2.py`, `*_pb2_grpc.py`, and `*_pb2.pyi` files are committed.
`tests/app_v2/contract/test_proto_contract.py` regenerates them in a temporary
directory and compares every byte with the committed files. Runtime startup never
generates code.

Wire representation choices fixed by this schema:

- date-times use `google.protobuf.Timestamp`;
- `ContentPart` is one object with exactly `content_type` and a non-empty string
  `content`; the wire contract does not assign URL, base64, or storage-key semantics
  to that string;
- enums reserve zero as `*_UNSPECIFIED`; the transport mapping layer must reject it
  and unknown numeric values instead of treating them as business values;
- no bytes content, `Any`, `Struct`, arbitrary JSON payload, attachment reference, runtime version,
  correlation ID, or causation ID is present.

For manual testing, Postman or Apifox may import `proto/v2/customer_service.proto` directly and connect to the AI gRPC Server once stage 2 and the transport implementation are complete.
