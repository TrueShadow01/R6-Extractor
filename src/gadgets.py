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
    weapon_uid: int | None = None
    equipment_slot: int = 5
    item_type: int = 0x963A496D

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
    Gadget(0x000000009B2CAF2E, "Ash - Breaching Round Launcher", 0x00000037802D16A1, 0x000000483B8E5C27, 0x000000483B8E5C15, weapon_uid=0x00000037802CC878),
    Gadget(0x00000064935FA57D, "Thatcher - E.G.S. Disruptor", 0x00000037802D16D3, 0x000000651F0E871D, 0x0000006328827031, weapon_uid=0x00000064935FA584),
    Gadget(0x0000000106FC47E2, "Castle - Armor Panel (intact)", 0x00000037802D16A7, 0x000000548AE78929, 0x00000054206C58AC),
    Gadget(0x000000009B2CAEFC, "Thermite - Exothermic Charge", 0x00000037802D16D4, 0x00000037802D20B9, 0x00000038FA6B1AFA),
    Gadget(0x0000000AA6A04860, "Lesion - Gu Mine", 0x00000037802D16BA, 0x000000512B6EFE33, 0x000000512B6EFDE6),
    Gadget(0x0000000ADC64DD6D, "Ying - Candela (closed)", 0x00000037802D16D9, 0x000000529BBF458E, 0x000000529BBF61C4),
    Gadget(0x000000088B89E1C3, "Ela - Grzmot Mine", 0x00000037802D16AD, 0x00000037802D1A46, 0x000000395EBC18B0),
    Gadget(0x00000009615E0527, "Echo - Yokai Drone", 0x00000037802D16AC, 0x00000037802D1A12, 0x0000005921DEB03A),
    Gadget(0x000000096ED81F00, "Hibana - X-KAIROS Launcher",  0x00000037802D16B4, 0x000000483B8EB6C5, 0x000000483B8EB6B3),
    Gadget(0x3B6B1B8499, "Ace - S.E.L.M.A.", 0x3D42207D45, 0x3D4220A13A, 0x3B6B1B84AC),
    Gadget(0x108F8574A5, "Alibi - Prisma projector", 0x37802D169F, 0x512B6F1306, 0x512B6F0844),
    Gadget(0x30F8369D2C, "Amaru - Garra launcher", 0x37802D16A0, 0x55192E0104, 0x4995CFC2A6),
    Gadget(0x41BDCE5F31, "Aruni - Surya Gate", 0x41BDCE4953, 0x41BDCE5F3C, 0x41BDCE5E99),
    Gadget(0x56EC57FC2B, "Azami - Kiba Barrier kunai", 0x56EC579678, 0x56EC57FCA8, 0x56EC57FB4B),
    Gadget(0x5DB5BC2B2A, "Blackbeard - H.U.L.L. shield", 0x37802D16A3, 0x5FF059B412, 0x5DB5BC2B56, equipment_slot=7),
    Gadget(0x46B1161F22, "Blitz - Flash shield", 0x37802D16A4, 0x46B1161EFD, 0x46B1161EA3),
    Gadget(0x5B52EFC9CE, "Brava - Kludge Drone", 0x5B52EFAECC, 0x5B52EFCB10, 0x5B52EFCA5A),
    Gadget(0x2A4CEB80CE, "Buck - Skeleton Key", 0x37802D16A5, 0x39E978FB6C, 0x2A4CEB8194),
    Gadget(0x2FF67F5827, "Capitao - Tactical Crossbow", 0x37802D16A6, 0x483B8E7F2B, 0x483B8E7F18),
    Gadget(0x57CA67A099, "Caveira - Luison suppressor (attachment)", 0x37802D16A8, 0x37802D1938, 0x2662CC4A91),
    Gadget(0x60C4EE7E3B, "Clash - CCE shield", 0x37802D16A9, 0x60C4EE826E, 0x60C4EE7E40),
    Gadget(0x5E8C093B6C, "Deimos - DeathMARK drone", 0x5DCA74D562, 0x5DCA74D7D9, 0x5DCA74D77A),
    Gadget(0x62BFAA96B1, "Denari - T.R.I.P. device", 0x62BFAA45D7, 0x62BFAA483F, 0x62BFAA484C),
    Gadget(0x13D0BCD6A8, "Dokkaebi - Logic Bomb tablet", 0x37802D16AB, 0x4A684BE3F9, 0x4A684BE3C0),
    Gadget(0x5BA42DDF48, "Fenrir - F-NATT mine", 0x5BA42DBF7B, 0x5BA42DDF84, 0x5BA42DDF59),
    Gadget(0x4A684B14C9, "Flores - RCE-Ratero", 0x4A684AE4ED, 0x4A684B14E2, 0x4A684B1398),
    Gadget(0x30E706D5DD, "Glaz - Flip Sight (optic)", 0x37802D16B1, 0x37802D1B2E, 0x30E706D6AF, equipment_slot=2, item_type=0x1C5C0E2D),
    Gadget(0x4B0AF2952A, "Goyo - Volcan canister", 0x37802D16B2, 0x5774FC72B3, 0x4B0AF29522),
    Gadget(0x27EFAE16B8, "Gridlock - Trax Stingers", 0x37802D16B3, 0x37802D1B7C, 0x395EBC189C),
    Gadget(0x59A6BD7EA9, "Grim - Kawan launcher (body)", 0x59A6BD6E6E, 0x59A6BD7FD3, 0x59A6BD7E2A, weapon_uid=0x59A6BD7EC6),
    Gadget(0x3ACCF2782B, "Iana - Gemini controller (component)", 0x3B075FB191, 0x3CFD8C2FD8, 0x38B9A2D860),
    Gadget(0x2418EC2F9D, "Kaid - Electroclaw", 0x37802D16B8, 0x37802D1C97, 0x395EBC1883),
    Gadget(0x33E28D72E6, "Kali - LV Explosive Lance", 0x37802D169C, 0x37802D170A, 0x33E28D72FF),
    Gadget(0x54C4E8C88B, "Lion - EE-ONE-D controller", 0x37802D16BB, 0x4A684BE48B, 0x4A684BE453),
    Gadget(0xA92106D9E, "Maestro - Evil Eye", 0x37802D16BC, 0x37802D1D5C, 0x3989288025),
    Gadget(0x1F7D4C7B04, "Maverick - Breaching Torch", 0x37802D16BD, 0x483B8EC03C, 0x483B8EBA4E),
    Gadget(0x529BD57E3C, "Melusi - Banshee", 0x3D42207D30, 0x533C4F01B6, 0x518A2BF323),
    Gadget(0x9C145A5DA, "Mira - Black Mirror", 0x37802D16BE, 0x37802D1DAE, 0x3975E3B054),
    Gadget(0x3F3936A334, "Montagne - Le Roc (extended)", 0x37802D16BF, 0x37802D1DE5, 0x2D2B424598),
    Gadget(0x24F948E738, "Mozzie - Pest launcher", 0x37802D16C0, 0x483B8EC0A8, 0x483B8EC099),
    Gadget(0x24F948E73E, "Mozzie - Pest", 0x37802D16C0, 0x483B8EC0A8, 0x483B8EC099),
    Gadget(0x2D2B414127, "Nokk - HEL controller", 0x37802D16C3, 0x37802D1E98, 0x2C06F6BB9B),
    Gadget(0x2E78D2C22E, "Nomad - Airjab launcher", 0x37802D16C2, 0x39E97A116D, 0x2E78D2C3C0),
    Gadget(0x67B7BE27EC, "Noor - Launcher (loaded)", 0x679C49B955, 0x67B7BE9A52, 0x67B7BE2850),
    Gadget(0x55DC2F2157, "Osa - Talon-8 shield (deployed)", 0x55192D7613, 0x55192D8838, 0x55192D8785),
    Gadget(0x547BC75B93, "Pulse - Heartbeat Sensor", 0x37802D16C4, 0x547BC67543, 0x547BC674D9),
    Gadget(0x5BC8F5214C, "Ram - BU-GI", 0x5C6FE20207, 0x5C9F2F26F0, 0x5C9F2F26C2),
    Gadget(0x6047CFE767, "Rauora - D.O.M. launcher", 0x6047CFC135, 0x6047CFE7DA, 0x6047CFE72F),
    Gadget(0x57A0ADFEC2, "Sens - R.O.U. projector", 0x57A0ADDBC9, 0x57A0ADFF93, 0x57A0ADFF48),
    Gadget(0x6047CFF9C4, "Skopos - Inactive-shell shield (component)", 0x5F2472B3A5, 0x5F2472DA70, 0x5F2472DA36),
    Gadget(0x64D62E744E, "Solid Snake - Radar", 0x64D62DB918, 0x650A6107C2, 0x64D62E7354),
    Gadget(0x5A363A3B24, "Solis - SPEC-IO wrist controller", 0x5A3639E8EE, 0x5A363A2832, 0x5A363A27C6),
    Gadget(0x38A4AB07DC, "Tachanka - Shumikha launcher", 0x37802D16D2, 0x483B8ECA4C, 0x483B8ECA3B),
    Gadget(0x573DBEB520, "Thorn - Razorbloom Shell", 0x53D5D2428C, 0x55192E34C1, 0x55192E3494),
    Gadget(0x532A0CC9AF, "Thunderbird - Kona Station", 0x532A0CB853, 0x532A0CC9B9, 0x532A0CC984),
    Gadget(0x5D048D31A9, "Tubarao - Zoto canister", 0x5D048CF355, 0x5D048D31C6, 0x5D048D317C),
    Gadget(0x548AE6FACB, "Vigil - ERC-7 device", 0x37802D16D7, 0x433FBE5216, 0x433FBE50E6),
    Gadget(0x33E28D6937, "Wamai - Mag-NET", 0x37802D169D, 0x37802D1719, 0x33E28D693D),
    Gadget(0x2D2B42ECE6, "Warden - Glance watch (component)", 0x37802D16D8, 0x37802D21B4, 0x2C06F6BB9D),
    Gadget(0x40705E87F6, "Zero - Argus launcher", 0x40705E6CA7, 0x483B8EC43B, 0x483B8EC42C),
    Gadget(0xE7E584CB5, "Zofia - KS79 Lifeline", 0x37802D16DA, 0x483B8ECA96, 0x483B8ECA84),
)

def get_gadet(uid):
    for gadget in GADGETS:
        if gadget.uid == uid:
            return gadget
    raise ValueError(f"Unsupported gadget model {uid:016X}")

def validate_gadget(game, gadget):
    """Reject mappings that no longer match the installed equipment records."""
    if get_gadet(gadget.uid) != gadget:
        raise ValueError("Gadget selection does not match the supported catalogue")

    records = registry._read_records(Path(game) / "datapc64.forge", extra_types=(0x963A496D, 0x31330C03, 0x7FF0E4DF, 0x1C5C0E2D),)
    operator = registry._record(records, gadget.operator_uid, registry.OPERATOR_TYPE)
    dependency_count = registry._u32(operator.data, 4)
    if dependency_count > 4096:
        raise ValueError("Unsupported operator dependency count")

    offset = 8 + dependency_count * 8 + 32 + gadget.equipment_slot * 37
    if operator.data[offset:offset + 4] != registry.SLOT_TAG or registry._unpack("<B", operator.data, offset + 12)[0] != gadget.equipment_slot or registry._u64(operator.data, offset + 4) != gadget.item_uid:
        raise ValueError("Gadget equipment changed. This mapping needs rechecking")

    item = registry._record(records, gadget.item_uid, gadget.item_type)
    count = registry._u32(item.data, 4)
    if gadget.item_type == 0x1C5C0E2D and count != 1:
        raise ValueError("Primary weapon appearance relationship changed")

    if not 1 <= count <= 4096 or registry._u64(item.data, 8) != gadget.appearance_uid:
        raise ValueError("Gadget appearance changed. This mapping needs rechecking")

    appearance = registry._record(records, gadget.appearance_uid, registry.APPEARANCE_TYPE)

    if gadget.weapon_uid is not None:
        # These equipment appearances select gameplay entites not meshes.
        if appearance.data[29:34] != bytes.fromhex("74251f0500") or any(registry._u64(appearance.data, n) for n in (34, 42, 50, 58)):
            raise ValueError("Weapon gadget equipment appearance changed")

        skin_offset = 8 + count * 8 + 32 + 37
        if item.data[skin_offset:skin_offset + 4] != registry.SLOT_TAG or registry._unpack("<B", item.data, skin_offset + 12)[0] != 1:
            raise ValueError("Weapon gadget skin slot changed")

        skin = registry._record(records, registry._u64(item.data, skin_offset + 4), 0x31330C03)
        if len(skin.data) != 130 or registry._u32(skin.data, 4) != 0:
            raise ValueError("Weapon gadget skin relationship changed")

        finish = registry._record(records, registry._u64(skin.data, 113), 0x7FF0E4DF)
        finish_count = registry._u32(finish.data, 4)
        if finish_count not in (0, 1) or len(finish.data) != 114 + finish_count * 8 or registry._u64(finish.data, 97 + finish_count * 8) != gadget.weapon_uid:
            raise ValueError("Weapon gadget finish relationship changed")

        weapon = registry._record(records, gadget.weapon_uid, 0x1C5C0E2D)
        if registry._u32(weapon.data, 4) != 1:
            raise ValueError("Weapon gadget appearance layout changed")

        appearance = registry._record(records, registry._u64(weapon.data, 8), registry.APPEARANCE_TYPE)
        valid_layouts = (bytes.fromhex("74251f0500"), bytes.fromhex("74251f0501"))
    elif (gadget.operator_uid, gadget.item_uid, gadget.appearance_uid) in {
        (0x00000037802D16A7, 0x000000548AE78929, 0x00000054206C58AC),
        (0x00000037802D16D9, 0x000000529BBF458E, 0x000000529BBF61C4),
        (0x00000037802D16AC, 0x00000037802D1A12, 0x0000005921DEB03A),
        (0x57A0ADDBC9, 0x57A0ADFF93, 0x57A0ADFF48),
    }:
        valid_layouts = (bytes.fromhex("74251f0501"),)
    else:
        valid_layouts = (bytes.fromhex("74251f0500"),)

    # Reference 66 is an entity reference not anotehr selectable mesh state
    if appearance.data[29:34] not in valid_layouts or gadget.uid not in tuple(registry._u64(appearance.data, n) for n in (34, 42, 50, 58)):
        raise ValueError("Gadget model changed. This mapping needs rechecking.")