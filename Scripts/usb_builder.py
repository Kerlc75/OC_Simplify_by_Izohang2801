from Scripts import utils
import json
import os
import shutil
import subprocess

class USBBuilder:
    build_marker = ".build_complete"
    required_efi_files = (
        ("EFI", "BOOT", "BOOTx64.efi"),
        ("EFI", "OC", "OpenCore.efi"),
        ("EFI", "OC", "config.plist"),
    )

    def __init__(self):
        self.utils = utils.Utils()
        self.efi_size_gb = 4
        self.min_disk_size = (self.efi_size_gb + 1) * 1024 ** 3
        self.max_disk_size = 2 * 1024 ** 4

    def is_admin(self):
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def powershell(self, script):
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise Exception((result.stderr or result.stdout).strip() or "PowerShell exited with code {}".format(result.returncode))
        return result.stdout.strip()

    def get_usb_disks(self):
        output = self.powershell(
            "$ErrorActionPreference = 'Stop'; "
            "$disks = @(Get-Disk | Where-Object { $_.BusType -eq 'USB' -and -not $_.IsBoot -and -not $_.IsSystem } | Sort-Object Number | "
            "Select-Object Number, UniqueId, FriendlyName, SerialNumber, Size, PartitionStyle, "
            "@{Name='DriveLetters'; Expression={ (@(Get-Partition -DiskNumber $_.Number -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter } | ForEach-Object { [string]$_.DriveLetter + ':' })) -join ', ' }}); "
            "ConvertTo-Json -InputObject $disks -Compress"
        )
        disks = json.loads(output) if output else []
        return disks if isinstance(disks, list) else [disks]

    def disk_identity(self, disk):
        return "{}|{}|{}".format(disk.get("UniqueId") or "", disk.get("SerialNumber") or "", disk.get("Size") or 0)

    def format_disk(self, disk):
        script = """
$ErrorActionPreference = 'Stop'
$disk = Get-Disk -Number {number}
if ($disk.BusType -ne 'USB' -or $disk.IsBoot -or $disk.IsSystem) {{ throw 'Disk {number} is not a removable USB data disk.' }}
if ("$($disk.UniqueId)|$($disk.SerialNumber)|$($disk.Size)" -ne '{identity}') {{ throw 'Disk {number} is no longer the drive you selected. Nothing was changed.' }}
if ($disk.IsOffline) {{ Set-Disk -Number {number} -IsOffline $false }}
if ($disk.IsReadOnly) {{ Set-Disk -Number {number} -IsReadOnly $false }}
if ($disk.PartitionStyle -ne 'RAW') {{ Clear-Disk -Number {number} -RemoveData -RemoveOEM -Confirm:$false }}
Initialize-Disk -Number {number} -PartitionStyle MBR
$efi = New-Partition -DiskNumber {number} -Size {efi_size}GB -MbrType FAT32 -IsActive -AssignDriveLetter
Format-Volume -Partition $efi -FileSystem FAT32 -NewFileSystemLabel 'EFI' -Force -Confirm:$false | Out-Null
$data = New-Partition -DiskNumber {number} -UseMaximumSize -MbrType IFS -AssignDriveLetter
Format-Volume -Partition $data -FileSystem exFAT -NewFileSystemLabel 'DATA' -Force -Confirm:$false | Out-Null
$efi = Get-Partition -DiskNumber {number} -PartitionNumber $efi.PartitionNumber
$data = Get-Partition -DiskNumber {number} -PartitionNumber $data.PartitionNumber
ConvertTo-Json -Compress -InputObject @{{ EFI = [string]$efi.DriveLetter; DATA = [string]$data.DriveLetter }}
""".format(number=int(disk.get("Number")), identity=self.disk_identity(disk).replace("'", "''"), efi_size=self.efi_size_gb)
        return json.loads(self.powershell(script).splitlines()[-1])

    def format_size(self, size):
        return "{:.1f} GB".format(int(size) / 1024 ** 3)

    def finish(self, message, color="91"):
        print("")
        print("\033[{}m{}\033[0m".format(color, message))
        print("")
        self.utils.request_input("Press Enter to go back...")

    def build_usb(self, result_dir):
        title = "Windows EFI USB Build"
        efi_source = os.path.join(result_dir, "EFI")

        self.utils.head(title)
        if os.name != "nt":
            return self.finish("This option is only available on Windows.")
        missing = [os.path.join(*path) for path in self.required_efi_files if not os.path.isfile(os.path.join(result_dir, *path))]
        if not os.path.isfile(os.path.join(result_dir, self.build_marker)) or missing:
            return self.finish("No complete EFI build found at {}. Build the OpenCore EFI first.".format(efi_source))
        if not self.is_admin():
            return self.finish("Administrator rights are required. Close this window and run OpCore-Simplify.bat as Administrator.")

        print("")
        print("This creates a bootable OpenCore USB drive:")
        print("  - MBR partition table")
        print("  - {} GB FAT32 partition labeled EFI (your built EFI is copied here)".format(self.efi_size_gb))
        print("  - exFAT partition labeled DATA on the remaining space")
        print("")
        print("\033[1;91mWARNING: The selected USB drive will be FORMATTED and ALL DATA ON IT WILL BE DESTROYED.\033[0m")
        print("")
        self.utils.request_input("Attach the USB drive now, then press Enter to continue...")

        while True:
            self.utils.head(title)
            print("")
            try:
                disks = self.get_usb_disks()
            except Exception as e:
                return self.finish("Failed to list USB drives: {}".format(e))

            if not disks:
                print("No USB drives found.")
            else:
                print("Attached USB drives:")
                print("")
                for disk in disks:
                    print("  {}. {} ({}) - {}{}".format(
                        disk.get("Number"), (disk.get("FriendlyName") or "Unknown").strip(), self.format_size(disk.get("Size", 0)),
                        disk.get("PartitionStyle"), " - " + disk.get("DriveLetters") if disk.get("DriveLetters") else ""
                    ))
            print("")
            print("R. Refresh list")
            print("B. Back")
            print("")
            option = self.utils.request_input("Select the USB drive number: ").strip().lower()
            if option == "b":
                return
            if option == "r":
                continue

            disk = next((d for d in disks if str(d.get("Number")) == option), None)
            if not disk:
                print("\033[91mInvalid selection, please try again.\033[0m")
                self.utils.request_input("Press Enter to continue...")
                continue
            if int(disk.get("Size", 0)) < self.min_disk_size:
                print("\033[91mThe selected drive is too small. At least {} is required.\033[0m".format(self.format_size(self.min_disk_size)))
                self.utils.request_input("Press Enter to continue...")
                continue
            if int(disk.get("Size", 0)) > self.max_disk_size:
                print("\033[91mThe selected drive is larger than 2 TB, which an MBR partition table cannot fully use. Use a smaller drive.\033[0m")
                self.utils.request_input("Press Enter to continue...")
                continue
            break

        self.utils.head(title)
        print("")
        print("\033[1;91mFINAL WARNING\033[0m")
        print("")
        print("  Disk {}: {} ({})".format(disk.get("Number"), (disk.get("FriendlyName") or "Unknown").strip(), self.format_size(disk.get("Size", 0))))
        if disk.get("DriveLetters"):
            print("  Current volumes: {}".format(disk.get("DriveLetters")))
        print("")
        print("\033[1;91mEverything on this drive will be permanently erased.\033[0m")
        print("")
        if self.utils.request_input("Type YES to format this drive: ").strip() != "YES":
            return self.finish("Cancelled. No changes were made.", "93")

        print("")
        print("Creating MBR disk, {} GB FAT32 EFI partition and exFAT DATA partition...".format(self.efi_size_gb))
        try:
            letters = self.format_disk(disk)
        except Exception as e:
            return self.finish("Failed to format the USB drive: {}".format(e))

        if not letters.get("EFI"):
            return self.finish("The EFI partition was created but has no drive letter. Assign one in Disk Management and copy {} manually.".format(efi_source))

        efi_target = "{}:\\EFI".format(letters.get("EFI"))
        print("Copying {} to {}...".format(efi_source, efi_target))
        try:
            shutil.copytree(efi_source, efi_target, dirs_exist_ok=True)
        except Exception as e:
            return self.finish("Failed to copy the EFI: {}".format(e))

        self.finish("USB drive is ready. EFI partition: {}:  DATA partition: {}:".format(letters.get("EFI"), letters.get("DATA") or "?"), "92")
