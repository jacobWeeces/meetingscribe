from meetingscribe import secrets


def test_set_get_delete_roundtrip(kc_namespace):
    acct = "anthropic_api_key"
    assert secrets.keychain_get(acct, kc_namespace) == ""        # absent
    assert secrets.keychain_set("sk-test-123", acct, kc_namespace) is True
    assert secrets.keychain_get(acct, kc_namespace) == "sk-test-123"
    # overwrite is idempotent
    assert secrets.keychain_set("sk-test-456", acct, kc_namespace) is True
    assert secrets.keychain_get(acct, kc_namespace) == "sk-test-456"
    secrets.keychain_delete(acct, kc_namespace)
    assert secrets.keychain_get(acct, kc_namespace) == ""        # gone


def test_unicode_value(kc_namespace):
    secrets.keychain_set("sk-✓-key", "acct", kc_namespace)
    assert secrets.keychain_get("acct", kc_namespace) == "sk-✓-key"
    secrets.keychain_delete("acct", kc_namespace)
