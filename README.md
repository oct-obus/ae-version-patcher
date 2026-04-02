# AE Version Patcher

Extend After Effects' **"Save a Copy As"** menu to support saving projects as older AE versions -- down to **CC 2014 (v13)**.

Works across AE versions by dynamically locating patch sites via PE export symbols and AOB scanning -- no hardcoded offsets.

## How It Works

After Effects normally only offers saving as the two immediately previous versions. This limitation is **purely artificial** -- AE's serialization engine already contains version-conditional logic going all the way back to CC 2014, inherited from every previous release.

This tool patches **exactly 2 bytes** in `BEE.dll` (AE's core engine DLL) to change which two versions appear in the "Save a Copy As" menu. AE then uses its own built-in conditional serializer to properly handle format differences for the target version.

### What happens under the hood

When AE saves as an older version, it walks the entire project tree and:
- **Skips streams** the target version doesn't understand (e.g., 3D geometry features added in AE 25)
- **Adjusts default values** for version-dependent parameters
- **Writes the correct file format header** for the target version

### How the patcher finds the right bytes

Instead of hardcoded file offsets (which break on every AE update), the patcher:
1. **Parses the PE export table** to find `BEE_GetSupportedSaveAsPreviousVersions` by symbol name
2. **Scans the function body** for the AOB pattern `C7 40 1C ?? 00 00 00` (`mov [rax+0x1c], imm32`)
3. **Validates** exactly 2 matches exist with values in a sane range (10-30)
4. **Identifies** the "newer" and "older" targets by their current values

This approach has been verified on both AE 24 and AE 26 BEE.dll files, which have completely different offsets but the same function structure.

## Requirements

- [uv](https://docs.astral.sh/uv/) (manages Python + dependencies automatically)

## Quick Start (PowerShell Installer)

The easiest way to use this on Windows is the interactive installer:

```powershell
.\install.ps1
```

On first run it automatically calls `uv sync` to install Python and dependencies. Then it provides an arrow-key driven menu for version selection, automatic backup, and admin elevation for the copy to Program Files.

## CLI Usage

```bash
uv sync
uv run python ae_version_patcher.py <input_BEE.dll> <output_BEE.dll> [--min-version N]
```

### Inspect current state (no patching)

```bash
uv run python ae_version_patcher.py BEE.dll /dev/null
```

### Examples

```bash
# Save as AE 22 (2022) / AE 23 (2023)
uv run python ae_version_patcher.py BEE.dll BEE_patched.dll --min-version 22

# Save as CC 2017 (v14) / CC 2018 (v15)
uv run python ae_version_patcher.py BEE.dll BEE_patched.dll --min-version 14

# Save as 2021 (v18) / AE 22 (2022)  -- handles the 18->22 gap
uv run python ae_version_patcher.py BEE.dll BEE_patched.dll --min-version 18
```

The `--min-version` flag sets the **older** of the two menu items. The newer item is the next AE version (handling the 18->22 version gap automatically).

### Version Reference

| `--min-version` | Menu shows |
|---|---|
| 13 | CC 2014 / CC 2017 |
| 14 | CC 2017 / CC 2018 |
| 15 | CC 2018 / CC 2019 |
| 18 | 2021 / 2022 |
| 22 | 2022 / 2023 |
| 24 | 2024 / 2025 (stock AE 26 behavior) |

## Manual Installation

1. **Close After Effects** completely
2. Find your AE install:
   ```
   C:\Program Files\Adobe\Adobe After Effects 2026\Support Files\
   ```
3. **Back up** the original `BEE.dll`:
   ```
   copy BEE.dll BEE.dll.bak
   ```
4. Run the patcher:
   ```bash
   uv sync
   uv run python ae_version_patcher.py BEE.dll BEE_patched.dll --min-version 22
   ```
5. Replace the original:
   ```
   copy BEE_patched.dll BEE.dll
   ```
6. Launch After Effects, then go to **File > Save a Copy As** -- it should show the new versions

### To revert

Replace `BEE.dll` with your backup (`BEE.dll.bak`), or use `install.ps1` option 3 (Restore).

## Important Notes

- **Always back up** your original `BEE.dll` before patching.
- **Adobe integrity checks** may detect the modification. If AE refuses to start, restore the backup.
- **Feature loss is expected.** Saving as an older version drops features that version doesn't support. This is the same behavior as AE's built-in "Save as Previous" -- it's a lossy conversion by design.
- **Extreme downgrades** (e.g., to CC 2014) cross multiple format boundary changes. While the serialization thresholds exist in the code, edge cases in deeply nested structures may not be perfectly handled. Test thoroughly with your specific projects.
- **The AE 23 format break** (officially announced by Adobe) added 4 bytes to `ldta` chunks. AE's serializer handles this via version thresholds, but projects with complex layer structures should be tested.

## Technical Details

<details>
<summary>Binary analysis methodology</summary>

The patch targets were identified by reverse-engineering `BEE.dll` from both AE 26 and AE 24:

### Patched function: `BEE_GetSupportedSaveAsPreviousVersions`

This function builds an `std::set<int>` of supported target save types. The patcher locates it via PE export name and scans for the two `mov dword [rax+0x1c], imm32` instructions that store the save-type constants.

Verified offsets across versions:
- **AE 26**: RVA 0x4FA650, patch sites at file offsets 0x4F9B1F and 0x4F9BC2
- **AE 24**: RVA 0x4B9610, patch sites at file offsets 0x4B8AD4 and 0x4B8B73

### Save type mapping

`BEE_SaveTypeToAppVersNum` (exported from BEE.dll) converts save types to display version numbers:

```
Save Type  ->  Display Version  ->  AE Name
   13          13                  CC 2014 (13.x)
   14          14                  CC 2017 (14.x)
   15          15                  CC 2018 (15.x)
   16          16                  CC 2019 (16.x)
   17          17                  2020 (17.x)
   18          18                  2021 (18.x)
   19          22                  2022 (22.x)   [+3 offset due to AE skipping 19-21]
   20          23                  2023 (23.x)
   21          24                  2024 (24.x)
   22          25                  2025 (25.x)
```

For AE >= 22: `save_type = public_version - 3`. For AE 13-18: `save_type = public_version`.
File format byte: `save_type + 0x4A` for all versions.

### Serialization threshold scan results (AE 26)

All `ShouldReadWriteForVersion` functions use `cmp eax, <composite>; setae al` (return true if version >= threshold):

| Composite | File Byte | Save Type | AE Version | Functions |
|-----------|-----------|-----------|-----------|-----------|
| 0x570005 | 0x57 | 13 | CC 2014.5 | 1 |
| 0x580002 | 0x58 | 14 | CC 2017.2 | 1 |
| 0x580007 | 0x58 | 14 | CC 2017.7 | 3 |
| 0x5C0003 | 0x5C | 18 | 2021.3 | 2 |
| 0x5C0007 | 0x5C | 18 | 2021.7 | 6 |
| 0x5D0002 | 0x5D | 19 | 22.2 | 2 |
| 0x5D000C | 0x5D | 19 | 22.12 | 2 |
| 0x5E0007 | 0x5E | 20 | 23.7 | 5 |
| 0x5F0000 | 0x5F | 21 | 24.0 | 1 |
| 0x600000 | 0x60 | 22 | 25.0 | 11 |
| 0x610002 | 0x61 | 23 | 26.2 | 4 |

</details>

## License

MIT
