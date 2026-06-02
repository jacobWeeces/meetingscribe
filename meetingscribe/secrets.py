"""Secure storage for the Anthropic API key in the macOS Keychain.

Uses generic-password Keychain items with device-only access so the key never
syncs to iCloud Keychain and is unreadable while the device is locked.
"""
import logging

import Security

log = logging.getLogger("meetingscribe")

DEFAULT_SERVICE = "MeetingScribe"
DEFAULT_ACCOUNT = "anthropic_api_key"
_OK = Security.errSecSuccess


def keychain_get(account=DEFAULT_ACCOUNT, service=DEFAULT_SERVICE):
    query = {
        Security.kSecClass: Security.kSecClassGenericPassword,
        Security.kSecAttrService: service,
        Security.kSecAttrAccount: account,
        Security.kSecReturnData: True,
        Security.kSecMatchLimit: Security.kSecMatchLimitOne,
    }
    status, data = Security.SecItemCopyMatching(query, None)
    if status != _OK or not data:
        return ""
    try:
        return bytes(data).decode("utf-8")
    except UnicodeDecodeError:
        log.warning(
            "Keychain value for %s/%s is not valid UTF-8; treating as absent",
            service,
            account,
        )
        return ""


def keychain_set(value, account=DEFAULT_ACCOUNT, service=DEFAULT_SERVICE):
    if not value:
        log.warning("keychain_set called with empty value; use keychain_delete to remove")
        return False
    keychain_delete(account, service)  # overwrite cleanly
    attrs = {
        Security.kSecClass: Security.kSecClassGenericPassword,
        Security.kSecAttrService: service,
        Security.kSecAttrAccount: account,
        Security.kSecValueData: value.encode("utf-8"),
        Security.kSecAttrAccessible: Security.kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
    }
    status, _ = Security.SecItemAdd(attrs, None)
    if status != _OK:
        log.error("Keychain write failed (OSStatus %d)", status)
    return status == _OK


def keychain_delete(account=DEFAULT_ACCOUNT, service=DEFAULT_SERVICE):
    query = {
        Security.kSecClass: Security.kSecClassGenericPassword,
        Security.kSecAttrService: service,
        Security.kSecAttrAccount: account,
    }
    Security.SecItemDelete(query)
