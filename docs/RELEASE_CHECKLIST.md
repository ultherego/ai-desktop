# Release checklist

For project maintainers. Tick items for a specific commit: the result of another commit attests nothing.

Evidence for each candidate (CachyOS checks, GitHub jobs, what was not exercised) is kept only in the progress log of [ROADMAP.md](ROADMAP.md); this page is the list to tick for the release commit. The few items ticked below record earlier candidates and must be ticked again for the release commit.

## Version

- [ ] The `pkgver-pkgrel` number in `packaging/arch/PKGBUILD` is the 1.0 release number.
- [ ] The same number is in README (the line about the current package), at the top of `CHANGELOG.md`,
      in `scripts/accept_web_candidate.fish` and in `tests/host_analysis_acceptance.py`.
- [ ] Document the supported native system, official installer, HOME and npm layouts plus registration/refresh behavior, minimum versions, tested baselines and capability requirements. Ordinary `doctor` remains metadata-only; `doctor --clients` must remain an explicit selected-provider offline probe without account access.
- [ ] Release acceptance covers upgrade from an earlier 0.1.0 package (including 79/80, which bundled their clients) and confirms that existing accounts and data survive.
- [ ] The entry in `CHANGELOG.md` describes the changes in plain language.

## Tests

- [ ] `python3 -B scripts/verify.py --mode ci --fail-fast` passes on this commit.
- [ ] GitHub Actions are green on this commit.
- [ ] `python3 -B scripts/verify.py --mode local --fail-fast` passes on CachyOS or Arch.
- [ ] Any change to isolation, permissions, the file system, network or login has been checked on a real system.

## Package

- [ ] Build in a clean checkout with `makepkg --cleanbuild` in `packaging/arch`; the same application package is built for every provider selection. Legacy ignored client files must not be read, packaged or used.
- [ ] The package for distribution does not contain `claude-code` or `codex`.
- [ ] Install, update and `pacman -R` done according to [INSTALLATION.md](INSTALLATION.md); the list of remaining data is correct.
- [ ] `ai-desktop doctor`, `ai-desktop status` and one real question through `ai-desktop ask` work.

## Client and task-approval gates

These gates apply to the 1.0 release commit. Metadata-only discovery and a successful compatibility probe alone cannot close them.

- [ ] Claude-only and Codex-only installations work independently; neither pulls in the other client. Test system packages, official curl installers, current npm, custom native paths and local-only setup before provider installation.
- [ ] Registration/refresh approval binds the complete payload digest; the fixed administrator helper does not execute the source. Source mutation, symlink retargeting, truncated copies and tampered metadata fail closed. An unapproved source update does not replace registered bytes.
- [ ] System-wide registration priority, explicit removal and retained original client/account data are covered in installation and upgrade acceptance.
- [ ] The distributed application package contains no vendor executable and performs no hidden client download.
- [ ] Unsafe ownership/modes, path substitution, unrecognized wrappers, unsupported version families and incompatible capabilities fail before model data or credentials are released.
- [ ] The approved operation uses one bound executable and client-private resource snapshot; replacement or an update during the operation refuses further phases. Test symlink retargeting and in-place changes, not just an atomic file rename.
- [ ] A tested compatible client update between tasks works without rebuilding AI Desktop; registered clients use an explicit approved refresh.
- [x] On candidate-82 head `0f0bcb6`, a native Claude client newer than the tested baseline (compatible-untested) rejects the appended invalid option after the full analyze/web option sets, demonstrates first-error ordering with two invalid options, and independently returns signed-out `auth status` JSON in an empty offline profile. Print and auth modes are not combined; help-only or synthetic parser tests are not this native evidence.
- [ ] Separate authentication, restricted tools, model transport, namespace and egress checks pass through the release runtime on CachyOS, with Claude and with Codex.
- [ ] A `project` task approval (stages, follow-up questions, an edit with its declared suite) and a `task run` approval cannot authorize an undeclared operation; `ask` still asks per copy test; the final diff still requires a separate write decision.
- [x] Candidate 84 (offline acceptance on `457118d`): one project approval covers only the declared stage/request/data/time budgets; a changed file (including within a stage), manifest, policy, recipient or expired scope stops the next transfer or automatic edit handoff. Ctrl+C stops continuation.
- [x] Candidate 84 (offline acceptance on `457118d`): standalone task approval shows the selected workspace/files and exact test before execution; Go/npm run once without a duplicate prompt. Changed scope/recipe, replay and failure refuse, while the full diff, write refusal and rollback remain independent. Ordinary ask/project-edit test approvals are unchanged.
- [x] Candidate 85 (accepted on CachyOS, merged in PR #23): ordinary interactive `ask` publishes one complete private record only after the first completed turn or controller-recorded action, checkpoints later completed turns, and creates no empty record after refusal or cancellation. Interrupted publication leaves no JSON or temporary record.
- [x] Candidate 85 (accepted on CachyOS, merged in PR #23): `--no-save` performs no conversation-store creation/update; saved data contains no OAuth, native session, consent/test grant, policy permission or write approval, and resume still rechecks current recipient, policy, sources and consent.
- [x] Candidate 85 (accepted on CachyOS, merged in PR #23): exact-head CachyOS CI/LOCAL, clean package build, upgrade from candidate 84, installed-file equality and an installed synthetic autosave/refusal/interruption scenario pass before merge.
- [ ] Cleanup removes only proven-unused experiments. Production modules, fixtures and checks are not removed merely because their names start with `e2_`.

## Repository content

- [ ] No secrets, login files, client binaries or data about a specific computer or person.
- [ ] Links in the documentation lead to existing files.
- [ ] `docs/KNOWN_LIMITATIONS.md`, `SECURITY.md` and `docs/THREAT_MODEL.md` describe the current behavior.
- [ ] Before distribution, the Claude Code terms and the subscription usage rules have been checked.

## Publishing

- [ ] README, SECURITY.md and KNOWN_LIMITATIONS.md describe the 1.0 release; no "development version" or candidate wording is left in user documentation.
- [ ] The release tag and description point to the exact commit.
