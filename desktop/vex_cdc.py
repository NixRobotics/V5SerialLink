"""Minimal VEX V5 CDC2 support for reading user serial through a V5 Controller.

A controller exposes only one serial port, which carries framed system packets.
User program output is fetched by polling the USER_READ command, as VEXcode,
PROS (`user_fifo_read`), and vexide (`UserDataPacket`) do.
"""

from dataclasses import dataclass

VEX_VENDOR_ID = 0x2888
V5_BRAIN_PRODUCT_ID = 0x0501
V5_CONTROLLER_PRODUCT_ID = 0x0503

COMMAND_HEADER = b"\xc9\x36\xb8\x47"
REPLY_HEADER = b"\xaa\x55"
USER_CDC = 0x56
USER_READ = 0x27
ACK = 0x76
STDIO_CHANNEL = 1
READ_CHUNK = 0x40
MAX_BUFFER_BYTES = 65536

NACK_MESSAGES = {
    0xFF: "general NACK",
    0xCE: "packet CRC error",
    0xD0: "payload too small",
    0xD1: "request too large",
}


def crc16_xmodem(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def _var_u16(value: int) -> bytes:
    if value > 0x7FFF:
        raise ValueError("CDC2 length exceeds 15 bits")
    return bytes([value]) if value <= 0x7F else bytes([(value >> 8) | 0x80, value & 0xFF])


def build_cdc2_command(ecmd: int, payload: bytes) -> bytes:
    packet = COMMAND_HEADER + bytes([USER_CDC, ecmd]) + _var_u16(len(payload)) + payload
    return packet + crc16_xmodem(packet).to_bytes(2, "big")


def build_user_read_request() -> bytes:
    return build_cdc2_command(USER_READ, bytes([STDIO_CHANNEL, READ_CHUNK]))


@dataclass(frozen=True)
class Cdc2Reply:
    ecmd: int
    ack: int
    payload: bytes

    @property
    def user_data(self) -> bytes:
        """stdout bytes from a USER_READ reply; the FIFO pads with NULs."""
        if self.ecmd != USER_READ or self.ack != ACK or not self.payload:
            return b""
        return self.payload[1:].split(b"\x00", 1)[0]

    @property
    def nack_message(self) -> str:
        return NACK_MESSAGES.get(self.ack, f"NACK 0x{self.ack:02X}")


class Cdc2ReplyParser:
    """Extract CRC-validated CDC2 replies from a byte stream."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[Cdc2Reply]:
        self.buffer += data
        if len(self.buffer) > MAX_BUFFER_BYTES:
            del self.buffer[:-MAX_BUFFER_BYTES]
        replies: list[Cdc2Reply] = []
        while True:
            start = self.buffer.find(REPLY_HEADER)
            if start < 0:
                # Keep a trailing 0xAA in case the header is split across reads.
                keep = 1 if self.buffer.endswith(REPLY_HEADER[:1]) else 0
                del self.buffer[: len(self.buffer) - keep]
                return replies
            del self.buffer[:start]
            if len(self.buffer) < 4:
                return replies
            if self.buffer[2] != USER_CDC:
                del self.buffer[:1]
                continue
            first = self.buffer[3]
            if first & 0x80:
                if len(self.buffer) < 5:
                    return replies
                length, size_bytes = ((first & 0x7F) << 8) | self.buffer[4], 2
            else:
                length, size_bytes = first, 1
            if length < 4:
                del self.buffer[:1]
                continue
            total = 3 + size_bytes + length
            if len(self.buffer) < total:
                return replies
            packet = bytes(self.buffer[:total])
            if crc16_xmodem(packet) != 0:
                del self.buffer[:1]
                continue
            del self.buffer[:total]
            body = packet[3 + size_bytes : -2]
            replies.append(Cdc2Reply(body[0], body[1], body[2:]))
