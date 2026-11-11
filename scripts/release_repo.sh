#!/usr/bin/env bash
# Build the signed pacman repository "ai-desktop" from built packages.
# Usage: scripts/release_repo.sh OUTDIR KEY PACKAGE...
#   OUTDIR  an empty or new directory that receives the repository files
#   KEY     the fingerprint of the project signing key, already in the gpg keyring
# Each package gets a detached signature, the database and file list are signed, and plain copies of
# ai-desktop.db / ai-desktop.files replace repo-add's symlinks (release assets cannot be links).
set -euo pipefail

if (( $# < 3 )); then
    echo "Usage: $0 OUTDIR KEY PACKAGE..." >&2
    exit 2
fi
out=$1
key=$2
shift 2

mkdir -p -- "$out"
if [[ -n "$(ls -A -- "$out")" ]]; then
    echo "$out is not empty" >&2
    exit 2
fi

for package in "$@"; do
    [[ -f "$package" && ! -L "$package" && "$package" == *.pkg.tar.zst ]] || { echo "not a package: $package" >&2; exit 2; }
    name=${package##*/}
    cp -- "$package" "$out/$name"
    gpg --batch --yes --local-user "$key" --detach-sign --no-armor --output "$out/$name.sig" "$out/$name"
done

(
    cd -- "$out"
    repo-add --sign --key "$key" ai-desktop.db.tar.gz ./*.pkg.tar.zst
    for part in db files; do
        rm -f -- "ai-desktop.$part" "ai-desktop.$part.sig"
        cp -- "ai-desktop.$part.tar.gz" "ai-desktop.$part"
        cp -- "ai-desktop.$part.tar.gz.sig" "ai-desktop.$part.sig"
    done
)

# Every signature must verify against the key before anything is published.
for signature in "$out"/*.sig; do
    gpg --batch --verify "$signature" "${signature%.sig}" 2>/dev/null || { echo "bad signature: $signature" >&2; exit 1; }
done
ls -1 -- "$out"
