# TWRP Auto Builder untuk Transsion Devices

Workflow otomatis untuk generate device tree dan build TWRP.

## Workflows

### 1. Generate Device Tree
- Buka tab **Actions** → pilih **Generate Device Tree**
- Klik **Run workflow**
- Input:
  - `vendor_boot_url`: URL download vendor_boot.img
  - `device_name`: (optional) nama device jika auto-detect gagal
  - `extract_first`: Centang untuk ekstrak vendor_boot dulu (recommended)
- Result: Device tree otomatis di-commit ke repo

### 2. Build TWRP
- Buka tab **Actions** → pilih **Build TWRP**  
- Klik **Run workflow**
- Input:
  - `device_codename`: Codename device (misal: X6885)
  - `twrp_branch`: Branch TWRP (twrp-12.1/twrp-11/twrp-13)
- Result: recovery.img/vendor_boot.img tersedia di Artifacts dan Release

## Setup

1. **WAJIB**: Upload `transsion_twrp_gen_vendor_boot.py` ke root repo GitHub
2. Create folder `.github/workflows/` di repo
3. Upload kedua file workflow ke `.github/workflows/`
4. Enable GitHub Actions di repo settings (Settings → Actions → Allow all actions)
5. Run workflows via tab Actions

**Struktur repo:**
```
your-repo/
├── transsion_twrp_gen_vendor_boot.py  ← WAJIB
└── .github/
    └── workflows/
        ├── generate-tree.yml
        └── build-twrp.yml
```

## Script Manual

```bash
python transsion_twrp_gen_vendor_boot.py vendor_boot.img -o output
```

## Troubleshooting

**Error: can't open file transsion_twrp_gen_vendor_boot.py**
- Script belum diupload ke repo
- Upload `transsion_twrp_gen_vendor_boot.py` ke root repo (bukan di folder)

**Error: No recovery ramdisk found**
- vendor_boot.img perlu diekstrak dulu sebelum diproses
- Centang opsi `Extract vendor_boot dulu dengan magiskboot` saat run workflow (default: true)
- Atau ekstrak manual dulu dengan magiskboot/Android Image Kitchen lalu upload folder hasil ekstrak

**Build gagal**
- Pastikan device tree sudah di-generate terlebih dahulu
- Cek codename device sudah benar
- TWRP branch sesuai Android version device