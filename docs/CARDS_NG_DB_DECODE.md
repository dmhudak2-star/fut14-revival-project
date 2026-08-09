# Decoding the card database

`data/db/cards_ng_db.db` inside `cards0.big`, and its sibling
`data/db/fifa_ng_db.db` inside `data0.big`. These hold the consumables --
their asset ids, names and effects -- which is why they matter: consumables
served with invented ids draw NOT FOUND, fall back to a default type and apply
nothing, and there is no other source for them.

## Where it stands

45% of the card database decodes: 1 237 161 of 2 730 320 bytes, and 1 465 932
of 2 686 556 for the player database. Both open with the EA `DB` magic, and the
card database's own size field reads `0x29A950` -- 2 730 320, the same total the
container declares. The decoded portion carries real content: `eng_us`,
`rus_ru`, `swe_se` locale tags and club-name fragments.

That is against nothing at all before: every earlier attempt produced either an
immediate "unsupported block type" or structurally valid noise.

## What was wrong, and it was three separate things

**A two-byte prefix.** The bitstream does not start at the chunk's first byte.
Reading the header at offsets 0 through 5 shows exactly one that yields a
sensible block -- offset 2, type ALIGNED, a block size within the chunk. This
matches the bit-offset-16 candidate an earlier sweep had already found and not
acted on.

**The chunk size is a maximum, not a measurement.** These chunks decode to less
than 0x40000, and asking for the full amount ran the reader past the end of the
bitstream, where it read the next block header out of whatever followed. That
surfaced as "unsupported LZX block type", which reads like a format fault and
is an off-the-end fault.

**The window carries across chunks.** A match early in one chunk reaches back
into the one before it. Decoding each chunk from an empty window made those
matches read before the start of the window, and the chunk stopped a fraction
of the way in.

`decode_block` now takes `partial` and `history` for the last two.

## What is still missing

Each chunk stops partway rather than at a clean end, so something after the
first block or two is still misread. Scanning for further stream headers inside
a chunk does not discriminate: 71 "plausible" headers appear in the first 40 KB
of chunk 0, which is what random bytes produce. That approach cannot settle it.

The next step is to instrument the block loop and find where the reader's bit
position diverges from the block's declared size -- the point at which decoding
a block leaves the reader misaligned for the next header. That is a measurement
inside one chunk, not a search across the file.

## The trap this file exists to remember

`cards_ng_db.db` was once "decoded" by dropping the E8 header: it produced all
262 144 bytes of chunk 0 without raising, and the output was noise -- 56% zeros,
558 meaningless fragments, no magic. A structurally valid decode is not a
decode. Check the magic and the zero ratio first: a real decode here runs 17-36%
zeros and opens with `DB`.
