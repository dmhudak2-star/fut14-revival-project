"""The decoder, against the frame that took the connection down.

On 21 August 2026 a console was taken into Face-à-Face, told there was no
opponent, and offered "Créer un match". Pressing it sent `createGame` -- the
first one this project has ever seen -- and the server died decoding it:

    ValueError: Unsupported TDF type 175 for @JY[ at offset 0x1F9

`HNET`, the host's address list, is declared on the wire as a list of structs,
but its elements are `NetworkAddress` **unions**, and a union element begins
with one byte naming its active member. Read as a plain struct that byte is a
terminator, so the element decoded as empty, the union's own fields were read
as siblings of `HNET`, and the real terminator forty bytes later became a
nonsense tag.

The frame is kept here in full because it is the evidence. Nothing about this
is inferred: it decodes to exactly its own length and re-encodes to the same
611 bytes.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from blaze_tdf import (  # noqa: E402
    STRUCT,
    Decoder,
    decode_frame,
    encode_fields,
    encode_frame,
    encode_item,
)

# component 4, command 1 -- createGame, as sent by an Xbox 360 running FIFA 14.
CREATE_GAME = bytes.fromhex("".join([
    "026300040001000000000038874D320501010D1566696661437573746F6D436F6E74726F"
    "6C6C6572000230000E6669666147616D655370656564000231000F6669666148616C664C"
    "656E6774680002340010666966614D61746368757048617368000B313638343336363936"
    "34000E666966615465616D4C6576656C00023000164F53444B5F6172656E614368616C6C"
    "656E6765496400023000104F53444B5F63617465676F72794964000230000A4F53444B5F"
    "636F6F70000231000E4F53444B5F67616D654D6F6465000230000C4F53444B5F726F6F6D"
    "4964000230000F4F53444B5F726F7374657255524C000100134F53444B5F726F73746572"
    "56657273696F6E00023100164F53444B5F73706F6E736F7265644576656E744964000230"
    "008B4C2C090000008F2A74050101020C4F53444B5F6D6178444E46001173746174735F64"
    "6E66203C3D2031303000104F53444B5F736B696C6C4C6576656C003073746174735F736B"
    "696C6C4C6576656C203E3D20312026262073746174735F736B696C6C4C6576656C203C3D"
    "203939009E3D320101009E5BB400009EDCA700009EE86D0101009F3974000F9F4E70010A"
    "67616D655479706530009F5CAC010100A2E97404030100B618E900B3E7E3A006E2493202"
    "24C0A80119020B639A0C027CED8D19694F0ADF727600F4E8FD858C6104000000FA010000"
    "00E35A64009996B5FEDDFF800900A67BAF0001BB29730000BB4BF0008202C23870040004"
    "02000000C27A64010100C27CE30200C2D8780002C329730001C638700000CA7A640000CA"
    "E9AF0300CECBF40000D29933040001BEFF07D299380000DAFA700002DB3D32010E71612D"
    "6F6E6C792D646179343500"
]))


def decoded() -> dict:
    return decode_frame(CREATE_GAME)


def test_the_frame_that_killed_the_connection_now_decodes() -> None:
    frame = decoded()
    assert (frame["component"], frame["command"]) == (4, 1)
    assert [field.label for field in frame["fields"]] == [
        "ATTR", "BTPL", "CRIT", "GCTR", "GENT", "GMRG", "GNAM", "GSET", "GTYP",
        "GURL", "HNET", "IGNO", "NRES", "NTOP", "PCAP", "PGID", "PGSC", "PMAX",
        "PRES", "QCAP", "RGID", "RNFO", "SLOT", "TIDS", "TIDX", "VOIP", "VSTR",
    ]


def test_nothing_is_left_over() -> None:
    """The standard this repo already held the VARIABLE type to: the rule is
    right only if the whole frame decodes with not one byte spare."""
    reader = Decoder(CREATE_GAME[12:])
    reader.all()
    assert reader.position == len(CREATE_GAME) - 12


def test_it_re_encodes_to_the_same_bytes() -> None:
    frame = decoded()
    again = encode_frame(
        frame["component"], frame["command"], frame["error"],
        frame["message_type"], frame["message_number"],
        encode_fields(frame["fields"]),
    )
    assert again == CREATE_GAME


def field(label: str):
    return next(f for f in decoded()["fields"] if f.label == label)


def test_the_host_address_list_holds_one_union_not_one_empty_struct() -> None:
    item_type, items = field("HNET").value
    assert item_type == STRUCT
    assert len(items) == 1
    active, members = items[0]
    # 0 is XboxClientAddress, the only variant FIFA 14 has ever sent.
    assert active == 0
    assert [m.label for m in members] == ["MACI", "XDDR", "XUID"]


def test_the_address_is_an_xnaddr_and_survives_intact() -> None:
    """What the second console will need, verbatim, to dial the first."""
    _, items = field("HNET").value
    _, members = items[0]
    address = next(m for m in members if m.label == "XDDR").value
    assert len(address) == 36
    assert address[0:4] == bytes([192, 168, 1, 25])       # LAN address
    assert address[8:10] == bytes([0x0C, 0x02])           # port 3074
    assert address[10:16].hex() == "7ced8d19694f"         # the console's MAC


def test_the_game_the_console_wanted_to_create() -> None:
    assert field("NTOP").value == 130      # PEER_TO_PEER_FULL_MESH
    assert field("PMAX").value == 2
    assert field("PCAP").value[1] == [2, 0, 0, 0]
    assert field("GTYP").value == "gameType0"
    assert field("VSTR").value == "qa-only-day45"


def test_a_plain_struct_list_is_untouched_by_the_union_rule() -> None:
    """852 struct lists across this repo's captures decode either way, and
    they have to keep doing so: a tag's first byte is never below 0x80, so a
    plain element and a union element can be told apart rather than guessed."""
    from blaze_tdf import INTEGER, Field
    plain = [[Field("ID", INTEGER, 7)], [Field("ID", INTEGER, 8)]]
    encoded = b"".join(encode_item(STRUCT, item) for item in plain)
    reader = Decoder(encoded)
    first = reader.list_item(STRUCT)
    second = reader.list_item(STRUCT)
    assert [f.label for f in first] == ["ID"]
    assert [f.value for f in second] == [8]
    assert reader.position == len(encoded)


def test_a_union_element_round_trips() -> None:
    from blaze_tdf import INTEGER, Field
    item = (0, [Field("XUID", INTEGER, 2535469248587161)])
    encoded = encode_item(STRUCT, item)
    reader = Decoder(encoded)
    active, members = reader.list_item(STRUCT)
    assert active == 0
    assert members[0].value == 2535469248587161
    assert reader.position == len(encoded)
