from __future__ import annotations

from app.agent_review.redaction import REDACTED, redact_content


def test_redaction_removes_authorization_header() -> None:
    redacted, report = redact_content("Authorization: Bearer abcdefghijklmnop")

    assert redacted == f"Authorization: Bearer {REDACTED}"
    assert report.replacements_by_type["authorization_bearer"] == 1


def test_redaction_removes_bearer_token() -> None:
    redacted, report = redact_content("call with Bearer abcdefghijklmnop")

    assert redacted == f"call with Bearer {REDACTED}"
    assert report.replacements_by_type["bearer_token"] == 1


def test_redaction_removes_secret_assignments() -> None:
    redacted, report = redact_content("password=secret-value client_secret=another-secret")

    assert redacted == f"password={REDACTED} client_secret={REDACTED}"
    assert report.secret_like_values_found == 2


def test_redaction_removes_database_url_credentials() -> None:
    redacted, report = redact_content("DATABASE_URL=postgres://user:pass@db.local/app")

    assert redacted == f"DATABASE_URL=postgres://{REDACTED}:{REDACTED}@db.local/app"
    assert report.replacements_by_type["database_url_credentials"] == 1


def test_redaction_recurses_nested_json() -> None:
    redacted, report = redact_content(
        {
            "outer": {
                "authorization": "Bearer abcdefghijklmnop",
                "items": [{"api_key": "sk-abcdefghijklmnop123456"}],
            }
        }
    )

    assert redacted["outer"]["authorization"] == REDACTED
    assert redacted["outer"]["items"][0]["api_key"] == REDACTED
    assert report.secret_like_values_found == 2


def test_redaction_preserves_safe_json_keys() -> None:
    redacted, report = redact_content({"token_count": 3, "safe": "example", "placeholder": "fake-token"})

    assert redacted == {"token_count": 3, "safe": "example", "placeholder": "fake-token"}
    assert report.secret_like_values_found == 0

