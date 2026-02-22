# TWRP Auto Builder untuk Transsion Devices

Workflow otomatis untuk generate device tree dan build TWRP.

## Workflows

### 1. Generate Device Tree
- Buka tab **Actions** → pilih **Generate Device Tree**
- Klik **Run workflow**
- Input:
  - `vendor_boot_url`: URL download vendor_boot.img
  - `device_name`: (optional) nama device jika auto-detect gagal
- Result: Device tree otomatis di-commit ke repo

### 2. Build TWRP
- Buka tab **Actions** → pilih **Build TWRP**  
- Klik **Run workflow**
- Input:
  - `device_codename`: Codename device (misal: X6885)
  - `twrp_branch`: Branch TWRP (twrp-12.1/twrp-11/twrp-13)
- Result: recovery.img/vendor_boot.img tersedia di Artifacts dan Release

## Setup

1. Upload file `transsion_twrp_gen_vendor_boot.py` ke root repo
2. Upload workflows ke `.github/workflows/`
3. Enable GitHub Actions di repo settings
4. Run workflows sesuai kebutuhan

## Script Manual

```bash
python transsion_twrp_gen_vendor_boot.py vendor_boot.img -o output
```
