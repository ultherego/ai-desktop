# Troubleshooting

## Checking the installation

`ai-desktop doctor` and `doctor --v` inspect metadata without executing a client. `doctor --clients` explicitly probes the selected cloud client offline, without login, network or a model request. A successful probe checks adapter prerequisites; it does not certify the vendor or prove that authentication and inference work.

## When something does not work

| Symptom | What to do |
|---|---|
| `client-missing` or `doctor` reports a missing client | Run `ai-desktop clients list`; install a provider independently or register an existing HOME/native/npm installation. Rebuilding AI Desktop does not install a client. |
| `client-unsafe` / `client-layout` | Check the source and supported native layout. HOME installations require registration. Arbitrary shell/JavaScript wrappers are not executed; point `clients register` at a recognized native payload. |
| `client-version` / `client-protocol` | Run `ai-desktop doctor --clients` and use a compatible client version. Refresh a registered copy after updating its source. Do not edit version pins or bypass the adapter check. |
| `client-changed` | Finish the update or registration refresh, then start a new operation so its selected payload is checked from the beginning. |
| `status`: `auth=unchecked` | This is normal: `status` and `doctor` do not check login or network. |
| Request to log in | `ai-desktop login`, then a new question. |
| Service limit, network error, timeout | The question is not asked again by itself: wait and ask it again. Only two narrow repeats exist ([trust boundaries](../SECURITY.md#trust-boundaries)): a web answer that arrived but failed its checks is asked once more, silently, while at least half of its time is left; and a request the service never started (a kept connection closed before the service took it, or a worker that was being replaced) is sent again, so nothing runs twice. The waiting time is 110 s, for `high` 5 minutes, and for `xhigh` and `max` 15 minutes. |
| Session busy or unconfirmed stop | `ai-desktop status`, then `ai-desktop stop` (stops the sessions of all your terminals). |
| `Invalid permissions.toml` | The message points to the key. Also check mode `0600`. |
| `Claude is blocked` | Set `claude = true` in `[network]`. |
| "… belongs to another account" when saving, moving or deleting | A folder on the way to the `write` scope belongs to another account (not you, not root). The message names each folder and its owner; use a folder of yours. |
| Read refused | Check `read` and `deny`. A link, special file, foreign owner or hard link means refusal. The message gives the limit. |
| `Nothing was saved` | Check `read` and `write` and whether the directory exists; then `task status`. |
| A write refuses after an interrupted task | Temporary files were left. `task status`, then `task rollback`. Do not erase the journal. |
| Limit of 128 tasks | The write will not be performed. Review `task status`, then remove finished entries with `ai-desktop task prune --workspace DIRECTORY` (separate consent; user files are never deleted). |
