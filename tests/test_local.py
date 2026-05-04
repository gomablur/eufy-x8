"""Tests for the Tuya v3.3 local protocol implementation."""
import json
import struct
from unittest.mock import patch

import pytest

from custom_components.eufy_x8.api.local import (
    InvalidKey,
    Message,
    TuyaCipher,
    TuyaDevice,
    crc,
    MAGIC_PREFIX,
    MAGIC_SUFFIX,
)


# ---------------------------------------------------------------------------
# TuyaCipher
# ---------------------------------------------------------------------------

VALID_KEY = "abcdef1234567890"  # exactly 16 chars


def test_tuya_cipher_encrypt_decrypt_roundtrip():
    cipher = TuyaCipher(VALID_KEY, (3, 3))
    plaintext = b'{"dps": {"15": "Charging"}}'
    encrypted = cipher.encrypt(Message.SET_COMMAND, plaintext)
    decrypted = cipher.decrypt(Message.SET_COMMAND, encrypted)
    assert decrypted == plaintext


def test_tuya_cipher_version_prefix():
    """v3.3 encrypt should produce a '3.3' prefix for SET_COMMAND."""
    cipher = TuyaCipher(VALID_KEY, (3, 3))
    encrypted = cipher.encrypt(Message.SET_COMMAND, b"test")
    assert encrypted[:3] == b"3.3"


def test_tuya_cipher_different_keys_produce_different_output():
    key1 = TuyaCipher("abcdef1234567890", (3, 3))
    key2 = TuyaCipher("1234567890abcdef", (3, 3))
    plaintext = b"hello world"
    assert key1.encrypt(Message.GET_COMMAND, plaintext) != key2.encrypt(Message.GET_COMMAND, plaintext)


# ---------------------------------------------------------------------------
# CRC
# ---------------------------------------------------------------------------

def test_crc_empty():
    assert isinstance(crc(b""), int)


def test_crc_known_value():
    # CRC of b"123456789" is a well-known CRC32 test vector: 0xCBF43926
    assert crc(b"123456789") == 0xCBF43926


def test_crc_different_inputs_differ():
    assert crc(b"abc") != crc(b"abd")


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

def test_message_has_magic_prefix_and_suffix():
    cipher = TuyaCipher(VALID_KEY, (3, 3))

    class _FakeDev:
        version = (3, 3)
        cipher = TuyaCipher(VALID_KEY, (3, 3))
        _listeners = {}

    msg = Message(Message.GET_COMMAND, payload=b"test", device=_FakeDev(),
                  encrypt=False, expect_response=False)
    raw = msg.bytes()
    prefix = struct.unpack(">I", raw[:4])[0]
    suffix = struct.unpack(">I", raw[-4:])[0]
    assert prefix == MAGIC_PREFIX
    assert suffix == MAGIC_SUFFIX


def test_message_dict_payload_serialised():
    class _FakeDev:
        version = (3, 3)
        cipher = TuyaCipher(VALID_KEY, (3, 3))
        _listeners = {}

    payload = {"dps": {"15": "Running"}}
    msg = Message(Message.GET_COMMAND, payload=payload, device=_FakeDev(),
                  encrypt=False, expect_response=False)
    raw = msg.bytes()
    # The raw payload area should contain the JSON string
    assert b"Running" in raw


# ---------------------------------------------------------------------------
# TuyaDevice key validation
# ---------------------------------------------------------------------------

def test_tuya_device_rejects_short_key():
    with pytest.raises(InvalidKey):
        TuyaDevice(
            device_id="abc",
            host="192.168.1.1",
            timeout=5,
            ping_interval=10,
            update_entity_state=lambda: None,
            local_key="tooshort",
        )


def test_tuya_device_rejects_long_key():
    with pytest.raises(InvalidKey):
        TuyaDevice(
            device_id="abc",
            host="192.168.1.1",
            timeout=5,
            ping_interval=10,
            update_entity_state=lambda: None,
            local_key="this_key_is_way_too_long_to_be_valid",
        )


def test_tuya_device_update_local_key_rejects_bad_key():
    # Patch asyncio.create_task to prevent process_queue from spawning a
    # persistent background task that outlives the (synchronous) test.
    with patch("custom_components.eufy_x8.api.local.asyncio.create_task"):
        dev = TuyaDevice(
            device_id="abc",
            host="192.168.1.1",
            timeout=5,
            ping_interval=10,
            update_entity_state=lambda: None,
            local_key=VALID_KEY,
        )
    with pytest.raises(InvalidKey):
        dev.update_local_key("bad")
