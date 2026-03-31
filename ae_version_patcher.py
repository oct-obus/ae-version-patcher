#!/usr/bin/env python3
"""
AE Version Patcher — Extend After Effects "Save a Copy As" Version Range

Patches Adobe After Effects 26's BEE.dll to unlock saving projects as older
AE versions (down to CC 2017) using AE's own built-in version-conditional
serialization, which already handles format differences going back that far.

By default AE 26 only offers "Save a Copy As AE 24" and "Save a Copy As AE 25".
This tool changes which two versions appear in that menu.

IMPORTANT:
  - This tool ONLY works with AE 26's BEE.dll (the offsets are version-specific)
  - Always back up the original BEE.dll before patching
  - Adobe may detect the modification — use at your own risk
  - The patched AE will show exactly 2 "Save a Copy As" menu items

Usage:
  python ae_version_patcher.py <input_BEE.dll> <output_BEE.dll> [--min-version N]

Examples:
  # Default: save as AE 24 / AE 25 (same as stock AE 26)
  python ae_version_patcher.py BEE.dll BEE_patched.dll

  # Save as AE 22 / AE 23
  python ae_version_patcher.py BEE.dll BEE_patched.dll --min-version 22

  # Save as CC 2017 (v14) / CC 2018 (v15)
  python ae_version_patcher.py BEE.dll BEE_patched.dll --min-version 14

  # Save as CC 2021 (v18) / AE 22
  python ae_version_patcher.py BEE.dll BEE_patched.dll --min-version 18
"""

import argparse
import shutil
import struct
import sys

# AE 26 BEE.dll patch locations in GetSupportedSaveAsPreviousVersions
# These are the single bytes that hold the "internal version" constants.
# Internal version = AE public version - 3
# File format byte = internal version + 0x4A
PATCH_OFFSET_NEWER = 0x4F9B1F  # stock value: 0x16 (internal 22 = AE 25)
PATCH_OFFSET_OLDER = 0x4F9BC2  # stock value: 0x15 (internal 21 = AE 24)

STOCK_NEWER = 0x16
STOCK_OLDER = 0x15

# Fingerprint bytes around the patch sites to verify we have the right DLL
FINGERPRINT_NEWER = (0x4F9B1C, bytes([0xC7, 0x40, 0x1C]))  # mov [rax+0x1c], imm32
FINGERPRINT_OLDER = (0x4F9BBF, bytes([0xC7, 0x40, 0x1C]))  # mov [rax+0x1c], imm32

# Version name mapping
VERSION_NAMES = {
    14: "CC 2017 (14.x)",
    15: "CC 2018 (15.x)",
    16: "CC 2019 (16.x)",
    17: "2020 (17.x)",
    18: "2021 (18.x)",
    22: "2022 (22.x)",
    23: "2023 (23.x)",
    24: "2024 (24.x)",
    25: "2025 (25.x)",
    26: "2026 (26.x)",
}

# Well-tested version thresholds found in AE 26's binary (via RE analysis).
# These are the file format version composites where ShouldReadWriteForVersion
# checks exist, confirming AE 26 knows how to serialize for these targets.
KNOWN_THRESHOLDS = [
    (0x57, 5, "CC 2017"),
    (0x58, 2, "CC 2018"),
    (0x5C, 3, "2021/18.x"),
    (0x5D, 2, "AE 22"),
    (0x5E, 1, "AE 23"),
    (0x5F, 0, "AE 24"),
    (0x60, 0, "AE 25"),
]


def internal_version(ae_public: int) -> int:
    """AE public version → internal version number."""
    return ae_public - 3


def ae_public(internal: int) -> int:
    """Internal version number → AE public version."""
    return internal + 3


def file_format_byte(internal: int) -> int:
    """Internal version number → file format header byte."""
    return internal + 0x4A


def version_display(ae_ver: int) -> str:
    """Human-readable version name."""
    return VERSION_NAMES.get(ae_ver, f"AE {ae_ver}")


def verify_dll(data: bytes) -> bool:
    """Check that this looks like the expected AE 26 BEE.dll."""
    for offset, expected in [FINGERPRINT_NEWER, FINGERPRINT_OLDER]:
        if data[offset:offset + len(expected)] != expected:
            return False
    if data[PATCH_OFFSET_NEWER] not in range(0x10, 0x20):
        return False
    if data[PATCH_OFFSET_OLDER] not in range(0x10, 0x20):
        return False
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Patch AE 26 BEE.dll to extend 'Save a Copy As' version range",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Version mapping:
  AE 14 = CC 2017    AE 18 = 2021      AE 23 = 2023      
  AE 15 = CC 2018    AE 22 = 2022      AE 24 = 2024
  AE 16 = CC 2019    (19-21 skipped)    AE 25 = 2025
  AE 17 = 2020                          AE 26 = 2026 (current)

The --min-version flag sets the OLDER of the two menu items.
The newer item will be min-version + 1.

Example: --min-version 22 gives you "Save as AE 22" and "Save as AE 23"
""",
    )
    parser.add_argument("input", help="Path to original BEE.dll")
    parser.add_argument("output", help="Path for patched output BEE.dll")
    parser.add_argument(
        "--min-version",
        type=int,
        default=24,
        help="Minimum AE version for Save-As menu (default: 24, range: 14-25)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip DLL verification (use if the fingerprint check fails)",
    )
    args = parser.parse_args()

    min_ver = args.min_version
    max_ver = min_ver + 1

    if not (14 <= min_ver <= 25):
        print(f"Error: --min-version must be 14-25, got {min_ver}", file=sys.stderr)
        sys.exit(1)

    if max_ver >= 26:
        print(
            f"Error: --min-version {min_ver} would set max to {max_ver} which is the current version",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(args.input, "rb") as f:
        data = bytearray(f.read())

    if not args.force and not verify_dll(data):
        print(
            "Error: This doesn't look like AE 26's BEE.dll.",
            file=sys.stderr,
        )
        print(
            "       The patch offsets are specific to AE 26. Use --force to override.",
            file=sys.stderr,
        )
        sys.exit(1)

    current_newer = data[PATCH_OFFSET_NEWER]
    current_older = data[PATCH_OFFSET_OLDER]

    print(f"Current values:")
    print(
        f"  Newer target: internal {current_newer} = {version_display(ae_public(current_newer))}"
    )
    print(
        f"  Older target: internal {current_older} = {version_display(ae_public(current_older))}"
    )

    new_newer = internal_version(max_ver)
    new_older = internal_version(min_ver)

    print(f"\nPatching to:")
    print(f"  Menu item 1 (older): {version_display(min_ver)}")
    print(f"    internal {new_older}, file format byte 0x{file_format_byte(new_older):02X}")
    print(f"  Menu item 2 (newer): {version_display(max_ver)}")
    print(f"    internal {new_newer}, file format byte 0x{file_format_byte(new_newer):02X}")

    data[PATCH_OFFSET_NEWER] = new_newer
    data[PATCH_OFFSET_OLDER] = new_older

    with open(args.output, "wb") as f:
        f.write(data)

    print(f"\n✅ Patched successfully → {args.output}")
    print(f"\nTo install:")
    print(f"  1. Close After Effects completely")
    print(f"  2. Navigate to your AE 26 install directory:")
    print(f"     C:\\Program Files\\Adobe\\Adobe After Effects 2026\\Support Files\\")
    print(f"  3. Rename original BEE.dll → BEE.dll.bak")
    print(f"  4. Copy {args.output} → BEE.dll")
    print(f"  5. Launch After Effects")
    print(f"  6. File → Save a Copy As → should show {version_display(min_ver)} and {version_display(max_ver)}")


if __name__ == "__main__":
    main()
