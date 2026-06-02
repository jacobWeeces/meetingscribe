"""Secure storage for the Anthropic API key in the macOS Keychain.

Uses generic-password Keychain items with device-only access so the key never
syncs to iCloud Keychain and is unreadable while the device is locked.
"""
import logging

import Security

log = logging.getLogger("meetingscribe")

DEFAULT_SERVICE = "MeetingScribe"
DEFAULT_ACCOUNT = "anthropic_api_key"
_OK = 0  # errSecSuccess


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
    return bytes(data).decode("utf-8")


def keychain_set(value, account=DEFAULT_ACCOUNT, service=DEFAULT_SERVICE):
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
