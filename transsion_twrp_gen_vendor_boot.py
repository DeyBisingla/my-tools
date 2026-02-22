#!/usr/bin/env python3
"""
Transsion TWRP Vendor Boot Device Tree Generator (Copyright 2026 @andrei0514)
Selling this generator is extremely prohibited as it is Open Source Software (OSS)
---------------------------------------------------------
Generates a complete TWRP device tree from an unpacked vendor_boot directory
for devices with vendor_boot as recovery partition.

Features:
- Cool ASCII art banner with owner credit (@andrei0514)
- Correctly sets TARGET_BOOTLOADER_BOARD_NAME to codename (e.g., X6885)
- Correctly sets TARGET_BOARD_PLATFORM to mtXXXX
- Copies all necessary files to recovery/root (first_stage_ramdisk, lib/modules, system/etc)
- Automatically patches adaptive-ts.ko if found (touch fix)
- Generates all required makefiles (Android.mk, AndroidProducts.mk, BoardConfig.mk, device.mk, twrp_*.mk)
- Includes --device-name override if auto‑detection fails

Usage:
    python transsion_twrp_gen_vendor_boot.py /path/to/unpacked_vendor_boot [-o OUTPUT] [--device-name NAME]
"""

import os
import sys
import argparse
import struct
import shutil
import tempfile
import re
import logging
import stat
import time
from pathlib import Path

# -------------------- Banner --------------------
BANNER = r"""
████████╗██╗    ██╗██████╗ ██████╗
╚══██╔══╝██║    ██║██╔══██╗██╔══██╗
   ██║   ██║ █╗ ██║██████╔╝██████╔╝
   ██║   ██║███╗██║██╔══██╗██╔═══╝
   ██║   ╚███╔███╔╝██║  ██║██║
   ╚═╝    ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝

                 TEAM WIN RECOVERY PROJECT
       Transsion TWRP Vendor Boot Device Tree Generator
                    by: @andrei0514
"""

def print_banner():
    try:
        if sys.stdout.isatty():
            print("\033[1;31m" + BANNER + "\033[0m")
        else:
            print(BANNER)
    except:
        print(BANNER)

# -------------------- Logging Setup --------------------
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('transsion_twrp_vendor_boot_gen')

# -------------------- Constants --------------------
BOOT_MAGIC_VENDOR_BOOT = b"VNDRBOOT"
VENDOR_RAMDISK_CMDLINE_SIZE = 2048
BOOT_PRODUCT_NAME_SIZE = 64
VENDOR_RAMDISK_NAME_SIZE = 32
BOOT_ID_SIZE = 32

VENDOR_RAMDISK_TYPE_NONE = 0
VENDOR_RAMDISK_TYPE_PLATFORM = 1
VENDOR_RAMDISK_TYPE_RECOVERY = 2
VENDOR_RAMDISK_TYPE_DLKM = 3

# -------------------- Vendor Boot Parser --------------------
class VendorBootHeader:
    # ... (same as PBRP version) ...
    def __init__(self, data):
        self.data = data
        offset = 0
        self.magic = data[offset:offset+8]
        if self.magic != BOOT_MAGIC_VENDOR_BOOT:
            raise ValueError(f"Invalid magic: expected {BOOT_MAGIC_VENDOR_BOOT}, got {self.magic}")
        offset += 8
        self.header_version = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        self.page_size = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        self.kernel_load_addr = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        self.ramdisk_load_addr = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        self.vendor_ramdisk_total_size = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        cmdline_bytes = data[offset:offset+VENDOR_RAMDISK_CMDLINE_SIZE]
        self.cmdline = cmdline_bytes.rstrip(b'\x00').decode('utf-8', errors='ignore')
        offset += VENDOR_RAMDISK_CMDLINE_SIZE
        self.tags_load_addr = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        product_name_bytes = data[offset:offset+BOOT_PRODUCT_NAME_SIZE]
        self.product_name = product_name_bytes.rstrip(b'\x00').decode('utf-8', errors='ignore')
        offset += BOOT_PRODUCT_NAME_SIZE
        self.header_size = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        self.dtb_size = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        self.dtb_load_addr = struct.unpack('<Q', data[offset:offset+8])[0]
        offset += 8
        self.vendor_ramdisk_table_size = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        self.vendor_ramdisk_table_num_entries = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        self.vendor_bootconfig_size = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        self.table_offset = offset

    def get_ramdisk_entries(self):
        if self.vendor_ramdisk_table_num_entries == 0:
            return []
        entries = []
        entry_size = 4 + 4 + 4 + VENDOR_RAMDISK_NAME_SIZE + BOOT_ID_SIZE
        for i in range(self.vendor_ramdisk_table_num_entries):
            start = self.table_offset + i * entry_size
            end = start + entry_size
            entry_data = self.data[start:end]
            if len(entry_data) < entry_size:
                raise ValueError(f"Truncated ramdisk table at entry {i}")
            entry = VendorRamdiskEntry(entry_data)
            entries.append(entry)
        return entries

    def get_dtb_offset(self):
        ramdisk_start = self.header_size + self.vendor_bootconfig_size
        dtb_offset = ramdisk_start + self.vendor_ramdisk_total_size
        if dtb_offset + self.dtb_size > len(self.data):
            dtb_offset = len(self.data) - self.dtb_size
        return dtb_offset

class VendorRamdiskEntry:
    # ... (same) ...
    def __init__(self, data):
        self.size = struct.unpack('<I', data[0:4])[0]
        self.offset = struct.unpack('<I', data[4:8])[0]
        self.type = struct.unpack('<I', data[8:12])[0]
        name_bytes = data[12:12+VENDOR_RAMDISK_NAME_SIZE]
        self.name = name_bytes.rstrip(b'\x00').decode('utf-8', errors='ignore')
        board_id_bytes = data[12+VENDOR_RAMDISK_NAME_SIZE:12+VENDOR_RAMDISK_NAME_SIZE+BOOT_ID_SIZE]
        self.board_id = board_id_bytes.hex()
        self.type_str = {
            VENDOR_RAMDISK_TYPE_NONE: "none",
            VENDOR_RAMDISK_TYPE_PLATFORM: "platform",
            VENDOR_RAMDISK_TYPE_RECOVERY: "recovery",
            VENDOR_RAMDISK_TYPE_DLKM: "dlkm"
        }.get(self.type, f"unknown({self.type})")

def parse_vendor_boot(img_path):
    with open(img_path, 'rb') as f:
        data = f.read()
    log.info(f"Read {len(data)} bytes from {img_path}")
    header = VendorBootHeader(data)
    entries = header.get_ramdisk_entries()
    log.info(f"Found {len(entries)} vendor ramdisk entries")
    return {'header': header, 'entries': entries, 'data': data}

# -------------------- Directory‑based extraction --------------------
def load_from_directory(dir_path):
    # ... (same as PBRP) ...
    log.info(f"Loading unpacked vendor_boot from directory: {dir_path}")
    ramdisk_dir = os.path.join(dir_path, 'ramdisk')
    ramdisk_cpio = os.path.join(dir_path, 'ramdisk.cpio')
    if os.path.isdir(ramdisk_dir):
        ramdisk_source = ramdisk_dir
        ramdisk_is_cpio = False
        log.info("Found existing 'ramdisk/' directory – using it directly.")
    elif os.path.isfile(ramdisk_cpio):
        ramdisk_source = ramdisk_cpio
        ramdisk_is_cpio = True
        log.info("Found 'ramdisk.cpio' – will extract it.")
    else:
        raise FileNotFoundError("No ramdisk/ directory or ramdisk.cpio found")

    dtb_path = os.path.join(dir_path, 'dtb')
    if not os.path.isfile(dtb_path):
        dtb_path = None
        log.warning("dtb file not found")

    header_path = os.path.join(dir_path, 'header')
    header_props = {}
    if os.path.isfile(header_path):
        with open(header_path, 'r') as f:
            for line in f:
                line = line.strip()
                if '=' in line:
                    k, v = line.split('=', 1)
                    header_props[k.strip()] = v.strip()
        log.info(f"Loaded header with {len(header_props)} properties")

    class MockHeader:
        def __init__(self, props):
            self.header_version = 4
            self.dtb_size = os.path.getsize(dtb_path) if dtb_path else 0
            self.vendor_ramdisk_total_size = 0
            self.vendor_bootconfig_size = 0
            self.header_size = 0
            self.cmdline = props.get('cmdline', '')
            self.product_name = props.get('product_name', '')
    mock_header = MockHeader(header_props)

    return {
        'header': mock_header,
        'entries': [],
        'data': None,
        'from_directory': True,
        'ramdisk_source': ramdisk_source,
        'ramdisk_is_cpio': ramdisk_is_cpio,
        'dtb_path': dtb_path,
        'header_props': header_props,
    }

# -------------------- CPIO extraction --------------------
def extract_cpio(cpio_file, dest_dir):
    # ... (same) ...
    if not os.path.isfile(cpio_file):
        raise FileNotFoundError(f"cpio file not found: {cpio_file}")
    os.makedirs(dest_dir, exist_ok=True)
    with open(cpio_file, 'rb') as f:
        magic = f.read(2)
        f.seek(0)
        if magic == b'\x1f\x8b':
            import gzip
            log.info("cpio file is gzipped, decompressing...")
            try:
                with gzip.open(cpio_file, 'rb') as gz:
                    data = gz.read()
                temp_cpio = os.path.join(os.path.dirname(dest_dir), 'ramdisk_decompressed.cpio')
                with open(temp_cpio, 'wb') as tf:
                    tf.write(data)
                cpio_file = temp_cpio
            except Exception as e:
                log.error(f"Failed to decompress gzip: {e}")
                return

    with open(cpio_file, 'rb') as f:
        while True:
            header = f.read(110)
            if len(header) < 110:
                break
            magic = header[0:6].decode('ascii')
            if magic not in ('070701', '070702'):
                log.warning(f"Unexpected cpio magic {magic} at offset {f.tell()-110}. Stopping extraction.")
                break
            inode = int(header[6:14], 16)
            mode = int(header[14:22], 16)
            uid = int(header[22:30], 16)
            gid = int(header[30:38], 16)
            nlink = int(header[38:46], 16)
            mtime = int(header[46:54], 16)
            filesize = int(header[54:62], 16)
            devmajor = int(header[62:70], 16)
            devminor = int(header[70:78], 16)
            rdevmajor = int(header[78:86], 16)
            rdevminor = int(header[86:94], 16)
            namesize = int(header[94:102], 16)
            check = int(header[102:110], 16)

            filename_raw = f.read(namesize)
            padding = (4 - (namesize % 4)) % 4
            f.read(padding)
            filename = filename_raw.rstrip(b'\x00').decode('utf-8', errors='ignore')

            if filename == 'TRAILER!!!':
                break

            full_path = os.path.join(dest_dir, filename.lstrip('/'))
            if filesize == 0:
                if stat.S_ISDIR(mode):
                    os.makedirs(full_path, exist_ok=True)
                elif stat.S_ISLNK(mode):
                    link_target = f.read(filesize).decode('utf-8', errors='ignore')
                    os.symlink(link_target, full_path)
                    pad = (4 - (filesize % 4)) % 4
                    f.read(pad)
                else:
                    os.makedirs(os.path.dirname(full_path), exist_ok=True)
                    open(full_path, 'wb').close()
            else:
                data = f.read(filesize)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'wb') as out_f:
                    out_f.write(data)
                pad = (4 - (filesize % 4)) % 4
                f.read(pad)
    log.info(f"Extracted cpio to {dest_dir}")

# -------------------- Touch Patch Function --------------------
def patch_adaptive_ts(module_path):
    # ... (same) ...
    if not os.path.isfile(module_path):
        log.warning(f"adaptive-ts.ko not found at {module_path}, skipping patch.")
        return

    with open(module_path, 'rb') as f:
        data = f.read()

    pattern1_old = b'\x60\x00\x00\x54\x40\x00'
    pattern1_new = b'\x60\x00\x00\x54\x00\x00'
    pattern2_old = b'\x20\x00\x80\x52'
    pattern2_new = b'\x00\x00\x80\x52'

    modified = False
    new_data = data

    last_pos = new_data.rfind(pattern1_old)
    if last_pos != -1:
        log.info(f"Found pattern1 at offset 0x{last_pos:x}, replacing.")
        new_data = new_data[:last_pos] + pattern1_new + new_data[last_pos+len(pattern1_old):]
        modified = True
        search_start = last_pos + len(pattern1_new)
    else:
        log.warning("Pattern1 (600000544000) not found, skipping first patch.")
        search_start = 0

    if search_start < len(new_data):
        pos2 = new_data.find(pattern2_old, search_start)
        if pos2 != -1:
            log.info(f"Found pattern2 at offset 0x{pos2:x} after patch location, replacing.")
            new_data = new_data[:pos2] + pattern2_new + new_data[pos2+len(pattern2_old):]
            modified = True
        else:
            log.warning("Pattern2 (20008052) not found after patch location, skipping second patch.")
    else:
        log.warning("Search start beyond file end, skipping second patch.")

    if modified:
        with open(module_path, 'wb') as f:
            f.write(new_data)
        log.info("adaptive-ts.ko patched successfully.")
    else:
        log.info("No modifications applied to adaptive-ts.ko.")

# -------------------- Utility Functions --------------------
def human_readable_size(size_bytes):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"

def parse_prop_file(prop_path):
    props = {}
    if not os.path.isfile(prop_path):
        return props
    with open(prop_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, value = line.split('=', 1)
                props[key.strip()] = value.strip()
    return props

def collect_all_props(base_dir):
    all_props = {}
    if not os.path.isdir(base_dir):
        return all_props
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file.endswith('.prop') or file in ('build.prop', 'default.prop'):
                path = os.path.join(root, file)
                props = parse_prop_file(path)
                all_props.update(props)
    log.info(f"Collected {len(all_props)} properties from ramdisk")
    return all_props

def detect_platform(board_name):
    if board_name and board_name.startswith('mt'):
        return 'mtk'
    elif board_name and (board_name.startswith('sdm') or board_name.startswith('sm')):
        return 'qcom'
    return 'unknown'

# -------------------- DeviceInfo Collector --------------------
class DeviceInfo:
    # ... (same as PBRP) ...
    def __init__(self, work_dir, parse_result, manual_device_name=None):
        self.work_dir = work_dir
        self.parse_result = parse_result
        self.manual_device_name = manual_device_name
        self.from_directory = parse_result.get('from_directory', False)

        if not self.from_directory:
            self.header = parse_result['header']
            self.entries = parse_result['entries']
            self.data = parse_result['data']
        else:
            self.header = parse_result['header']
            self.entries = []
            self.data = None
            self.ramdisk_source = parse_result['ramdisk_source']
            self.ramdisk_is_cpio = parse_result['ramdisk_is_cpio']
            self.dtb_path_input = parse_result['dtb_path']
            self.header_props = parse_result['header_props']

        self.recovery_ramdisk_dir = None
        self.dtb_path = None
        self.props = {}
        self.board_name = None
        self.device_name = None          # e.g., Infinix-X6885
        self.codename = None             # e.g., X6885 (without brand)
        self.manufacturer = None         # e.g., Infinix
        self.platform = None
        self.arch = None
        self.sdk_version = None
        self.kernel_version = None
        self.dynamic_partitions = False
        self.super_partition_size = None
        self.boot_header_version = self.header.header_version if not self.from_directory else 4
        self.kernel_path = None
        self.fstab_content = None
        self.init_files_source = None
        self.modules_source = None
        self.system_etc_source = None

    def extract_all(self):
        if self.from_directory:
            self._extract_from_directory()
        else:
            self._extract_from_image()

    def _extract_from_image(self):
        if self.header.dtb_size > 0:
            dtb_offset = self.header.get_dtb_offset()
            dtb_data = self.data[dtb_offset:dtb_offset+self.header.dtb_size]
            dtb_path = os.path.join(self.work_dir, 'dtb.img')
            with open(dtb_path, 'wb') as f:
                f.write(dtb_data)
            self.dtb_path = dtb_path
            log.info(f"DTB extracted")

        for entry in self.entries:
            cpio_file = os.path.join(self.work_dir, f"ramdisk_{entry.name or entry.type_str}.cpio")
            with open(cpio_file, 'wb') as f:
                f.write(self.data[entry.offset:entry.offset+entry.size])
            dest_dir = os.path.join(self.work_dir, f"ramdisk_{entry.name or entry.type_str}")
            extract_cpio(cpio_file, dest_dir)
            if entry.type == VENDOR_RAMDISK_TYPE_RECOVERY:
                self.recovery_ramdisk_dir = dest_dir
            else:
                pass
        if not self.recovery_ramdisk_dir and self.entries:
            self.recovery_ramdisk_dir = os.path.join(self.work_dir, f"ramdisk_{self.entries[0].name or self.entries[0].type_str}")
            log.warning("No dedicated recovery ramdisk, using first ramdisk.")
        if not self.recovery_ramdisk_dir:
            raise RuntimeError("No recovery ramdisk found.")
        self._post_extract()

    def _extract_from_directory(self):
        log.info("Extracting from unpacked directory...")
        if self.ramdisk_is_cpio:
            dest_dir = os.path.join(self.work_dir, 'recovery_ramdisk')
            extract_cpio(self.ramdisk_source, dest_dir)
            self.recovery_ramdisk_dir = dest_dir
        else:
            dest_dir = os.path.join(self.work_dir, 'recovery_ramdisk')
            shutil.copytree(self.ramdisk_source, dest_dir, symlinks=True)
            self.recovery_ramdisk_dir = dest_dir
        if self.dtb_path_input:
            dtb_dest = os.path.join(self.work_dir, 'dtb.img')
            shutil.copy2(self.dtb_path_input, dtb_dest)
            self.dtb_path = dtb_dest
        self._post_extract()

    def _post_extract(self):
        self.props = collect_all_props(self.recovery_ramdisk_dir)

        if self.manual_device_name:
            self.device_name = self.manual_device_name
            log.info(f"Using manually specified device name: {self.device_name}")
        else:
            self.device_name = (self.props.get('ro.product.system.device') or
                                self.props.get('ro.product.vendor.device') or
                                self.props.get('ro.product.device') or
                                self.props.get('ro.build.product') or
                                self.header_props.get('device') or
                                None)
        if not self.device_name:
            log.error("Device name could not be determined. Please provide it with --device-name (e.g., Infinix-X6885).")
            sys.exit(1)

        if self.device_name and '-' in self.device_name:
            self.codename = self.device_name.split('-', 1)[1]
        else:
            self.codename = self.device_name

        self.board_name = (self.props.get('ro.board.platform') or
                           self.props.get('ro.product.board') or
                           self.header_props.get('board') or
                           'mt6789')
        self.manufacturer = (self.props.get('ro.product.manufacturer') or
                             self.props.get('ro.product.vendor.manufacturer') or
                             (self.device_name.split('-')[0] if self.device_name and '-' in self.device_name else 'transsion')).lower()
        self.platform = detect_platform(self.board_name)
        self.arch = self.props.get('ro.product.cpu.abi', 'arm64-v8a')
        sdk_str = self.props.get('ro.build.version.sdk')
        self.sdk_version = int(sdk_str) if sdk_str and sdk_str.isdigit() else None
        self.kernel_version = self.props.get('ro.kernel.version')
        if not self.kernel_version:
            proc_version = os.path.join(self.recovery_ramdisk_dir, 'proc', 'version')
            if os.path.isfile(proc_version):
                with open(proc_version, 'r') as f:
                    line = f.read().strip()
                    match = re.search(r'Linux version (\S+)', line)
                    if match:
                        self.kernel_version = match.group(1)

        kernel_candidates = [
            os.path.join(self.recovery_ramdisk_dir, 'kernel'),
            os.path.join(self.recovery_ramdisk_dir, 'prebuilt', 'kernel'),
        ]
        for k in kernel_candidates:
            if os.path.isfile(k):
                self.kernel_path = k
                break

        for root, dirs, files in os.walk(self.recovery_ramdisk_dir):
            if 'recovery.fstab' in files:
                fstab_path = os.path.join(root, 'recovery.fstab')
                with open(fstab_path, 'r') as f:
                    self.fstab_content = f.read()
                break

        init_dest = os.path.join(self.work_dir, 'recovery_root_src')
        os.makedirs(init_dest, exist_ok=True)
        for root, dirs, files in os.walk(self.recovery_ramdisk_dir):
            for file in files:
                if file.endswith('.rc') or file in ('ueventd.rc', 'init.recovery.*'):
                    src = os.path.join(root, file)
                    rel = os.path.relpath(src, self.recovery_ramdisk_dir)
                    dst = os.path.join(init_dest, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
        if os.listdir(init_dest):
            self.init_files_source = init_dest
            log.info(f"Collected {len(os.listdir(init_dest))} init files")

        modules_src = os.path.join(self.recovery_ramdisk_dir, 'lib', 'modules')
        if os.path.isdir(modules_src):
            modules_dest = os.path.join(self.work_dir, 'modules_src')
            shutil.copytree(modules_src, modules_dest, symlinks=True)
            self.modules_source = modules_dest
            log.info(f"Collected modules from lib/modules")

        system_etc_src = os.path.join(self.recovery_ramdisk_dir, 'system', 'etc')
        if os.path.isdir(system_etc_src):
            etc_dest_base = os.path.join(self.work_dir, 'system_etc_src')
            os.makedirs(etc_dest_base, exist_ok=True)
            wanted = ['vintf', 'cgroups.json', 'twrp.flags', 'ueventd.rc']
            for item in os.listdir(system_etc_src):
                src_path = os.path.join(system_etc_src, item)
                if os.path.isdir(src_path) and item == 'vintf':
                    shutil.copytree(src_path, os.path.join(etc_dest_base, 'vintf'), symlinks=True, dirs_exist_ok=True)
                    log.info("Copied vintf/")
                elif item in wanted and os.path.isfile(src_path):
                    shutil.copy2(src_path, os.path.join(etc_dest_base, item))
                    log.info(f"Copied {item}")
            if os.listdir(etc_dest_base):
                self.system_etc_source = etc_dest_base
                log.info(f"Collected system/etc files: {os.listdir(etc_dest_base)}")

        if self.fstab_content and 'super' in self.fstab_content:
            self.dynamic_partitions = True
        if self.props.get('ro.boot.dynamic_partitions') == 'true':
            self.dynamic_partitions = True
        if self.dynamic_partitions:
            self.super_partition_size = 8 * 1024 * 1024 * 1024

    def log_summary(self):
        log.info("="*50)
        log.info("COLLECTED DEVICE INFORMATION")
        log.info("="*50)
        log.info(f"Device name          : {self.device_name}")
        log.info(f"Codename             : {self.codename}")
        log.info(f"Board name           : {self.board_name}")
        log.info(f"Manufacturer         : {self.manufacturer}")
        log.info(f"Platform             : {self.platform}")
        log.info(f"Architecture         : {self.arch}")
        log.info(f"Android SDK version  : {self.sdk_version}")
        log.info(f"Kernel version       : {self.kernel_version}")
        log.info(f"Boot header version  : {self.boot_header_version}")
        log.info(f"Dynamic partitions   : {self.dynamic_partitions}")
        log.info(f"Kernel found         : {self.kernel_path is not None}")
        log.info(f"Stock fstab found    : {self.fstab_content is not None}")
        log.info(f"Init files collected : {self.init_files_source is not None}")
        log.info(f"Modules collected    : {self.modules_source is not None}")
        log.info(f"System/etc collected : {self.system_etc_source is not None}")
        log.info("="*50)

# -------------------- Device Tree Generator (TWRP) --------------------
class DeviceTreeGenerator:
    def __init__(self, info, output_dir):
        self.info = info
        self.output_dir = output_dir
        self.device_path = None

    def generate(self):
        if not self.info.device_name:
            log.error("Device name not found. Cannot generate tree.")
            sys.exit(1)

        self.device_path = os.path.join(self.output_dir, self.info.manufacturer, self.info.codename)
        os.makedirs(self.device_path, exist_ok=True)

        subdirs = ['bootctrl', 'init', 'mtk_plpath_utils', 'prebuilt', 'recovery/root']
        for d in subdirs:
            os.makedirs(os.path.join(self.device_path, d), exist_ok=True)

        if self.info.kernel_path:
            shutil.copy2(self.info.kernel_path, os.path.join(self.device_path, 'prebuilt', 'kernel'))
            log.info("Kernel copied to prebuilt/")
        if self.info.dtb_path:
            shutil.copy2(self.info.dtb_path, os.path.join(self.device_path, 'prebuilt', 'dtb.img'))
            log.info("DTB copied to prebuilt/dtb.img")

        self._generate_init_files()
        self._populate_recovery_root()
        self._generate_android_mk()
        self._generate_android_products_mk()
        self._generate_boardconfig_mk()
        self._generate_device_mk()
        self._generate_product_mk()
        self._generate_system_prop()
        self._generate_vendorsetup()
        self._generate_readme()

        log.info(f"Device tree generated at: {self.device_path}")

    def _generate_init_files(self):
        cpp_filename = f"init_{self.info.device_name}.cpp"
        bp_path = os.path.join(self.device_path, 'init', 'Android.bp')
        cpp_path = os.path.join(self.device_path, 'init', cpp_filename)

        with open(bp_path, 'w') as f:
            f.write(f"""cc_library_static {{
    name: "libinit_{self.info.device_name}",
    header_libs: [
        "libbase_headers",
    ],
    cflags: [
        "-Wall",
    ],
    static_libs: [
        "libbase",
    ],
    srcs: ["{cpp_filename}"],
    recovery_available: true,
}}
""")

        with open(cpp_path, 'w') as f:
            f.write(f"""#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <android-base/properties.h>

#define _REALLY_INCLUDE_SYS__SYSTEM_PROPERTIES_H_
#include <sys/_system_properties.h>

using android::base::GetProperty;
using std::string;

void property_override(string prop, string value)
{{
    auto pi = (prop_info *)__system_property_find(prop.c_str());

    if (pi != nullptr)
        __system_property_update(pi, value.c_str(), value.size());
    else
        __system_property_add(prop.c_str(), prop.size(), value.c_str(), value.size());
}}

void vendor_load_properties()
{{
    string prop_partitions[] = {{"", "vendor.", "odm."}};
    for (const string &prop : prop_partitions)
    {{
        property_override(string("ro.product.") + prop + string("brand"), "{self.info.manufacturer.capitalize()}");
        property_override(string("ro.product.") + prop + string("name"), "{self.info.codename}-OP");
        property_override(string("ro.product.") + prop + string("device"), "{self.info.device_name}");
        property_override(string("ro.product.") + prop + string("model"), "{self.info.manufacturer.capitalize()} {self.info.codename}");
        property_override(string("ro.product.") + prop + string("marketname"), "{self.info.manufacturer.capitalize()} HOT 60 PRO");
        property_override(string("ro.product.system.") + prop + string("device"), "{self.info.manufacturer.capitalize()} HOT 60 PRO");
    }}
}}
""")
        log.info("Generated init files")

    def _populate_recovery_root(self):
        recovery_root = os.path.join(self.device_path, 'recovery', 'root')

        if self.info.init_files_source:
            shutil.copytree(self.info.init_files_source, recovery_root, dirs_exist_ok=True, symlinks=True)
            log.info("Copied init files to recovery/root")

        fs_ramdisk = os.path.join(recovery_root, 'first_stage_ramdisk')
        os.makedirs(fs_ramdisk, exist_ok=True)
        for root, dirs, files in os.walk(self.info.recovery_ramdisk_dir):
            for f in files:
                if f.startswith('fstab.') or f == 'fstab':
                    src = os.path.join(root, f)
                    rel = os.path.relpath(src, self.info.recovery_ramdisk_dir)
                    if rel.startswith('first_stage_ramdisk/'):
                        dst = os.path.join(fs_ramdisk, os.path.basename(rel))
                    else:
                        dst = os.path.join(fs_ramdisk, f)
                    shutil.copy2(src, dst)
                    log.info(f"Copied fstab file {f} to first_stage_ramdisk/")
        if not os.listdir(fs_ramdisk):
            default_fstab = os.path.join(fs_ramdisk, f'fstab.{self.info.board_name}')
            with open(default_fstab, 'w') as f:
                f.write("# Default fstab for {}\n".format(self.info.board_name))
                f.write("# Add your mount points here\n")
            log.warning(f"No fstab found; created placeholder {default_fstab}")

        if self.info.modules_source:
            modules_dest = os.path.join(recovery_root, 'lib', 'modules')
            shutil.copytree(self.info.modules_source, modules_dest, dirs_exist_ok=True, symlinks=True)
            log.info("Copied modules to recovery/root/lib/modules")
            adaptive_ts_path = os.path.join(modules_dest, 'adaptive-ts.ko')
            patch_adaptive_ts(adaptive_ts_path)

        if self.info.system_etc_source:
            etc_dest = os.path.join(recovery_root, 'system', 'etc')
            shutil.copytree(self.info.system_etc_source, etc_dest, dirs_exist_ok=True, symlinks=True)
            log.info("Copied system/etc files")

        if self.info.fstab_content:
            etc_dest = os.path.join(recovery_root, 'system', 'etc')
            os.makedirs(etc_dest, exist_ok=True)
            fstab_dest = os.path.join(etc_dest, 'recovery.fstab')
            with open(fstab_dest, 'w') as f:
                f.write(self.info.fstab_content)
            log.info("Placed recovery.fstab in recovery/root/system/etc/")
        else:
            etc_dest = os.path.join(recovery_root, 'system', 'etc')
            os.makedirs(etc_dest, exist_ok=True)
            default_fstab = os.path.join(etc_dest, 'recovery.fstab')
            with open(default_fstab, 'w') as f:
                f.write("# Default recovery.fstab\n")
                f.write("/dev/block/platform/bootdevice/by-name/system    /system    ext4    ro    wait\n")
                f.write("/dev/block/platform/bootdevice/by-name/vendor    /vendor    ext4    ro    wait\n")
                f.write("/dev/block/platform/bootdevice/by-name/userdata  /data      ext4    rw    wait,check\n")
            log.info("Generated default recovery.fstab in system/etc/")

    def _generate_android_mk(self):
        path = os.path.join(self.device_path, 'Android.mk')
        with open(path, 'w') as f:
            f.write("""#
# Copyright (C) 2022 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

LOCAL_PATH := $(call my-dir)

ifeq ($(TARGET_DEVICE), {device})

include $(call all-subdir-makefiles,$(LOCAL_PATH))

endif
""".format(device=self.info.device_name))
        log.info("Android.mk generated")

    def _generate_android_products_mk(self):
        path = os.path.join(self.device_path, 'AndroidProducts.mk')
        with open(path, 'w') as f:
            f.write("""#
# Copyright (C) 2022 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

PRODUCT_MAKEFILES := \\
	$(LOCAL_DIR)/twrp_{codename}.mk

COMMON_LUNCH_CHOICES := \\
	twrp_{codename}-eng \\
	twrp_{codename}-userdebug \\
	twrp_{codename}-user
""".format(codename=self.info.codename))
        log.info("AndroidProducts.mk generated")

    def _generate_boardconfig_mk(self):
        kernel_base = getattr(self.info.header, 'kernel_load_addr', '0x3fff8000')
        kernel_offset = getattr(self.info.header, 'kernel_offset', '0x00008000')
        tags_offset = getattr(self.info.header, 'tags_load_addr', '0x07c88000')
        page_size = getattr(self.info.header, 'page_size', 4096)
        dtb_offset = getattr(self.info.header, 'dtb_load_addr', '0x07c88000')
        vendor_cmdline = getattr(self.info.header, 'cmdline', 'bootopt=64S3,32N2,64N2')
        path = os.path.join(self.device_path, 'BoardConfig.mk')
        with open(path, 'w') as f:
            f.write("""#
# Copyright (C) 2022 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

DEVICE_PATH := device/{manufacturer}/{codename}

# Architecture
TARGET_ARCH := {arch}
TARGET_ARCH_VARIANT := armv8-a
TARGET_CPU_ABI := arm64-v8a
TARGET_CPU_ABI2 :=
TARGET_CPU_VARIANT := generic

# Power
ENABLE_CPUSETS := true
ENABLE_SCHEDBOOST := true

# Bootloader
TARGET_BOOTLOADER_BOARD_NAME := {codename}
TARGET_NO_BOOTLOADER := true

# Build hacks
BUILD_BROKEN_DUP_RULES := true
BUILD_BROKEN_ELF_PREBUILT_PRODUCT_COPY_FILES := true

# DTBO
BOARD_KERNEL_SEPARATED_DTBO := true

# Kernel
TARGET_NO_KERNEL := true
BOARD_RAMDISK_USE_LZ4 := true
TARGET_PREBUILT_DTB := $(DEVICE_PATH)/prebuilt/dtb.img

BOARD_BOOT_HEADER_VERSION := {boot_header_version}
BOARD_KERNEL_BASE := {kernel_base}
BOARD_KERNEL_OFFSET := {kernel_offset}
BOARD_KERNEL_TAGS_OFFSET := {tags_offset}
BOARD_PAGE_SIZE := {page_size}
BOARD_TAGS_OFFSET := {tags_offset}
BOARD_RAMDISK_OFFSET := 0x26f08000  # common default
BOARD_DTB_SIZE := {dtb_size}
BOARD_DTB_OFFSET := {dtb_offset}
BOARD_VENDOR_BASE := {kernel_base}
BOARD_VENDOR_CMDLINE := {vendor_cmdline}

BOARD_MKBOOTIMG_ARGS += --dtb $(TARGET_PREBUILT_DTB)
BOARD_MKBOOTIMG_ARGS += --vendor_cmdline $(BOARD_VENDOR_CMDLINE)
BOARD_MKBOOTIMG_ARGS += --pagesize $(BOARD_PAGE_SIZE) --board ""
BOARD_MKBOOTIMG_ARGS += --kernel_offset $(BOARD_KERNEL_OFFSET)
BOARD_MKBOOTIMG_ARGS += --ramdisk_offset $(BOARD_RAMDISK_OFFSET)
BOARD_MKBOOTIMG_ARGS += --tags_offset $(BOARD_TAGS_OFFSET)
BOARD_MKBOOTIMG_ARGS += --header_version $(BOARD_BOOT_HEADER_VERSION)
BOARD_MKBOOTIMG_ARGS += --dtb_offset $(BOARD_DTB_OFFSET)

# AVB
BOARD_AVB_ENABLE := true

# Partitions configs
BOARD_FLASH_BLOCK_SIZE := 262144
BOARD_MAIN_SIZE := 12670140416
BOARD_SUPER_PARTITION_SIZE := 9126805504   # TODO: Fix hardcoded value
BOARD_VENDOR_BOOTIMAGE_PARTITION_SIZE := 67108864
BOARD_USES_METADATA_PARTITION := true
BOARD_SUPER_PARTITION_GROUPS := main
BOARD_MAIN_PARTITION_LIST += \\
    odm_dlkm \\
    product \\
    system \\
    system_ext \\
    vendor \\
    vendor_dlkm

BOARD_ODM_DLKMIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_PRODUCTIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_SYSTEM_EXTIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_USERDATAIMAGE_FILE_SYSTEM_TYPE := f2fs
BOARD_VENDORIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_VENDOR_DLKMIMAGE_FILE_SYSTEM_TYPE := ext4

TARGET_COPY_OUT_ODM_DLKM := odm_dlkm
TARGET_COPY_OUT_PRODUCT := product
TARGET_COPY_OUT_SYSTEM := system
TARGET_COPY_OUT_SYSTEM_EXT := system_ext
TARGET_COPY_OUT_VENDOR := vendor
TARGET_COPY_OUT_VENDOR_DLKM := vendor_dlkm

# Platform
TARGET_BOARD_PLATFORM := {board_platform}

# Properties
TARGET_SYSTEM_PROP += $(DEVICE_PATH)/system.prop

# Recovery
BOARD_HAS_LARGE_FILESYSTEM := true
BOARD_USES_GENERIC_KERNEL_IMAGE := true
BOARD_INCLUDE_RECOVERY_RAMDISK_IN_VENDOR_BOOT := true
BOARD_MOVE_RECOVERY_RESOURCES_TO_VENDOR_BOOT := true
TARGET_NO_RECOVERY := true
TARGET_RECOVERY_FSTAB := $(DEVICE_PATH)/recovery/root/system/etc/recovery.fstab
TARGET_RECOVERY_PIXEL_FORMAT := RGBX_8888
TARGET_USERIMAGES_USE_EXT4 := true
TARGET_USERIMAGES_USE_F2FS := true

# Crypto
TW_INCLUDE_CRYPTO := true
TW_INCLUDE_CRYPTO_FBE := true
TW_USE_FSCRYPT_POLICY := 2
TW_FORCE_KEYMASTER_VER := true

# Hack
PLATFORM_SECURITY_PATCH := 2099-12-31
PLATFORM_VERSION := 99.87.36
PLATFORM_VERSION_LAST_STABLE := $(PLATFORM_VERSION)
VENDOR_SECURITY_PATCH := $(PLATFORM_SECURITY_PATCH)
BOOT_SECURITY_PATCH := $(PLATFORM_SECURITY_PATCH)

# Tools
TW_INCLUDE_FB2PNG := true
TW_INCLUDE_NTFS_3G := true
TW_INCLUDE_REPACKTOOLS := true
TW_INCLUDE_RESETPROP := true
TW_INCLUDE_LPTOOLS := true

# F2FS
TW_ENABLE_FS_COMPRESSION := false

# Debug
TARGET_USES_LOGD := true
TWRP_INCLUDE_LOGCAT := true
TARGET_RECOVERY_DEVICE_MODULES += debuggerd
RECOVERY_BINARY_SOURCE_FILES += $(TARGET_OUT_EXECUTABLES)/debuggerd
TARGET_RECOVERY_DEVICE_MODULES += strace
RECOVERY_BINARY_SOURCE_FILES += $(TARGET_OUT_EXECUTABLES)/strace

# Fastbootd
TW_INCLUDE_FASTBOOTD := true

# TWRP Configs
TW_DEFAULT_BRIGHTNESS := 80
TW_EXCLUDE_APEX := true
TW_EXCLUDE_LPDUMP := true
TW_EXTRA_LANGUAGES := true
TW_FRAMERATE := 120
TW_THEME := portrait_hdpi
TARGET_USES_MKE2FS := true
TW_MAX_BRIGHTNESS := 255
TW_LOAD_VENDOR_BOOT_MODULES := true

# StatusBar
TW_STATUS_ICONS_ALIGN := center
TW_CUSTOM_CPU_POS := 300
TW_CUSTOM_CLOCK_POS := 70
TW_CUSTOM_BATTERY_POS := 790

# Hack depends
ALLOW_MISSING_DEPENDENCIES := true

# Assert
TARGET_OTA_ASSERT_DEVICE := {device_name}

# Brightness
TW_DEFAULT_BRIGHTNESS := 2047
TW_MAX_BRIGHTNESS := 4095

# Init
TARGET_INIT_VENDOR_LIB := libinit_{device_name}
TARGET_RECOVERY_DEVICE_MODULES := libinit_{device_name}

# Maintainer (optional)
TW_MAINTAINER := "アンドレイ"
""".format(
                manufacturer=self.info.manufacturer,
                codename=self.info.codename,
                arch=self.info.arch.split('-')[0],
                boot_header_version=self.info.boot_header_version,
                kernel_base=kernel_base,
                kernel_offset=kernel_offset,
                tags_offset=tags_offset,
                page_size=page_size,
                dtb_size=os.path.getsize(self.info.dtb_path) if self.info.dtb_path else 209018,
                dtb_offset=dtb_offset,
                vendor_cmdline=vendor_cmdline,
                board_platform=self.info.board_name,
                device_name=self.info.device_name,
            ))
        log.info("BoardConfig.mk generated")

    def _generate_device_mk(self):
        path = os.path.join(self.device_path, 'device.mk')
        with open(path, 'w') as f:
            f.write("""#
# Copyright (C) 2022 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

# Inherit from those products. Most specific first.
$(call inherit-product, $(SRC_TARGET_DIR)/product/core_64_bit_only.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/base.mk)

# Installs gsi keys into ramdisk, to boot a developer GSI with verified boot.
$(call inherit-product, $(SRC_TARGET_DIR)/product/gsi_keys.mk)

# Enable project quotas and casefolding for emulated storage without sdcardfs
$(call inherit-product, $(SRC_TARGET_DIR)/product/emulated_storage.mk)

# Enable Virtual A/B OTA
$(call inherit-product, $(SRC_TARGET_DIR)/product/virtual_ab_ota/launch_with_vendor_ramdisk.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/virtual_ab_ota/compression.mk)

ENABLE_VIRTUAL_AB := true
AB_OTA_UPDATER := true

AB_OTA_PARTITIONS += \\
    boot \\
    dtbo \\
    lk \\
    odm \\
    odm_dlkm \\
    product \\
    system \\
    system_ext \\
    vbmeta_system \\
    vbmeta_vendor \\
    vendor \\
    vendor_boot \\
    vendor_dlkm

AB_OTA_POSTINSTALL_CONFIG += \\
    RUN_POSTINSTALL_system=true \\
    POSTINSTALL_PATH_system=system/bin/mtk_plpath_utils \\
    FILESYSTEM_TYPE_system=ext4 \\
    POSTINSTALL_OPTIONAL_system=true

AB_OTA_POSTINSTALL_CONFIG += \\
    RUN_POSTINSTALL_vendor=true \\
    POSTINSTALL_PATH_vendor=bin/checkpoint_gc \\
    FILESYSTEM_TYPE_vendor=ext4 \\
    POSTINSTALL_OPTIONAL_vendor=true

PRODUCT_PACKAGES += \\
    otapreopt_script \\
    cppreopts.sh

PRODUCT_PROPERTY_OVERRIDES += ro.twrp.vendor_boot=true

# Dynamic Partitions
PRODUCT_USE_DYNAMIC_PARTITIONS := true

# API
PRODUCT_SHIPPING_API_LEVEL := 31
PRODUCT_TARGET_VNDK_VERSION := 31

# Boot control HAL
PRODUCT_PACKAGES += \\
    android.hardware.boot@1.2-mtkimpl \\
    android.hardware.boot@1.2-mtkimpl.recovery

PRODUCT_PACKAGES_DEBUG += \\
    bootctl

# Fastbootd
PRODUCT_PACKAGES += \\
    android.hardware.fastboot@1.0-impl-mock \\
    fastbootd

# Health Hal
PRODUCT_PACKAGES += \\
    android.hardware.health@2.1-impl \\
    android.hardware.health@2.1-service

# Keymaster
PRODUCT_PACKAGES += \\
    android.hardware.keymaster@4.1

# Keystore Hal
PRODUCT_PACKAGES += \\
    android.system.keystore2

# MTK plpath utils
PRODUCT_PACKAGES += \\
    mtk_plpath_utils \\
    mtk_plpath_utils.recovery

# Security
PRODUCT_PACKAGES += \\
    android.hardware.security.keymint \\
    android.hardware.security.secureclock \\
    android.hardware.security.sharedsecret

# Update engine
PRODUCT_PACKAGES += \\
    update_engine \\
    update_engine_sideload \\
    update_verifier

PRODUCT_PACKAGES_DEBUG += \\
    update_engine_client

# Additional configs
TW_RECOVERY_ADDITIONAL_RELINK_LIBRARY_FILES += \\
    $(TARGET_OUT_SHARED_LIBRARIES)/android.hardware.keymaster@4.1

TARGET_RECOVERY_DEVICE_MODULES += \\
    android.hardware.keymaster@4.1
""")
        log.info("device.mk generated")

    def _generate_product_mk(self):
        path = os.path.join(self.device_path, f'twrp_{self.info.codename}.mk')
        with open(path, 'w') as f:
            f.write("""#
# Copyright (C) 2022 The LineageOS Project
#
# SPDX-License-Identifier: Apache-2.0
#

# Inherit from {device} device
$(call inherit-product, device/{manufacturer}/{codename}/device.mk)

# Inherit some common TWRP stuff.
$(call inherit-product, vendor/twrp/config/common.mk)

# Product Specifics
PRODUCT_NAME := twrp_{codename}
PRODUCT_DEVICE := {codename}
PRODUCT_BRAND := {brand}
PRODUCT_MODEL := {brand} {codename}
PRODUCT_MANUFACTURER := {brand_upper}

PRODUCT_GMS_CLIENTID_BASE := android-{brand_lower}
""".format(
                device=self.info.device_name,
                manufacturer=self.info.manufacturer,
                codename=self.info.codename,
                brand=self.info.manufacturer.capitalize(),
                brand_upper=self.info.manufacturer.upper(),
                brand_lower=self.info.manufacturer.lower(),
            ))
        log.info(f"twrp_{self.info.codename}.mk generated")

    def _generate_system_prop(self):
        path = os.path.join(self.device_path, 'system.prop')
        with open(path, 'w') as f:
            f.write(f"""# Fstab
ro.postinstall.fstab.prefix=/system

# USB MTP
ro.sys.usb.storage.type=mtp

# Crypto
ro.crypto.volume.filenames_mode=aes-256-cts

# Gatekeeper
ro.hardware.gatekeeper=trustonic

# TEE
ro.vendor.mtk_tee_gp_support=1
ro.vendor.mtk_trustonic_tee_support=1

keymaster_ver=4.1
""")
            f.write("\n# Additional properties from device\n")
            for key in ['ro.build.version.sdk', 'ro.build.version.release', 'ro.board.platform']:
                if key in self.info.props:
                    f.write(f"{key}={self.info.props[key]}\n")
        log.info("system.prop generated")

    def _generate_vendorsetup(self):
        path = os.path.join(self.device_path, 'vendorsetup.sh')
        with open(path, 'w') as f:
            f.write("#!/bin/bash\n")
            f.write(f"add_lunch_combo twrp_{self.info.codename}-eng\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        log.info("vendorsetup.sh generated")

    def _generate_readme(self):
        path = os.path.join(self.device_path, 'README.md')
        with open(path, 'w') as f:
            f.write(f"# TWRP Device Tree for {self.info.device_name}\n\n")
            f.write("Generated by transsion_twrp_vendor_boot_gen\n\n")
            f.write("## Device specifications\n\n")
            f.write(f"- Device: {self.info.device_name}\n")
            f.write(f"- Board: {self.info.board_name}\n")
            f.write(f"- Platform: {self.info.platform}\n")
            f.write(f"- Android version: {self.info.sdk_version}\n\n")
            f.write("## Features\n\n")
            f.write("- Works with vendor_boot\n")
            f.write("- Dynamic partitions: {}\n".format(self.info.dynamic_partitions))
        log.info("README.md generated")

# -------------------- Main --------------------
def main():
    print_banner()
    time.sleep(0.5)

    parser = argparse.ArgumentParser(description='Generate TWRP device tree for Transsion vendor_boot devices')
    parser.add_argument('input', help='Path to vendor_boot.img OR directory containing unpacked contents')
    parser.add_argument('-o', '--output', default='./output', help='Output directory (default: ./output)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose logging')
    parser.add_argument('--keep-temp', action='store_true', help='Keep temporary working directory')
    parser.add_argument('--device-name', help='Manually specify device name (e.g., Infinix-X6885) if auto‑detection fails')
    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    if args.output == './output':
        effective_output = os.path.join(args.output, 'TWRP')
    else:
        effective_output = args.output

    if not os.path.exists(args.input):
        log.error(f"Path not found: {args.input}")
        sys.exit(1)

    temp_dir = tempfile.mkdtemp(prefix='twrp_vendor_')
    log.info(f"Working directory: {temp_dir}")

    try:
        if os.path.isdir(args.input):
            parse_result = load_from_directory(args.input)
            info = DeviceInfo(temp_dir, parse_result, manual_device_name=args.device_name)
            info.extract_all()
        else:
            parse_result = parse_vendor_boot(args.input)
            info = DeviceInfo(temp_dir, parse_result, manual_device_name=args.device_name)
            info.extract_all()

        info.log_summary()
        generator = DeviceTreeGenerator(info, effective_output)
        generator.generate()

    except Exception as e:
        log.error(f"Fatal error: {e}", exc_info=args.verbose)
        if args.keep_temp:
            log.info(f"Temporary directory kept at: {temp_dir}")
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)
        sys.exit(1)
    else:
        if not args.keep_temp:
            shutil.rmtree(temp_dir, ignore_errors=True)
            log.debug("Temporary directory removed.")
        else:
            log.info(f"Temporary directory preserved: {temp_dir}")

    log.info("Done!")

if __name__ == '__main__':
    main()
