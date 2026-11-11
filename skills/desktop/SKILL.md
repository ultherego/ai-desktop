---
name: desktop
description: Diagnoses a desktop environment, compositor, window manager or desktop shell from its version, configuration and documentation. Use for KDE Plasma, GNOME, COSMIC, Xfce, Cinnamon, Budgie, MATE, LXQt, LXDE, Hyprland, niri, Sway, i3, MangoWM, Wayfire, Qtile, bspwm, Openbox, DMS and Noctalia.
---

# Desktop, compositor and shell

The catalog lists the guides of the environments installed on this computer. Request the guide of the environment in question before reading configuration; several can be installed, and an installed package does not prove which session is running.

## Rules

1. Establish the running environment, its version, the session type (Wayland or X11) and the exact symptom. Treat a desktop shell (DMS, Noctalia) as a component separate from the compositor under it.
2. Commands run by AI Desktop have no graphical session, session bus or compositor socket. A failed IPC, D-Bus or display connection is missing visibility, not a fault; propose such a command to the user instead.
3. Use the documentation of the installed version; options change between releases. Do not carry syntax between environments. Without a source, mark an option as unverified.
4. Read configuration only within the allowed file scopes. Establish whether a file is generated or overwritten by a settings tool before proposing an edit.
5. Propose the smallest change, keep the previous content, check it with the environment's own offline validator when the exact command is allowed. A reload, a restart of the shell or of a user service is a separate consented step; never end the user's session.
6. Report the environment and version, the file and source used, the evidence or the gap, and the write state.

## Guides

| Environment | Guide |
|---|---|
| KDE Plasma | `desktop/plasma` |
| GNOME | `desktop/gnome` |
| COSMIC | `desktop/cosmic` |
| Xfce | `desktop/xfce` |
| Cinnamon | `desktop/cinnamon` |
| Budgie | `desktop/budgie` |
| MATE | `desktop/mate` |
| LXQt | `desktop/lxqt` |
| LXDE | `desktop/lxde` |
| Hyprland | `desktop/hyprland` |
| niri | `desktop/niri` |
| Sway | `desktop/sway` |
| i3 | `desktop/i3` |
| MangoWM | `desktop/mango` |
| Wayfire | `desktop/wayfire` |
| Qtile | `desktop/qtile` |
| bspwm | `desktop/bspwm` |
| Openbox | `desktop/openbox` |
| DMS (Dux Modular Shell) | `desktop/dms` |
| Noctalia | `desktop/noctalia` |
