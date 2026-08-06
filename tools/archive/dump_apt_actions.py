#!/usr/bin/env python3
"""Disassemble EA APT ActionScript bytecode (big-endian console format).

This intentionally focuses on static inspection: it does not modify game files.
The APT data and constant files are normally the two members of an APT-in-BIG
screen resource.
"""

from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from pathlib import Path


OP_NAMES = {
    0x00: "End", 0x04: "NextFrame", 0x05: "PrevFrame", 0x06: "Play",
    0x07: "Stop", 0x08: "ToggleQuality", 0x09: "StopSounds",
    0x0A: "Add", 0x0B: "Subtract", 0x0C: "Multiply", 0x0D: "Divide",
    0x0E: "Equals", 0x0F: "LessThan", 0x10: "And", 0x11: "Or",
    0x12: "Not", 0x13: "StringEquals", 0x14: "StringLength",
    0x15: "SubString", 0x17: "Pop", 0x18: "ToInteger",
    0x1C: "GetVariable", 0x1D: "SetVariable", 0x20: "SetTarget2",
    0x21: "StringConcat", 0x22: "GetProperty", 0x23: "SetProperty",
    0x24: "CloneSprite", 0x25: "RemoveSprite", 0x26: "Trace",
    0x27: "StartDragMovie", 0x28: "StopDragMovie", 0x29: "StringCompare",
    0x2A: "Throw", 0x2B: "CastOp", 0x2C: "ImplementsOp",
    0x30: "Random", 0x31: "MbLength", 0x32: "Ord", 0x33: "Chr",
    0x34: "GetTimer", 0x35: "MbSubString", 0x36: "MbOrd", 0x37: "MbChr",
    0x3A: "Delete", 0x3B: "Delete2", 0x3C: "DefineLocal",
    0x3D: "CallFunction", 0x3E: "Return", 0x3F: "Modulo",
    0x40: "NewObject", 0x41: "Var", 0x42: "InitArray",
    0x43: "InitObject", 0x44: "TypeOf", 0x45: "TargetPath",
    0x46: "Enumerate", 0x47: "Add2", 0x48: "LessThan2",
    0x49: "Equals2", 0x4A: "ToNumber", 0x4B: "ToString",
    0x4C: "PushDuplicate", 0x4D: "StackSwap", 0x4E: "GetMember",
    0x4F: "SetMember", 0x50: "Increment", 0x51: "Decrement",
    0x52: "CallMethod", 0x53: "NewMethod", 0x54: "InstanceOf",
    0x55: "Enumerate2", 0x56: "EA_PushThis", 0x58: "EA_PushGlobal",
    0x59: "EA_PushZero", 0x5A: "EA_PushOne", 0x5B: "EA_CallFuncPop",
    0x5C: "EA_CallFunc", 0x5D: "EA_CallMethodPop", 0x5E: "EA_CallMethod",
    0x60: "BitwiseAnd", 0x61: "BitwiseOr", 0x62: "BitwiseXOr",
    0x63: "ShiftLeft", 0x64: "ShiftRight", 0x65: "ShiftRight2",
    0x66: "StrictEqual", 0x67: "Greater", 0x68: "StringGreater",
    0x69: "Extends", 0x70: "EA_PushThisVar", 0x71: "EA_PushGlobalVar",
    0x72: "EA_ZeroVar", 0x73: "EA_PushTrue", 0x74: "EA_PushFalse",
    0x75: "EA_PushNull", 0x76: "EA_PushUndefined", 0x77: "TraceStart",
    0x81: "GotoFrame", 0x83: "GetURL", 0x87: "SetRegister",
    0x88: "ConstantPool", 0x8A: "WaitForFrame", 0x8B: "SetTarget",
    0x8C: "GotoLabel", 0x8D: "WaitForFrameExpr", 0x8E: "DefineFunction2",
    0x8F: "Try", 0x94: "With", 0x96: "PushData",
    0x99: "BranchAlways", 0x9A: "GetURL2", 0x9B: "DefineFunction",
    0x9D: "BranchIfTrue", 0x9E: "CallFrame", 0x9F: "GotoFrame2",
    0xA1: "EA_PushString", 0xA2: "EA_PushConstantByte",
    0xA3: "EA_PushConstantWord", 0xA4: "EA_GetStringVar",
    0xA5: "EA_GetStringMember", 0xA6: "EA_SetStringVar",
    0xA7: "EA_SetStringMember", 0xAE: "EA_PushValueOfVar",
    0xAF: "EA_GetNamedMember", 0xB0: "EA_CallNamedFuncPop",
    0xB1: "EA_CallNamedFunc", 0xB2: "EA_CallNamedMethodPop",
    0xB3: "EA_CallNamedMethod", 0xB4: "EA_PushFloat",
    0xB5: "EA_PushByte", 0xB6: "EA_PushShort", 0xB7: "EA_PushLong",
    0xB8: "EA_BranchIfFalse", 0xB9: "EA_PushRegister", 0xFF: "Padding",
}

ALIGNED = {0x81, 0x83, 0x87, 0x88, 0x8B, 0x8C, 0x8E, 0x94, 0x96,
           0x99, 0x9B, 0x9D, 0x9F, 0xA1, 0xA4, 0xA5, 0xA6, 0xA7, 0xB8}
DIRECT_STRING = {0xA1, 0xA4, 0xA5, 0xA6, 0xA7, 0x8C}
POOL_BYTE = {0xA2, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3}


def u16(data: bytes, off: int) -> int:
    return struct.unpack_from(">H", data, off)[0]


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from(">I", data, off)[0]


def i32(data: bytes, off: int) -> int:
    return struct.unpack_from(">i", data, off)[0]


def cstring(data: bytes, off: int) -> str:
    if not 0 <= off < len(data):
        return f"<bad-string@0x{off:X}>"
    end = data.find(b"\0", off)
    if end < 0:
        end = len(data)
    return data[off:end].decode("utf-8", "replace")


@dataclass
class Constant:
    kind: int
    value: object

    def display(self) -> str:
        if isinstance(self.value, str):
            return repr(self.value)
        return str(self.value)


def read_constants(data: bytes) -> list[Constant]:
    if not data.startswith(b"Apt constant file"):
        raise ValueError("not an APT constant file")
    count = u32(data, 24)
    header_size = u32(data, 28)
    if header_size != 32:
        raise ValueError(f"unexpected constant header size {header_size}")
    result = []
    for n in range(count):
        off = 32 + n * 8
        kind, raw = u32(data, off), u32(data, off + 4)
        if kind == 1:
            value = cstring(data, raw)
        elif kind == 5:
            value = bool(raw)
        elif kind == 6:
            value = struct.unpack(">f", struct.pack(">I", raw))[0]
        elif kind == 7:
            value = struct.unpack(">i", struct.pack(">I", raw))[0]
        elif kind == 3:
            value = None
        else:
            value = raw
        result.append(Constant(kind, value))
    return result


@dataclass
class Instruction:
    start: int
    end: int
    opcode: int
    args: str = ""
    function_name: str | None = None
    body_start: int | None = None
    body_end: int | None = None


def resolve_pool(pool: list[int], constants: list[Constant], index: int) -> str:
    if index >= len(pool):
        return f"pool[{index}]<out-of-range>"
    const_index = pool[index]
    if const_index >= len(constants):
        return f"pool[{index}]=const[{const_index}]<out-of-range>"
    return f"pool[{index}]=const[{const_index}]={constants[const_index].display()}"


def decode(data: bytes, constants: list[Constant], start: int, end: int) -> list[Instruction]:
    out: list[Instruction] = []
    pool: list[int] = []
    pos = start
    while pos < end:
        ins_start = pos
        op = data[pos]
        pos += 1
        if op in ALIGNED:
            pos = (pos + 3) & ~3
        args = ""
        fn_name = None
        body_start = body_end = None

        if op == 0x88:  # ConstantPool
            count, table = u32(data, pos), u32(data, pos + 4)
            pos += 8
            pool = [u32(data, table + i * 4) for i in range(count)]
            args = f"count={count} table=0x{table:X}"
        elif op == 0x8E:  # DefineFunction2
            name_off, n_params = u32(data, pos), u32(data, pos + 4)
            n_regs = data[pos + 8]
            flags = int.from_bytes(data[pos + 9:pos + 12], "big")
            param_table = u32(data, pos + 12)
            body_size = i32(data, pos + 16)
            pos += 28
            fn_name = cstring(data, name_off)
            params = []
            for n in range(n_params):
                p = param_table + n * 8
                params.append(f"r{i32(data, p)}={cstring(data, u32(data, p + 4))}")
            body_start, body_end = pos, pos + body_size
            args = (f"{fn_name}({', '.join(params)}) regs={n_regs} flags=0x{flags:06X} "
                    f"body=0x{body_start:X}..0x{body_end:X}")
        elif op == 0x9B:  # DefineFunction
            name_off = u32(data, pos)
            n_params, param_table = i32(data, pos + 4), u32(data, pos + 8)
            body_size = i32(data, pos + 12)
            pos += 24
            fn_name = cstring(data, name_off)
            params = [cstring(data, u32(data, param_table + n * 4)) for n in range(n_params)]
            body_start, body_end = pos, pos + body_size
            args = f"{fn_name}({', '.join(params)}) body=0x{body_start:X}..0x{body_end:X}"
        elif op in (0x99, 0x9D, 0xB8):
            rel = i32(data, pos)
            pos += 4
            args = f"{rel:+d} -> 0x{pos + rel:X}"
        elif op in DIRECT_STRING:
            string_off = u32(data, pos)
            pos += 4
            args = f"0x{string_off:X} {cstring(data, string_off)!r}"
        elif op in POOL_BYTE:
            index = data[pos]
            pos += 1
            args = resolve_pool(pool, constants, index)
        elif op in (0xA3,):
            index = u16(data, pos)
            pos += 2
            args = resolve_pool(pool, constants, index)
        elif op in (0xB9, 0xB5):
            value = data[pos]
            pos += 1
            args = f"r{value}" if op == 0xB9 else str(value)
        elif op == 0xB6:
            args = str(u16(data, pos))
            pos += 2
        elif op == 0xB7:
            args = str(i32(data, pos))
            pos += 4
        elif op == 0xB4:
            args = str(struct.unpack_from(">f", data, pos)[0])
            pos += 4
        elif op in (0x81, 0x87, 0x9F):
            args = str(i32(data, pos))
            pos += 4
        elif op in (0x83,):
            a, b = u32(data, pos), u32(data, pos + 4)
            pos += 8
            args = f"{cstring(data, a)!r}, {cstring(data, b)!r}"
        elif op == 0x96:
            count, table = u32(data, pos), u32(data, pos + 4)
            pos += 8
            values = [u32(data, table + i * 4) for i in range(count)]
            args = ", ".join(resolve_pool(pool, constants, x) for x in values)
        elif op in (0x8A, 0x8B, 0x8D, 0x8F, 0x94):
            raise ValueError(f"unsupported structured opcode {OP_NAMES.get(op)} at 0x{ins_start:X}")
        elif op not in OP_NAMES:
            raise ValueError(f"unknown opcode 0x{op:02X} at 0x{ins_start:X}")

        out.append(Instruction(ins_start, pos, op, args, fn_name, body_start, body_end))
    return out


def locate_action_stream(data: bytes) -> int:
    # Movie character records carry an action-list pointer. For a standalone
    # extracted screen this signature is also a reliable, explicit fallback.
    marker = b"\x88\x00\x00\x00"
    candidates = [n for n in range(0, min(len(data), 0x1000), 4)
                  if data[n:n + 4] == marker]
    if not candidates:
        raise ValueError("no aligned ConstantPool instruction found")
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("apt_data", type=Path)
    parser.add_argument("apt_constants", type=Path)
    parser.add_argument("--start", type=lambda x: int(x, 0))
    parser.add_argument("--end", type=lambda x: int(x, 0))
    args = parser.parse_args()

    apt = args.apt_data.read_bytes()
    constants = read_constants(args.apt_constants.read_bytes())
    start = args.start if args.start is not None else locate_action_stream(apt)
    # The first ConstantPool points at its out-of-line index table, which also
    # marks the end of the executable bytecode in retail EA screen resources.
    inferred_end = u32(apt, ((start + 1 + 3) & ~3) + 4)
    end = args.end if args.end is not None else inferred_end
    instructions = decode(apt, constants, start, end)

    functions = [i for i in instructions if i.function_name]
    for ins in instructions:
        owners = [f for f in functions if f.body_start <= ins.start < f.body_end]
        indent = "  " * len(owners)
        owner = owners[-1].function_name if owners else "<top>"
        op_name = OP_NAMES.get(ins.opcode, f"op_{ins.opcode:02X}")
        print(f"0x{ins.start:04X} {owner:24} {indent}{op_name:24} {ins.args}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
