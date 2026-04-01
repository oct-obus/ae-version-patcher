#!/usr/bin/env python3
"""
AE Version Patcher -- Extend After Effects "Save a Copy As" Version Range

Patches Adobe After Effects' BEE.dll to unlock saving projects as older
AE versions (down to CC 2014) using AE's own built-in version-conditional
serialization, which already handles format differences going back that far.

Works across AE versions by dynamically locating patch sites via PE export
symbols and AOB (Array of Bytes) scanning -- no hardcoded offsets.

IMPORTANT:
  - Always back up the original BEE.dll before patching
  - Adobe may detect the modification -- use at your own risk
  - The patched AE will show exactly 2 "Save a Copy As" menu items

Requires: pefile (pip install pefile)

Usage:
  python ae_version_patcher.py <input_BEE.dll> <output_BEE.dll> [--min-version N]

Examples:
  # Default: restore stock behavior (whatever the DLL shipped with)
  python ae_version_patcher.py BEE.dll BEE_patched.dll

  # Save as AE 22 / AE 23
  python ae_version_patcher.py BEE.dll BEE_patched.dll --min-version 22

  # Save as CC 2017 (v14) / CC 2018 (v15)
  python ae_version_patcher.py BEE.dll BEE_patched.dll --min-version 14

  # Save as CC 2021 (v18) / AE 22
  python ae_version_patcher.py BEE.dll BEE_patched.dll --min-version 18
"""

import argparse
import sys

try:
    import pefile
except ImportError:
    print("Error: 'pefile' module is required. Install it with: pip install pefile",
          file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Export symbol used to locate the function in any AE version's BEE.dll.
# This is the MSVC-mangled name of BEE_GetSupportedSaveAsPreviousVersions,
# which returns a std::set<int> of save type values.
# ---------------------------------------------------------------------------
EXPORT_NAME = b"?BEE_GetSupportedSaveAsPreviousVersions@@YA?AV?$set@HU?$less@H@std@@V?$allocator@H@2@@std@@XZ"
EXPORT_FRIENDLY = "BEE_GetSupportedSaveAsPreviousVersions"

# How far into the function body to scan (the patch sites are typically
# within the first ~400 bytes, but we scan generously).
FUNC_SCAN_SIZE = 4096

# The AOB pattern we search for within the function:
#   C7 40 1C XX 00 00 00  =  mov dword [rax+0x1c], XX
# This is the instruction that stores a save-type constant into a
# std::set<int> node's value slot. There should be exactly 2 matches
# (the "newer" and "older" save-as-previous targets).
AOB_PREFIX = bytes([0xC7, 0x40, 0x1C])  # 3 bytes before the value
AOB_SUFFIX = bytes([0x00, 0x00, 0x00])  # 3 bytes after the value

# Sane range for save-type values (CS3=10 through ~30 for future versions)
SAVE_TYPE_MIN = 10
SAVE_TYPE_MAX = 30

# ---------------------------------------------------------------------------
# Save type -> AE public version mapping (from BEE_SaveTypeToAppVersNum)
#
# The "save type" is the internal enum stored in the set returned by
# GetSupportedSaveAsPreviousVersions. BEE_SaveTypeToAppVersNum converts it
# to the display version number shown in the menu:
#   save type 10     -> CS3 (8.x)      [special case]
#   save type 11     -> CS5 (10.x)     [special case]
#   save type 12     -> CS6 (11.x)     [special case]
#   save type 13-18  -> same (13.x-18.x) [identity range]
#   save type 19+    -> +3 (22.x+)     [offset: AE skipped versions 19-21]
# ---------------------------------------------------------------------------

VERSION_TO_SAVE_TYPE = {
    13: 13,  # CC 2014 (13.x)
    14: 14,  # CC 2017 (14.x)
    15: 15,  # CC 2018 (15.x)
    16: 16,  # CC 2019 (16.x)
    17: 17,  # 2020 (17.x)
    18: 18,  # 2021 (18.x)
    22: 19,  # 2022 (22.x)
    23: 20,  # 2023 (23.x)
    24: 21,  # 2024 (24.x)
    25: 22,  # 2025 (25.x)
    26: 23,  # 2026 (26.x)
    27: 24,  # 2027 (27.x) -- anticipated
    28: 25,  # 2028 (28.x) -- anticipated
}

SAVE_TYPE_TO_VERSION = {v: k for k, v in VERSION_TO_SAVE_TYPE.items()}

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
    27: "2027 (27.x)",
    28: "2028 (28.x)",
}

VALID_VERSIONS = sorted(VERSION_TO_SAVE_TYPE.keys())


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

def save_type_for_version(ae_public: int) -> int:
    if ae_public not in VERSION_TO_SAVE_TYPE:
        raise ValueError(f"No save type mapping for AE {ae_public}")
    return VERSION_TO_SAVE_TYPE[ae_public]


def version_for_save_type(st: int) -> int:
    if st in SAVE_TYPE_TO_VERSION:
        return SAVE_TYPE_TO_VERSION[st]
    # Extrapolate: save types >= 19 use +3 offset, below that are identity
    if st >= 19:
        return st + 3
    return st


def next_version(ae_ver: int) -> int:
    idx = VALID_VERSIONS.index(ae_ver)
    if idx + 1 < len(VALID_VERSIONS):
        return VALID_VERSIONS[idx + 1]
    raise ValueError(f"No version after AE {ae_ver}")


def version_display(ae_ver: int) -> str:
    return VERSION_NAMES.get(ae_ver, f"AE {ae_ver}")


# ---------------------------------------------------------------------------
# Dynamic patch-site discovery
# ---------------------------------------------------------------------------

def find_export_rva(pe: pefile.PE) -> int:
    """Find the RVA of GetSupportedSaveAsPreviousVersions via export table."""
    pe.parse_data_directories(
        directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]]
    )
    if not hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
        raise RuntimeError(
            f"BEE.dll has no export directory. Is this really AE's BEE.dll?"
        )

    # Try exact match first, then substring match
    for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
        if exp.name == EXPORT_NAME:
            return exp.address

    for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
        name = exp.name or b""
        if b"GetSupportedSaveAsPreviousVersions" in name:
            return exp.address

    raise RuntimeError(
        f"Export '{EXPORT_FRIENDLY}' not found in BEE.dll. "
        f"This DLL may be from an unsupported AE version."
    )


def find_patch_sites(data: bytes, pe: pefile.PE) -> tuple:
    """
    Locate the two save-type value bytes inside GetSupportedSaveAsPreviousVersions.

    Returns (offset_a, value_a, offset_b, value_b) where offset_a < offset_b.
    The values are the current save-type bytes at those locations.
    """
    rva = find_export_rva(pe)
    func_offset = pe.get_offset_from_rva(rva)

    if func_offset is None or func_offset == 0:
        raise RuntimeError(
            f"Could not convert RVA 0x{rva:X} to file offset. PE may be malformed."
        )

    # Read the function body
    end = min(func_offset + FUNC_SCAN_SIZE, len(data))
    body = data[func_offset:end]

    # Scan for: C7 40 1C XX 00 00 00
    matches = []
    for i in range(len(body) - 6):
        if (body[i:i+3] == AOB_PREFIX
                and body[i+4:i+7] == AOB_SUFFIX):
            val = body[i + 3]
            if SAVE_TYPE_MIN <= val <= SAVE_TYPE_MAX:
                abs_offset = func_offset + i + 3
                matches.append((abs_offset, val))

    if len(matches) != 2:
        raise RuntimeError(
            f"Expected exactly 2 AOB matches in {EXPORT_FRIENDLY}, found {len(matches)}. "
            f"Function at file offset 0x{func_offset:X} (RVA 0x{rva:X}). "
            f"Matches: {[(f'0x{o:X}', v) for o, v in matches]}. "
            f"This AE version may use a different instruction encoding."
        )

    # Sort by file offset (first match = newer, second = older in current builds,
    # but we identify them by value: higher save type = newer target)
    matches.sort(key=lambda m: m[1])
    older = matches[0]  # lower save type = older version
    newer = matches[1]  # higher save type = newer version

    return newer[0], newer[1], older[0], older[1]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    valid_vers_str = ", ".join(str(v) for v in VALID_VERSIONS)
    parser = argparse.ArgumentParser(
        description="Patch AE BEE.dll to extend 'Save a Copy As' version range",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Version mapping:
  AE 13 = CC 2014    AE 17 = 2020      AE 23 = 2023
  AE 14 = CC 2017    AE 18 = 2021      AE 24 = 2024
  AE 15 = CC 2018    AE 22 = 2022      AE 25 = 2025
  AE 16 = CC 2019    (19-21 skipped)    AE 26 = 2026

Valid --min-version values: {valid_vers_str}

The --min-version flag sets the OLDER of the two menu items.
The newer item will be the next AE version (handles 18->22 gap).

Example: --min-version 22 gives you "Save as AE 22" and "Save as AE 23"
Example: --min-version 18 gives you "Save as AE 18 (2021)" and "Save as AE 22 (2022)"
""",
    )
    parser.add_argument("input", help="Path to BEE.dll (any supported AE version)")
    parser.add_argument("output", help="Path for patched output BEE.dll")
    parser.add_argument(
        "--min-version",
        type=int,
        default=None,
        help=f"Minimum AE version for Save-As menu (valid: {valid_vers_str})",
    )
    args = parser.parse_args()

    # Read the DLL
    with open(args.input, "rb") as f:
        data = bytearray(f.read())

    # Parse PE and find patch sites dynamically
    print(f"Analyzing {args.input}...")
    try:
        pe = pefile.PE(data=bytes(data), fast_load=True)
    except pefile.PEFormatError as e:
        print(f"Error: Not a valid PE file: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        off_newer, val_newer, off_older, val_older = find_patch_sites(data, pe)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        pe.close()

    print(f"Found patch sites:")
    print(f"  Newer: file offset 0x{off_newer:X}, "
          f"save type {val_newer} = {version_display(version_for_save_type(val_newer))}")
    print(f"  Older: file offset 0x{off_older:X}, "
          f"save type {val_older} = {version_display(version_for_save_type(val_older))}")

    # Determine target versions
    if args.min_version is None:
        # No --min-version: show current state and exit without patching
        print(f"\nNo --min-version specified. Use --min-version N to patch.")
        print(f"Valid values: {valid_vers_str}")
        sys.exit(0)

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

    new_newer = save_type_for_version(max_ver)
    new_older = save_type_for_version(min_ver)

    print(f"\nPatching to:")
    print(f"  Menu item 1 (older): {version_display(min_ver)}")
    print(f"    save type {new_older}, file format byte 0x{new_older + 0x4A:02X}")
    print(f"  Menu item 2 (newer): {version_display(max_ver)}")
    print(f"    save type {new_newer}, file format byte 0x{new_newer + 0x4A:02X}")

    data[off_newer] = new_newer
    data[off_older] = new_older

    with open(args.output, "wb") as f:
        f.write(data)

    print(f"\nPatched successfully -> {args.output}")
    print(f"\nTo install:")
    print(f"  1. Close After Effects completely")
    print(f"  2. Replace BEE.dll in your AE Support Files directory with {args.output}")
    print(f"  3. Launch After Effects")
    print(f"  4. File -> Save a Copy As -> should show "
          f"{version_display(min_ver)} and {version_display(max_ver)}")


if __name__ == "__main__":
    main()
