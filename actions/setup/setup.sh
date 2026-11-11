#!/usr/bin/env bash
# Extracted from mongodb-labs/drivers-github-tools@99b1cbd (upstream path: setup/setup.sh),
# then hardened. The environment it exports is unchanged.
#
# Divergence from upstream, deliberate:
#   - The secret-name normalisation loop is gone. action.yml loads the secret under a blank alias,
#     so the JSON keys arrive unprefixed already. The loop could not handle an ARN.
#   - The variables the env files are built from are checked for presence and rejected if they
#     contain a newline. docker --env-file is line-oriented: a newline in a password silently
#     truncates it and the run fails as a Garasign auth error, which is a misleading place to look.
#   - mkdir -p, so a rerun of a job does not fail on a directory that already exists.
#   - umask 077 around the env files, so they are not world-readable on a shared runner, and
#     they are written under RUNNER_TEMP, which the runner scopes to the job and cleans up.
#     /tmp remains the fallback, so behaviour off a runner is unchanged.
set -euo pipefail

# Fail on a credential that cannot survive an env file, before writing one.
reject_newline() {
  local name=$1
  case "${!name}" in
    *$'\n'*|*$'\r'*)
      echo "::error::$name contains a newline and cannot be written to a docker env file"
      exit 1 ;;
  esac
}

: "${GARASIGN_USERNAME:?missing garasign-username in $AWS_SECRET_ID}"
: "${GARASIGN_PASSWORD:?missing garasign-password in $AWS_SECRET_ID}"
: "${GPG_KEY_ID:?missing gpg-key-id in $AWS_SECRET_ID}"
reject_newline GARASIGN_USERNAME
reject_newline GARASIGN_PASSWORD

umask 077

echo "Set up envfile for garasign"
TEMP_DIR=${RUNNER_TEMP:-/tmp}
GARASIGN_ENVFILE=$TEMP_DIR/garasign-envfile
cat << EOF > "$GARASIGN_ENVFILE"
GRS_CONFIG_USER1_USERNAME=$GARASIGN_USERNAME
GRS_CONFIG_USER1_PASSWORD=$GARASIGN_PASSWORD
EOF

KONDUKTO_ENVFILE=${KONDUKTO_ENVFILE:-}
if [ -n "${KONDUKTO_TOKEN:-}" ]; then
  echo "Setting up envfile for Kondukto"
  reject_newline KONDUKTO_TOKEN
  KONDUKTO_ENVFILE=$TEMP_DIR/kondukto-envfile
  cat << EOF > "$KONDUKTO_ENVFILE"
KONDUKTO_TOKEN=${KONDUKTO_TOKEN}
EOF
fi

echo "Set up output directories"
RELEASE_ASSETS=$TEMP_DIR/release-assets
mkdir -p "$RELEASE_ASSETS"
echo "$GITHUB_RUN_ID" > "$RELEASE_ASSETS/release_run_id.txt"
S3_ASSETS=$TEMP_DIR/s3-assets
mkdir -p "$S3_ASSETS"

echo "Set up global variables"
cat <<EOF >> "$GITHUB_ENV"
AWS_BUCKET=${RELEASE_ASSETS_BUCKET:-}
GPG_KEY_ID=$GPG_KEY_ID
GPG_PUBLIC_URL=${GPG_PUBLIC_URL:-}
GARASIGN_ENVFILE=$GARASIGN_ENVFILE
KONDUKTO_ENVFILE=$KONDUKTO_ENVFILE
RELEASE_ASSETS=$RELEASE_ASSETS
S3_ASSETS=$S3_ASSETS
SECURITY_REPORT_URL=${SECURITY_REPORT_URL:-}
EOF

# --global because a caller may not have checked anything out, and a repository-local `git config`
# would fail such a job with `fatal: not in a git directory`.
echo "Set up git credentials"
git config --global user.email "167856002+mongodb-dbx-release-bot[bot]@users.noreply.github.com"
git config --global user.name "mongodb-dbx-release-bot[bot]"
