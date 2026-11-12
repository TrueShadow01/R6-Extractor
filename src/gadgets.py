"""Source checked pilot gadget mappings"""

from dataclasses import dataclass
from pathlib import Path

from src import operator_registry as registry

@dataclass(frozen=True)
class Gadget:
    uid: int
    name: str
    operator_uid: int
    item_uid: int
    appearance_uid: int

    @property
    def model_groups(self):
        return((self.uid,),)

GADGETS = (
    Gadget(0x000000009B2CAF32, "Rook - Armor pack (closed)", 0x00000037802D16CF, 0x00000037802D1F7A, 0x0000003C8F418B8C),
    Gadget(0x00000000CE8EA8B5, "Rook - Armor pack (open)", 0x00000037802D16CF, 0x00000037802D1F7A, 0x0000003C8F418B8C),
    Gadget(0x000000009B2CAF02, "Sledge - Hammer", 0x00000037802D16D0, 0x00000037802D1FB7, 0x00000036232BA6A5),
    Gadget(0x000000009B2CAF20, "Bandit - Battery", 0x00000037802D16A2, 0x0000005917D2F8F3, 0x0000004ACA9EA1E3),
    Gadget(0x00000001FC75DB96, "Frost - Welcome Mat (folded)", 0x00000037802D16AF, 0x00000037802D1AAB, 0x0000003C8F418BF7),
    Gadget(0x0000003C8F4197B2, "Frost - Welcome Mat (open)", 0x00000037802D16AF, 0x00000037802D1AAB, 0x0000003C8F418BF7),
    Gadget(0x000000009B2CAF2A, "Jager - ADS", 0x00000037802D16B7, 0x00000037802D1C4F, 0x0000003C8F418C13),
    Gadget(0x0000003016776CF8, "Mute - Signal Jammer", 0x00000037802D16C1, 0x00000037802D1E3A, 0x0000003016776D08),
    Gadget(0x000000226A3FDC9B, "Smoke - Gas canister", 0x00000037802D16D1, 0x0000004F0FF31179, 0x000000226A3FDC94),
    Gadget(0x000000067C9C5173, "Valkyrie - Black Eye", 0x00000037802D16D6, 0x00000037802D213D, 0x000000395EBC18E0),
    Gadget(0x000000009B2CAF26, "Fuze - Cluster Charge", 0x00000037802D16B0, 0x00000037802D1AE8, 0x00000038FA6AE807),
    Gadget(0x000000009B2CAF2C, "Kapkan - Entry Denial Device", 0x00000037802D16B9, 0x0000005580BC9622, 0x0000005580BC9D9D),
    Gadget(0x000000009B2CAEF8, "Twitch - Shock Drone", 0x00000037802D16D5, 0x00000037802D20F9, 0x00000041EBBDCA29),
    Gadget(0x0000000106FC86BF, "Doc - Stim Pistol", 0x00000037802D16AA, 0x000000483B8E9E92, 0x000000483B8E9E88),
    Gadget(0x0000002A4CEB7295, "IQ - Scanner", 0x00000037802D16B5, 0x00000037802D1BDB, 0x0000002A4CEB72A0),
)

def get_gadet(uid):
    for gadget in GADGETS:
        if gadget.uid == uid:
            return gadget
    raise ValueError(f"Unsupported gadget model {uid:016X}")

def validate_gadget(game, gadget):
    """Reject pilot mapping if the installed game no longer selects it"""
    if get_gadet(gadget.uid) != gadget:
        raise ValueError("Gadget selection does not match the supported catalogue")

    records = registry._read_records(Path(game) / "datapc64.forge", extra_types=(0x963A496D,))
    operator = registry._record(records, gadget.operator_uid, registry.OPERATOR_TYPE)
    dependency_count = registry._u32(operator.data, 4)
    if dependency_count > 4096:
        raise ValueError("Unsupported operator dependency count")

    offset = 8 + dependency_count * 8 + 32 + 5 * 37
    if operator.data[offset:offset + 4] != registry.SLOT_TAG or registry._unpack("<B", operator.data, offset + 12)[0] != 5 or registry._u64(operator.data, offset + 4) != gadget.item_uid:
        raise ValueError("Gadget equipment changed. This mapping needs rechecking")

    item = registry._record(records, gadget.item_uid, 0x963A496D)
    count = registry._u32(item.data, 4)

    if not 1 <= count <= 4096 or registry._u64(item.data, 8) != gadget.appearance_uid:
        raise ValueError("Gadget appearance changed. This mapping needs rechecking")

    appearance = registry._record(records, gadget.appearance_uid, registry.APPEARANCE_TYPE)

    # Inspected visual model block. Reference at 66 is not a selectable state
    if appearance.data[29:34] != bytes.fromhex("74251f0500") or gadget.uid not in tuple(registry._u64(appearance.data, offset) for offset in (34, 42, 50, 58)):
        raise ValueError("Gadget model changed. This mapping needs rechecking")