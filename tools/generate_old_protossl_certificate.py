#!/usr/bin/env python3
"""Generate the redirector certificate accepted by legacy EA ProtoSSL.

The method is the public Bug_OldProtoSSL workaround by Aim4Kill: create the
expected EA issuer/subject certificate, then change only the outer (second)
MD5-with-RSA algorithm OID to rsaEncryption.  The signature bytes themselves
are left untouched.  This is used solely by the local preservation server.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


MD5_WITH_RSA_OID = bytes.fromhex("2A864886F70D010104")
RSA_ENCRYPTION_OID = bytes.fromhex("2A864886F70D010101")
CA_SUBJECT = (
    "/OU=Online Technology Group/O=Electronic Arts, Inc./L=Redwood City"
    "/ST=California/C=US/CN=OTG3 Certificate Authority"
)
SERVER_SUBJECT = (
    "/CN=gosredirector.ea.com/OU=Global Online Studio"
    "/O=Electronic Arts, Inc./ST=California/C=US"
)


def patch_outer_signature_oid(certificate_der: bytes) -> bytes:
    offsets: list[int] = []
    cursor = 0
    while True:
        offset = certificate_der.find(MD5_WITH_RSA_OID, cursor)
        if offset < 0:
            break
        offsets.append(offset)
        cursor = offset + len(MD5_WITH_RSA_OID)
    if len(offsets) != 2:
        raise RuntimeError(
            "Expected exactly two MD5-with-RSA OIDs in the certificate; "
            f"found {len(offsets)}"
        )
    result = bytearray(certificate_der)
    outer_offset = offsets[1]
    result[outer_offset : outer_offset + len(MD5_WITH_RSA_OID)] = (
        RSA_ENCRYPTION_OID
    )
    return bytes(result)


def run(*arguments: str) -> None:
    subprocess.run(arguments, check=True)


def generate(output: Path, *, force: bool = False) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    ca_key = output / "otg3-ca.key.pem"
    ca_cert = output / "otg3-ca.crt.pem"
    server_key = output / "gosredirector.key.pem"
    server_csr = output / "gosredirector.csr.pem"
    server_cert = output / "gosredirector.crt.pem"
    server_der = output / "gosredirector.der"
    modified_der = output / "gosredirector-old-protossl.der"
    modified_cert = output / "gosredirector-old-protossl.crt.pem"
    serial = output / "otg3-ca.crt.srl"
    generated = (
        ca_key,
        ca_cert,
        server_key,
        server_csr,
        server_cert,
        server_der,
        modified_der,
        modified_cert,
        serial,
    )

    if modified_cert.exists() and server_key.exists() and not force:
        return modified_cert, server_key
    if force:
        for path in generated:
            path.unlink(missing_ok=True)

    run("openssl", "genrsa", "-out", str(ca_key), "1024")
    run(
        "openssl",
        "req",
        "-new",
        "-md5",
        "-x509",
        "-days",
        "10000",
        "-key",
        str(ca_key),
        "-out",
        str(ca_cert),
        "-subj",
        CA_SUBJECT,
    )
    run("openssl", "genrsa", "-out", str(server_key), "1024")
    run(
        "openssl",
        "req",
        "-new",
        "-key",
        str(server_key),
        "-out",
        str(server_csr),
        "-subj",
        SERVER_SUBJECT,
    )
    run(
        "openssl",
        "x509",
        "-req",
        "-in",
        str(server_csr),
        "-CA",
        str(ca_cert),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-out",
        str(server_cert),
        "-days",
        "10000",
        "-md5",
    )
    run(
        "openssl",
        "x509",
        "-outform",
        "der",
        "-in",
        str(server_cert),
        "-out",
        str(server_der),
    )
    modified_der.write_bytes(patch_outer_signature_oid(server_der.read_bytes()))
    run(
        "openssl",
        "x509",
        "-inform",
        "der",
        "-in",
        str(modified_der),
        "-out",
        str(modified_cert),
    )
    os.chmod(ca_key, 0o600)
    os.chmod(server_key, 0o600)
    return modified_cert, server_key


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    certificate, key = generate(args.output, force=args.force)
    print(f"certificate={certificate}")
    print(f"key={key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
