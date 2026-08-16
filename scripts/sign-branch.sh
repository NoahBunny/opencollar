#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
#
# sign-branch.sh — make every enforcement-sensitive commit on this branch carry
# a signature the `signed-commits` CI gate will accept.
#
#   bash scripts/sign-branch.sh              # report only, changes nothing
#   bash scripts/sign-branch.sh --apply      # configure signing + rewrite history
#   bash scripts/sign-branch.sh --apply --base main
#
# Never pushes. The rewrite changes every SHA on the branch, so the push is
# yours to make deliberately; the script prints the exact command and leaves a
# backup branch behind either way.
#
# WHY A SCRIPT AND NOT A ONE-LINER
# --------------------------------
# CONTRIBUTING.md offers the one-liner:
#     git rebase --exec 'git commit --amend --no-edit -S' <upstream>..HEAD
# On its own that is not enough to turn the gate green, for two reasons found
# on 2026-08-17:
#
#   1. It signs nothing unless signing is configured first. This machine had no
#      commit.gpgsign, no user.signingkey, no gpg.format.
#   2. Even once signed, an SSH signature reads %G?=N — identical to unsigned —
#      in any checkout without gpg.ssh.allowedSignersFile, which is exactly what
#      a CI runner is. Verified directly: N without the file, G with it. So the
#      gate would have kept failing on commits that were genuinely signed.
#
# The repo side of (2) ships alongside this script: .github/allowed_signers
# holds the trusted keys and signed-commits.yml points git at it.
set -euo pipefail

APPLY=0
BASE="main"
while [ $# -gt 0 ]; do
    case "$1" in
        --apply) APPLY=1 ;;
        --base)  BASE="${2:?--base needs a ref}"; shift ;;
        -h|--help) sed -n '5,12p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

# Path list mirrors SENSITIVE_REGEX in .github/workflows/signed-commits.yml.
# Kept byte-identical on purpose — if these drift, the script reports a verdict
# CI does not share, which is worse than not checking at all.
SENSITIVE_REGEX='^(shared/focuslock_vault\.py|shared/focuslock_mesh\.py|shared/focuslock_payment\.py|shared/focuslock_penalties\.py|shared/focuslock_config\.py|shared/focuslock_sync\.py|shared/banks\.json|focuslock_mesh\.py|focuslock_ntfy\.py|focuslock-mail\.py|focuslock-desktop\.py|focuslock-desktop-win\.py|watchdog-win\.pyw|build-win\.py|android/(slave|controller|companion)/|installers/|\.github/workflows/|\.github/dependabot\.yml|\.github/CODEOWNERS|\.github/allowed_signers)'

say()  { printf '%s\n' "$*"; }
head2() { printf '\n== %s ==\n' "$*"; }
die()  { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

cd "$(git rev-parse --show-toplevel 2>/dev/null)" || die "not inside a git repository"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" != "HEAD" ] || die "detached HEAD — check out the branch you mean to sign"
git rev-parse --verify --quiet "$BASE" >/dev/null || die "base ref '$BASE' does not exist"
MERGE_BASE="$(git merge-base "$BASE" HEAD)"

# ── what needs signing ──────────────────────────────────────────────────────
mapfile -t COMMITS < <(git rev-list "$MERGE_BASE..HEAD")
[ "${#COMMITS[@]}" -gt 0 ] || die "no commits between $BASE and $BRANCH — nothing to do"

# Verification needs a trust list even in report mode. Without one, git cannot
# check an SSH signature and reports N — so a branch that is already correctly
# signed would be reported as entirely unsigned, which is the specific lie this
# script exists to stop telling. Fall back to the in-repo list when the user has
# not configured one globally; CI does exactly the same thing.
VERIFY=(git)
if [ -z "$(git config --get gpg.ssh.allowedSignersFile || true)" ] && [ -f .github/allowed_signers ]; then
    VERIFY=(git -c gpg.ssh.allowedSignersFile=.github/allowed_signers)
fi

classify() {  # -> sets SENSITIVE_OK / SENSITIVE_BAD / SKIPPED arrays
    SENSITIVE_OK=(); SENSITIVE_BAD=(); SKIPPED=()
    local sha touched status
    for sha in "${COMMITS[@]}"; do
        touched="$(git show --no-renames --name-only --format='' "$sha" | sed '/^$/d' || true)"
        if [ -z "$touched" ] || ! printf '%s\n' "$touched" | grep -qE "$SENSITIVE_REGEX"; then
            SKIPPED+=("$sha"); continue
        fi
        status="$("${VERIFY[@]}" log -1 --format='%G?' "$sha" 2>/dev/null)"
        case "$status" in
            G|U) SENSITIVE_OK+=("$sha") ;;
            *)   SENSITIVE_BAD+=("$sha|$status") ;;
        esac
    done
}

head2 "Branch"
say "  repo        $(pwd)"
say "  branch      $BRANCH"
say "  base        $BASE ($(git rev-parse --short "$MERGE_BASE"))"
say "  commits     ${#COMMITS[@]}"
if [ "$(git rev-list --merges "$MERGE_BASE..HEAD" | wc -l)" -ne 0 ]; then
    die "branch contains merge commits — this script only handles linear history"
fi

head2 "Signing configuration"
FMT="$(git config --get gpg.format || echo 'openpgp (git default)')"
KEY="$(git config --get user.signingkey || true)"
AUTOSIGN="$(git config --get commit.gpgsign || echo 'false')"
ALLOWED="$(git config --get gpg.ssh.allowedSignersFile || true)"
say "  gpg.format               ${FMT}"
say "  user.signingkey          ${KEY:-<unset>}"
say "  commit.gpgsign           ${AUTOSIGN}"
say "  gpg.ssh.allowedSignersFile ${ALLOWED:-<unset>}"

classify
head2 "Current verdict (same rule as CI)"
say "  signed + sensitive       ${#SENSITIVE_OK[@]}"
say "  UNSIGNED + sensitive     ${#SENSITIVE_BAD[@]}"
say "  not sensitive (exempt)   ${#SKIPPED[@]}"

if [ "${#SENSITIVE_BAD[@]}" -eq 0 ]; then
    head2 "Result"
    say "  Every commit touching an enforcement-sensitive path is signed."
    say "  signed-commits would pass."
    exit 0
fi

if [ "$APPLY" -eq 0 ]; then
    head2 "Result"
    say "  ${#SENSITIVE_BAD[@]} commit(s) would fail the gate. Sample:"
    printf '    %s  %%G?=%s  %s\n' \
        "$(echo "${SENSITIVE_BAD[0]}" | cut -d'|' -f1 | cut -c1-9)" \
        "$(echo "${SENSITIVE_BAD[0]}" | cut -d'|' -f2)" \
        "$(git log -1 --format='%s' "$(echo "${SENSITIVE_BAD[0]}" | cut -d'|' -f1)" | cut -c1-56)"
    say ""
    say "  Re-run with --apply to configure signing and rewrite these commits."
    say "  This rewrites every SHA from $BASE forward and needs a force-push after."
    exit 1
fi

# ── apply ───────────────────────────────────────────────────────────────────
head2 "Preflight"
[ -z "$(git status --porcelain --untracked-files=no)" ] \
    || die "working tree has uncommitted changes — commit or stash first"
say "  working tree clean"

# Resolve a signing key. Never generates one: a signing key is an identity, and
# this script does not get to mint one on the Lion's behalf.
if [ -z "$KEY" ]; then
    for cand in ~/.ssh/id_ed25519.pub ~/.ssh/id_rsa.pub; do
        [ -f "$cand" ] && { KEY="$cand"; break; }
    done
    [ -n "$KEY" ] || die "no user.signingkey set and no SSH public key found in ~/.ssh — create or configure one first"
    say "  discovered SSH key       $KEY"
fi

# SSH signing specifically, because it is the only kind CI can verify (nothing
# imports a GPG keyring into the runner).
if [ "${KEY##*.}" = "pub" ]; then
    git config gpg.format ssh
    git config user.signingkey "$KEY"
    git config gpg.ssh.allowedSignersFile .github/allowed_signers
    say "  configured SSH signing (repo-local, not --global)"
else
    say "  keeping existing GPG configuration — note CI cannot verify GPG signatures"
fi
git config commit.gpgsign true

COMMITTER_EMAIL="$(git config --get user.email || true)"
[ -n "$COMMITTER_EMAIL" ] || die "user.email is unset — the signature principal would not match anything"
if [ -f .github/allowed_signers ] && [ "${KEY##*.}" = "pub" ]; then
    if ! grep -q "^${COMMITTER_EMAIL} " .github/allowed_signers; then
        die ".github/allowed_signers has no entry for ${COMMITTER_EMAIL} — add '${COMMITTER_EMAIL} $(cut -d' ' -f1,2 "$KEY")' and commit it, or CI will still read your signatures as untrusted"
    fi
    say "  allowed_signers carries  ${COMMITTER_EMAIL}"
fi

BACKUP="backup/pre-signing/${BRANCH}-$(date +%Y%m%d-%H%M%S)"
git branch "$BACKUP" HEAD
say "  backup branch            $BACKUP"

head2 "Rewriting ${#COMMITS[@]} commit(s)"
say "  every SHA from $(git rev-parse --short "$MERGE_BASE") forward will change"
if ! GIT_EDITOR=true git rebase --exec 'git commit --amend --no-edit --no-verify -S' "$MERGE_BASE"; then
    git rebase --abort 2>/dev/null || true
    die "rebase failed — branch untouched, backup at $BACKUP"
fi
say "  rebase complete"

# ── verify ──────────────────────────────────────────────────────────────────
mapfile -t COMMITS < <(git rev-list "$MERGE_BASE..HEAD")
classify
head2 "Verification (same rule as CI)"
say "  signed + sensitive       ${#SENSITIVE_OK[@]}"
say "  UNSIGNED + sensitive     ${#SENSITIVE_BAD[@]}"
say "  not sensitive (exempt)   ${#SKIPPED[@]}"

if [ "${#SENSITIVE_BAD[@]}" -ne 0 ]; then
    say ""
    say "  Still unsigned after the rewrite:"
    for entry in "${SENSITIVE_BAD[@]}"; do
        say "    ${entry%%|*}  %G?=${entry##*|}"
    done
    die "signing did not take — restore with: git reset --hard $BACKUP"
fi

DIFF_OK=0
git diff --quiet "$BACKUP" HEAD && DIFF_OK=1

head2 "Result"
say "  All ${#SENSITIVE_OK[@]} sensitive commit(s) now verify as signed."
if [ "$DIFF_OK" -eq 1 ]; then
    say "  Tree identical to $BACKUP — signatures added, no content changed."
else
    say "  WARNING: tree differs from $BACKUP. Inspect before pushing:"
    say "    git diff $BACKUP HEAD"
fi
say ""
say "  Not pushed. The branch is already on origin, so this needs a force-push:"
say "    git push --force-with-lease origin $BRANCH"
say ""
say "  Undo at any point:"
say "    git reset --hard $BACKUP"
say ""
say "  For the 'Verified' badge on GitHub, the public half of $KEY must be"
say "  registered at Settings -> SSH and GPG keys as a SIGNING key (an"
say "  authentication key of the same bytes does not count)."
