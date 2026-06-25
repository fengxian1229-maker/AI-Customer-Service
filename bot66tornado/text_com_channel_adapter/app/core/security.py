import hmac
from fastapi import HTTPException, status


def verify_text_com_payload_secret(received_secret: str | None, expected_secret: str | None) -> None:
    """Verify Text.com webhook payload secret_key.

    Text.com Chat Webhooks include the configured secret_key in the JSON payload.
    This is not an HMAC signature; keep this endpoint HTTPS-only in production.
    """
    if not expected_secret:
        # Local development can run without a secret, but production should set it.
        return

    if not received_secret or not hmac.compare_digest(received_secret, expected_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Text.com webhook secret_key",
        )
