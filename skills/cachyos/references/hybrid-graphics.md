---
name: hybrid-graphics
description: Identifying which GPU renders on a two-GPU system, PRIME render offload, and settings not to change blindly.
tools: mesa, nvidia-utils
source: https://wiki.archlinux.org/title/PRIME
section: PRIME GPU offloading; PRIME render offload; PCI-Express Runtime D3 (RTD3) Power Management; Configure applications to render using GPU; Troubleshooting
checked: 2026-11-11
version: rolling
---

# Hybrid graphics (PRIME)

Applies when two GPUs are present and an application uses the wrong one, the discrete GPU never sleeps, or offloading fails. PRIME renders on one GPU and displays through the GPU that has the screen connected.

Read first: `lspci -nnd ::03xx` lists the GPUs with PCI IDs. Which GPU renders: `glxinfo | grep "OpenGL renderer"` with and without `DRI_PRIME=1` (open-source drivers) or through `prime-run` (NVIDIA, from `nvidia-prime`); `prime-run vulkaninfo` for Vulkan. Power state: `cat /sys/class/drm/card*/device/power_state`, and for an NVIDIA GPU `cat /sys/bus/pci/devices/<address>/power/runtime_status` (`suspended` or `running`) or `.../runtime_suspended_time`, which keeps increasing while suspended. `lsof +c0 /dev/nvidia*` lists users without waking the GPU; `nvidia-smi` wakes it.

Interpret: offload is per application through environment variables (`DRI_PRIME=1`, or `__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia`), not a system setting. Vulkan with `DRI_PRIME` needs `vulkan-mesa-layers`. A dGPU that stays awake may be held by a process or lack runtime D3 support (`/proc/driver/nvidia/gpus/<address>/power`). Use the actual PCI address, not an example.

Do not change blindly; each needs separate consent and a recorded way back: udev rules `/etc/udev/rules.d/80-nvidia-pm.rules` and `NVreg_DynamicPowerManagement` in `/etc/modprobe.d/` (both need a restart; undo removes the files), global vendor variables in `/etc/environment` (they must be unset to use the dGPU again), `xorg.conf` layouts and BusIDs, module blacklists, and kernel parameters. A leftover `bumblebee` blacklists `nvidia_drm`, which offload needs; removing it is a package change.

Stop when the GPUs or drivers are not confirmed, when a black screen or freeze would leave no recovery path, or when the request is a power optimization without a measured symptom.
