# Decoding `fifa_ng_db.db`

The game's own player database, inside `data0.big` at `data/db/fifa_ng_db.db`.
It is what a card for a player with no FUT version would have to come from --
`Abderazzak Hamdallah` is absent from all 14 019 FUT cards, and a card built
without his asset id would draw with no name and no portrait, because the title
resolves both from that id rather than from anything the server sends.

## What is established

Container header, same family as every other resource:

```text
chunklzx v2   total 2686556   chunk 262144   chunks 11   header 16
chunk 0: stored 126606, block type 3, data at 0x30
```

The multi-chunk walk itself is correct -- the descriptor of the next chunk sits
after this chunk's data with its own data 16-byte aligned, and that fix landed
earlier and holds for every resource that does decode.

## Where it fails

The first chunk's data does not begin with a readable LZX block header:

```text
first word  0x0a3b   bits 0000101000111011
E8 bit 0, block type 000 -> invalid
no E8 bit, block type 000 -> invalid
```

Sweeping bit offsets 0..48 and demanding *both* a valid block type and a
plausible block size gives exactly one candidate, which is worth recording
because it is not the usual forest of false positives:

```text
offset  E8  type          size
   16    0   2 (ALIGNED)  11239
```

That reads as a two-byte prefix before the LZX stream. But slicing two bytes
off and decoding reports block type 6, so the bit analysis and the decoder do
not agree on the alignment -- meaning the prefix is not simply two bytes, or
the stream is not plain LZX from there.

## What not to repeat

`cards_ng_db.db`, the FUT card database in `cards0.big`, was "decoded" once by
dropping the E8 header: it produced all 262144 bytes of chunk 0 without
raising, and the output was noise -- 56% zeros, 558 meaningless fragments, no
`BIGF` magic. A structurally valid decode is not a decode. Check the magic and
the string density before claiming anything, as `fcc_login.big` does at 33%
zeros with a readable constant pool.

## Next

The single coherent candidate at bit 16 is the thread to pull. Either the
prefix is a length or a checksum whose width is not two bytes, or the words
are ordered differently in these larger resources than in the small ones that
decode. Comparing the first bytes of a chunk that decodes against one of these
would settle which.
