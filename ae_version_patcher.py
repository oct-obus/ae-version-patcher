#!/usr/bin/env python3
"""
AE Version Patcher -- Extend After Effects "Save a Copy As" Version Range

Patches Adobe After Effects 26's BEE.dll to unlock saving projects as older
AE versions (down to CC 2014) using AE's own built-in version-conditional
serialization, which already handles format differences going back that far.

By default AE 26 only offers "Save a Copy As AE 24" and "Save a Copy As AE 25".
This tool changes which two versions appear in that menu.

IMPORTANT:
  - This tool ONLY works with AE 26's BEE.dll (the offsets are version-specific)
  - Always back up the original BEE.dll before patching
  - Adobe may detect the modification -- use at your own risk
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
# These are the single bytes that hold the "save type" constants.
# BEE_SaveTypeToAppVersNum converts save types to display version numbers.
PATCH_OFFSET_NEWER = 0x4F9B1F  # stock value: 0x16 (save type 22 -> AE 25)
PATCH_OFFSET_OLDER = 0x4F9BC2  # stock value: 0x15 (save type 21 -> AE 24)

STOCK_NEWER = 0x16
STOCK_OLDER = 0x15

# Fingerprint bytes around the patch sites to verify we have the right DLL
FINGERPRINT_NEWER = (0x4F9B1C, bytes([0xC7, 0x40, 0x1C]))  # mov [rax+0x1c], imm32
FINGERPRINT_OLDER = (0x4F9BBF, bytes([0xC7, 0x40, 0x1C]))  # mov [rax+0x1c], imm32

# Save type -> AE public version mapping (from BEE_SaveTypeToAppVersNum disassembly)
#
# The "save type" is the internal enum value stored in the set returned by
# GetSupportedSaveAsPreviousVersions. BEE_SaveTypeToAppVersNum converts it:
#   save type 10     -> CS3 (8.x)      [special case]
#   save type 11     -> CS5 (10.x)     [special case]
#   save type 12     -> CS6 (11.x)     [special case]
#   save type 13-18  -> same (13.x-18.x) [identity range]
#   save type 19-26  -> +3 (22.x-26.x)   [offset range: AE skipped versions 19-21]
#
# For AE versions >= 22, save_type = public_version - 3.
# For AE versions 13-18, save_type = public_version.

# Valid public AE versions for --min-version, mapped to save type values
VERSION_TO_SAVE_TYPE = {
    13: 13,  # CC 2014 (13.x)
    14: 14,  # CC 2017 (14.x)
    15: 15,  # CC 2018 (15.x)
    16: 16,  # CC 2019 (16.x)
    17: 17,  # 2020 (17.x)
    18: 18,  # 2021 (18.x)
    22: 19,  # 2022 (22.x) -- save type 19 displays as 22 via +3
    23: 20,  # 2023 (23.x) -- save type 20 displays as 23 via +3
    24: 21,  # 2024 (24.x) -- save type 21 displays as 24 via +3
    25: 22,  # 2025 (25.x) -- save type 22 displays as 25 via +3
}

SAVE_TYPE_TO_VERSION = {v: k for k, v in VERSION_TO_SAVE_TYPE.items()}

# Version name mapping
VERSION_NAMES = {
    13: "CC 2014 (13.x)",
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
# File format byte = save_type + 0x4A for all versions.
KNOWN_THRESHOLDS = [
    (0x57, 5, "CC 2014 (13.x)"),
    (0x58, 2, "CC 2017 (14.x)"),
    (0x5C, 3, "2021 (18.x)"),
    (0x5D, 2, "2022 (22.x)"),
    (0x5E, 1, "2023 (23.x)"),
    (0x5F, 0, "2024 (24.x)"),
    (0x60, 0, "2025 (25.x)"),
]

# Sorted list of valid --min-version values (handles the 19-21 gap)
VALID_VERSIONS = sorted(VERSION_TO_SAVE_TYPE.keys())


def save_type_for_version(ae_public: int) -> int:
    """AE public version -> save type value for the DLL patch."""
    if ae_public not in VERSION_TO_SAVE_TYPE:
        raise ValueError(f"No save type mapping for AE {ae_public}")
    return VERSION_TO_SAVE_TYPE[ae_public]


def version_for_save_type(st: int) -> int:
    """Save type value -> AE public version."""
    if st in SAVE_TYPE_TO_VERSION:
        return SAVE_TYPE_TO_VERSION[st]
    return st  # fallback for unknown values


def next_version(ae_ver: int) -> int:
    """Return the next valid AE version after ae_ver (handles 18->22 gap)."""
    idx = VALID_VERSIONS.index(ae_ver)
    if idx + 1 < len(VALID_VERSIONS):
        return VALID_VERSIONS[idx + 1]
    raise ValueError(f"No version after AE {ae_ver}")


def version_display(ae_ver: int) -> str:
    """Human-readable version name."""
    return VERSION_NAMES.get(ae_ver, f"AE {ae_ver}")


def verify_dll(data: bytes) -> bool:
    """Check that this looks like the expected AE 26 BEE.dll."""
    for offset, expected in [FINGERPRINT_NEWER, FINGERPRINT_OLDER]:
        if data[offset:offset + len(expected)] != expected:
            return False
    # Save type values should be in a reasonable range (10-26)
    if data[PATCH_OFFSET_NEWER] not in range(0x0A, 0x1B):
        return False
    if data[PATCH_OFFSET_OLDER] not in range(0x0A, 0x1B):
        return False
    return True


def main():
    valid_vers_str = ", ".join(str(v) for v in VALID_VERSIONS)
    parser = argparse.ArgumentParser(
        description="Patch AE 26 BEE.dll to extend 'Save a Copy As' version range",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Version mapping:
  AE 13 = CC 2014    AE 17 = 2020      AE 23 = 2023      
  AE 14 = CC 2017    AE 18 = 2021      AE 24 = 2024
  AE 15 = CC 2018    AE 22 = 2022      AE 25 = 2025
  AE 16 = CC 2019    (19-21 skipped)    AE 26 = 2026 (current)

Valid --min-version values: {valid_vers_str}

The --min-version flag sets the OLDER of the two menu items.
The newer item will be the next AE version (handles 18->22 gap).

Example: --min-version 22 gives you "Save as AE 22" and "Save as AE 23"
Example: --min-version 18 gives you "Save as AE 18 (2021)" and "Save as AE 22 (2022)"
""",
    )
    parser.add_argument("input", help="Path to original BEE.dll")
    parser.add_argument("output", help="Path for patched output BEE.dll")
    parser.add_argument(
        "--min-version",
        type=int,
        default=24,
        help=f"Minimum AE version for Save-As menu (default: 24, valid: {valid_vers_str})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip DLL verification (use if the fingerprint check fails)",
    )
    args = parser.parse_args()

    min_ver = args.min_version

    if min_ver not in VERSION_TO_SAVE_TYPE:
        print(f"Error: --min-version must be one of {valid_vers_str}, got {min_ver}",
              file=sys.stderr)
        sys.exit(1)

    try:
        max_ver = next_version(min_ver)
    except ValueError:
        print(f"Error: --min-version {min_ver} has no next version (it's the latest supported)",
              file=sys.stderr)
        sys.exit(1)

    if max_ver >= 26:
        print(
            f"Error: --min-version {min_ver} would set the newer item to {max_ver} "
            f"which is the current version",
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
        f"  Newer target: save type {current_newer} = {version_display(version_for_save_type(current_newer))}"
    )
    print(
        f"  Older target: save type {current_older} = {version_display(version_for_save_type(current_older))}"
    )

    new_newer = save_type_for_version(max_ver)
    new_older = save_type_for_version(min_ver)

    print(f"\nPatching to:")
    print(f"  Menu item 1 (older): {version_display(min_ver)}")
    print(f"    save type {new_older}, file format byte 0x{new_older + 0x4A:02X}")
    print(f"  Menu item 2 (newer): {version_display(max_ver)}")
    print(f"    save type {new_newer}, file format byte 0x{new_newer + 0x4A:02X}")

    data[PATCH_OFFSET_NEWER] = new_newer
    data[PATCH_OFFSET_OLDER] = new_older

    with open(args.output, "wb") as f:
        f.write(data)

    print(f"\nPatched successfully -> {args.output}")
    print(f"\nTo install:")
    print(f"  1. Close After Effects completely")
    print(f"  2. Navigate to your AE 26 install directory:")
    print(f"     C:\\Program Files\\Adobe\\Adobe After Effects 2026\\Support Files\\")
    print(f"  3. Rename original BEE.dll -> BEE.dll.bak")
    print(f"  4. Copy {args.output} -> BEE.dll")
    print(f"  5. Launch After Effects")
    print(f"  6. File -> Save a Copy As -> should show {version_display(min_ver)} and {version_display(max_ver)}")


if __name__ == "__main__":
    main()
