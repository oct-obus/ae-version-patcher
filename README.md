# AE Version Patcher

Extend Adobe After Effects 26's **"Save a Copy As"** menu to support saving projects as older AE versions -- down to **CC 2014 (v13)**.

## How It Works

After Effects 26 normally only offers saving as AE 24 or AE 25. This limitation is **purely artificial** -- AE 26's serialization engine already contains version-conditional logic going all the way back to CC 2014, inherited from every previous release.

This tool patches **exactly 2 bytes** in `BEE.dll` (AE's core engine DLL) to change which two versions appear in the "Save a Copy As" menu. AE then uses its own built-in conditional serializer to properly handle format differences for the target version.

### What happens under the hood

When AE saves as an older version, it walks the entire project tree and:
- **Skips streams** the target version doesn't understand (e.g., 3D geometry features added in AE 25)
- **Adjusts default values** for version-dependent parameters
- **Writes the correct file format header** for the target version

AE 26's binary contains **56 version-conditional serialization checkpoints** spanning from CC 2014 to AE 26.2, all using `>=` threshold comparisons. This means asking AE 26 to save as CC 2014 triggers every single conditional skip, producing a properly formatted output.

## Usage

```bash
python ae_version_patcher.py <input_BEE.dll> <output_BEE.dll> [--min-version N]
```

### Examples

```bash
# Save as AE 22 (2022) / AE 23 (2023)
python ae_version_patcher.py BEE.dll BEE_patched.dll --min-version 22

# Save as CC 2017 (v14) / CC 2018 (v15)
python ae_version_patcher.py BEE.dll BEE_patched.dll --min-version 14

# Save as 2021 (v18) / AE 22 (2022)  -- handles the 18->22 gap
python ae_version_patcher.py BEE.dll BEE_patched.dll --min-version 18
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
| 24 | 2024 / 2025 (stock behavior) |

## Installation

1. **Close After Effects** completely
2. Find your AE 26 install:
   ```
   C:\Program Files\Adobe\Adobe After Effects 2026\Support Files\
   ```
3. **Back up** the original `BEE.dll`:
   ```
   copy BEE.dll BEE.dll.bak
   ```
4. Run the patcher:
   ```bash
   python ae_version_patcher.py BEE.dll BEE_patched.dll --min-version 22
   ```
5. Replace the original:
   ```
   copy BEE_patched.dll BEE.dll
   ```
6. Launch After Effects, then go to **File > Save a Copy As** -- it should show the new versions

### To revert

Replace `BEE.dll` with your backup (`BEE.dll.bak`), or re-run the patcher with `--min-version 24` (stock behavior).

## Important Notes

- **AE 26 only.** The patch offsets (`0x4F9B1F` and `0x4F9BC2`) are specific to AE 26's `BEE.dll`. Other AE versions have different offsets.
- **Always back up** your original `BEE.dll` before patching.
- **Adobe integrity checks** may detect the modification. If AE refuses to start, restore the backup.
- **Feature loss is expected.** Saving as an older version drops features that version doesn't support (new 3D geometry streams, material projections, etc.). This is the same behavior as AE's built-in "Save as Previous" -- it's a lossy conversion by design.
- **Extreme downgrades** (e.g., AE 26 to CC 2014) cross multiple format boundary changes. While the serialization thresholds exist in the code, edge cases in deeply nested structures may not be perfectly handled. Test thoroughly with your specific projects.
- **The AE 23 format break** (officially announced by Adobe) added 4 bytes to `ldta` chunks. AE 26's serializer should handle this since the threshold exists in the binary, but projects with complex layer structures should be tested.

## Technical Details

<details>
<summary>Binary analysis methodology</summary>

The patch targets were identified by reverse-engineering `BEE.dll` from both AE 26 and AE 24:

### Patched function: `BEE_GetSupportedSaveAsPreviousVersions`

This function builds an `std::set<int>` of supported target save types. Stock AE 26 inserts two values:
- `0x16` (save type 22 -> AE 25) at file offset `0x4F9B1F`
- `0x15` (save type 21 -> AE 24) at file offset `0x4F9BC2`

The instruction is `mov dword [rax+0x1c], <value>` -- we change only the immediate operand byte.

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

### Serialization threshold scan results

All `ShouldReadWriteForVersion` functions use `cmp eax, <composite>; setae al` (return true if version >= threshold):

| Composite | File Byte | Save Type | AE Version | Functions in AE 26 |
|-----------|-----------|-----------|-----------|-------------------|
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

41 of 56 checkpoints are inherited identically from AE 24's `BEE.dll`.

</details>

## License

MIT
