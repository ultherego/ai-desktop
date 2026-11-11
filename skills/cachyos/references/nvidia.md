---
name: nvidia
description: Choosing the NVIDIA driver package for a GPU family and checking module loading and DRM kernel mode setting.
tools: nvidia-utils
source: https://wiki.archlinux.org/title/NVIDIA
section: Installation; DRM kernel mode setting; Early loading; Wayland configuration
checked: 2026-11-11
version: rolling
---

# NVIDIA

Applies when an NVIDIA GPU is confirmed and the driver choice, module loading or display start is in question. Use the repository packages, not the installer from the NVIDIA website.

Read first: `lspci -k -d ::03xx` for the model and the kernel driver in use; map the model to its family before choosing a package. With the driver loaded, `cat /sys/module/nvidia_drm/parameters/modeset` and `.../fbdev` should both return `Y`; DRM is enabled by default since `nvidia-utils` 560.35.03-5.

Interpret the family: Blackwell and newer use `nvidia-open` (upstream recommendation); Turing, Ampere and Ada use `nvidia-open` or the proprietary `nvidia-580xx-dkms` (AUR); Maxwell, Pascal and Volta use `nvidia-580xx-dkms`; Kepler and older are legacy and unsupported. The package must match the kernel: `nvidia-open` for `linux`, `nvidia-open-lts` for `linux-lts`, the `-dkms` variant for any kernel, rebuilt only for kernels whose headers are installed. After a kernel or driver update, compare the running kernel with the variant and the installed headers before suspecting hardware; a new package without a restart is not in use. 32-bit programs need the matching `lib32` package.

`nvidia-utils` blacklists `nouveau` after a reboot and also blacklists the Nova driver. Wayland needs DRM KMS; restarting before it is in place can give a black screen. Adding the modules to the initramfs (early loading) breaks hibernation by default. The GSP firmware is known to cause crashes; `NVreg_EnableGpuFirmware=0` works only with the proprietary driver. `ibt=off` removes a security mitigation.

Changes needing separate consent: driver package swaps, module parameters, kernel parameters, initramfs changes, `nvidia-xconfig` (rewrites `/etc/X11/xorg.conf`). Record the previous packages and files; undo reinstalls them and regenerates the initramfs. Confirm a working fallback boot path first.

Stop when the family is unknown, the hardware is legacy and unsupported, or no recovery path exists. Only a restart and the original scenario confirm a fix.
