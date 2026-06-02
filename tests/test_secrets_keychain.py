from meetingscribe import secrets


def test_set_get_delete_roundtrip(kc_namespace):
    acct = "anthropic_api_key"
    try:
        assert secrets.keychain_get(acct, kc_namespace) == ""        # absent
        assert secrets.keychain_set("sk-test-123", acct, kc_namespace) is True
        assert secrets.keychain_get(acct, kc_namespace) == "sk-test-123"
        # overwrite is idempotent
        assert secrets.keychain_set("sk-test-456", acct, kc_namespace) is True
        assert secrets.keychain_get(acct, kc_namespace) == "sk-test-456"
        secrets.keychain_delete(acct, kc_namespace)
        assert secrets.keychain_get(acct, kc_namespace) == ""        # gone
    finally:
        secrets.keychain_delete(acct, kc_namespace)


def test_unicode_value(kc_namespace):
    try:
        secrets.keychain_set("sk-✓-key", "acct", kc_namespace)
        assert secrets.keychain_get("acct", kc_namespace) == "sk-✓-key"
    finally:
        secrets.keychain_delete("acct", kc_namespace)


def test_set_empty_returns_false_and_stores_nothing(kc_namespace):
    assert secrets.keychain_set("", "acct", kc_namespace) is False
    assert secrets.keychain_get("acct", kc_namespace) == ""
