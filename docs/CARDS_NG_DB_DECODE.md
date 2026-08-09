# Decoding the card database

`data/db/cards_ng_db.db` inside `cards0.big`, and its sibling
`data/db/fifa_ng_db.db` inside `data0.big`. These hold the consumables -- their
asset ids and subtypes -- which is why they matter: consumables served with
invented ids draw NOT FOUND art, all carry the same name, and apply nothing.
There is no other source for them.

Both now decode whole: 2 730 320 bytes and 2 686 556, each matching the size
its own `DB` header declares. So do their meta XMLs, and every entry sampled
across all five BIG archives.

    python3 tools/extract_fifa_databases.py --out runtime/db
    python3 tools/build_consumables.py

## The format: XCompress frames

`chunklzx` is a container of chunks, and a chunk is **not** one LZX bitstream.
It is a run of frames, each producing at most 32 KiB, each introduced by its
own header: `FF <u16 raw> <u16 packed>` when the frame is short, a bare
`<u16 packed>` when it fills. This is what XCompress emits on the 360.

The check that settles it costs nothing and needs no decoder: walk the frame
headers and add up the output they claim. Ten chunks of eight full frames plus
a last of four gives 10 x 262 144 + 108 880 = **2 730 320**, which is the total
the container declares, to the byte.

Across a chunk's frames the decoder's state carries: the window, the Huffman
trees, the repeated offsets, and how much of the current block is left, because
a block announces far more than one frame holds. Only the bit reader restarts,
each frame's bitstream being byte-aligned behind its own length. Between chunks
nothing carries -- each chunk restarts the window, which is what makes the
container randomly addressable.

A resource small enough for one frame is the `FF` case and nothing else. The
decoder read only that case, which is why it managed every single-frame
resource in the title update and none of the databases.

## The layout inside

A directory of tables, each a block of fixed-size records whose fields are
packed to the bit. Table and field names are not in the file -- it carries
four-character shortnames, and the `-meta.xml` beside it maps them: `igAa` is
`fcc_healingcards`, `IKbB` is `cardassetid`.

The field descriptors are **sorted by shortname**, not left in declaration
order, and each gives the field's bit offset and depth. So the meta file gives
the names and the descriptors give the layout, and neither alone is enough:
reading the meta file's order straight onto the records puts `weightrare` where
`cardassetid` should be and every value comes out plausible and wrong.

Strings are type 13 -- a 32-bit offset into a Huffman-compressed pool after the
records. `tools/archive/t3db.py` returns those offsets as they are. The
consumables need none of them.

## What came out

    fcc_contractcards    13   subtypes 201-202
    fcc_healingcards     27   subtypes 211-220
    fcc_trainingcards   142   subtypes 51-340
    fcc_misccards        21   subtypes 231-233

and, the point of the exercise, the subtype blocks land one-to-one on the
member names `CardsDLL` counts consumables under. 201 against 202 is
`consumablesContractPlayer` against `consumablesContractManager`; 219 against
220 is a player's fitness against the team's; 51-57 against 61-67 is an
outfielder's training against a keeper's. Two sources that were read
independently agreeing to that degree is the strongest evidence in this project
that either is being read correctly.

124 of these go into the club. The rest are manager modifiers and coin boosts:
they are real cards, and nothing in the database says which member each block
belongs to, so naming them would be a guess of exactly the kind this file
exists to have stopped.

## Three faults, and one that was hiding

Finding the frames fixed the format. Three other things were wrong underneath:

* a two-byte prefix before each frame's bitstream -- the frame length,
* the container's chunk size being a maximum rather than a measurement, so
  asking for the full 0x40000 ran the reader past the end of the bitstream and
  it read the next block header out of whatever followed, which surfaced as
  "unsupported block type" -- a format fault by appearance, an off-the-end one
  in fact,
* and `decode_container` counting the chunk descriptor twice when advancing,
  landing 16 bytes past every descriptor after the first. Harmless on any
  resource of one chunk, which is nearly all of them, and fatal on every
  database. Fixing it took the sampled failures across the five archives from
  nine to none.

## The trap this file exists to remember

`cards_ng_db.db` was once "decoded" by dropping the E8 header: it produced all
262 144 bytes of chunk 0 without raising, and the output was noise -- 56%
zeros, 558 meaningless fragments, no magic. A structurally valid decode is not
a decode.

Then, hunting the tail, a scan for further stream headers inside chunk 0
reported 71 plausible ones in the first 40 KB. That is what random bytes
produce, and following it would have cost days. What settled the format instead
was arithmetic on the frame headers -- a check with an exact expected answer,
which noise cannot pass.
