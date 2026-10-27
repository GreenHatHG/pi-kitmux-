#!/usr/bin/env bash
# Rebuild this environment on a fresh Debian from GitHub + this repo.
#   1. install git + zsh, clone pi-kitmux (ssh key or https+token first)
#   2. run THIS script
# Secrets (ssh key, OCTPUS/TAVILY/FIRECRAWL api keys) are NOT in git --
# transfer them once by hand, or re-issue them.
set -uo pipefail

REPO_URL="https://github.com/GreenHatHG/pi-kitmux-.git"
REPO="$HOME/projects/pi-kitmux"
DF="$REPO/dotfiles"

step() { [ -f "/tmp/.restore-done-$1" ] && { echo "== $1: done, skip"; return 1; } || return 0; }
mark() { touch "/tmp/.restore-done-$1"; }

########################################
echo ">>> step 1/6 apt packages"
if step apt; then
  export DEBIAN_FRONTEND=noninteractive
  # third-party repos added by command (nothing stored in git): signing key via
  # curl|gpg, then one echo per repo. URLs embed the signed-by path.
  sudo install -d /usr/share/keyrings
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    | sudo gpg --dearmor -o /usr/share/keyrings/nodesource.gpg
  echo "deb [signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
    | sudo tee /etc/apt/sources.list.d/nodesource.sources >/dev/null
  curl -fsSL 'https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x7e51d1ad956055ca' \
    | sudo gpg --dearmor -o /usr/share/keyrings/trzsz.gpg
  echo "deb [signed-by=/usr/share/keyrings/trzsz.gpg] https://ppa.launchpadcontent.net/trzsz/ppa/ubuntu jammy main" \
    | sudo tee /etc/apt/sources.list.d/trzsz.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y zsh
  # locale for prompt rendering (command, not a packed file)
  sudo sed -i 's/^# *\(en_US.UTF-8 UTF-8\)/\1/' /etc/locale.gen && sudo locale-gen
  # old box was a GCE instance: this list is already scrubbed of gce-/google-
  # packages, but anything else that misses lands in a log, not a hard stop
  xargs -a "$DF/apt/packages.txt" -d '\n' sudo apt-get install -y -q \
    >/tmp/.apt-fail.log 2>&1 || echo "!! some packages failed, see /tmp/.apt-fail.log"
  # pi itself (the only npm global that matters)
  sudo npm install -g @earendil-works/pi-coding-agent \
    || echo "!! failed: npm i -g @earendil-works/pi-coding-agent"
  mark apt
fi

########################################
echo ">>> step 2/6 toolchains: bun, pnpm, uv"
if step toolchains; then
  curl -fsSL https://bun.sh/install | bash || echo "!! bun install failed"
  corepack enable pnpm 2>/dev/null || sudo npm install -g pnpm
  curl -LsSf https://astral.sh/uv/install.sh | sh || echo "!! uv install failed"
  ~/.local/bin/uv python install 3.12 3.14 || true
  mark toolchains
fi

########################################
echo ">>> step 3/6 secrets placeholders + deploy links"
if step deploy; then
  # the gitignored secrets file that .zshrc sources; fill in real values after
  mkdir -p ~/.config/pi-kitmux
  [ -f ~/.config/pi-kitmux/secrets.env ] || cat > ~/.config/pi-kitmux/secrets.env <<'EOF'
# fill in (or scp from the old box): octopus gateway + search API keys
# NOTE: keep the 'export' prefix -- without it pi can't see the vars
export OCTPUS_API_KEY=
export TAVILY_API_KEY=
export FIRECRAWL_API_KEY=
EOF
  chmod 600 ~/.config/pi-kitmux/secrets.env
  # deploy.sh builds ~/.zshrc from the omz template + zshrc.pre, links custom.zsh,
  # pi configs and byobu (p10k is wizard-regenerated, see notes/)
  "$REPO/scripts/deploy.sh"
  mark deploy
fi

########################################
echo ">>> step 4/6 oh-my-zsh + plugins (all at HEAD, plain git clone)"
if step omz; then
  git clone --depth=1 https://github.com/ohmyzsh/ohmyzsh.git ~/.oh-my-zsh
  git clone --depth=1 https://github.com/Aloxaf/fzf-tab \
    ~/.oh-my-zsh/custom/plugins/fzf-tab
  git clone --depth=1 https://github.com/zsh-users/zsh-autosuggestions \
    ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions
  git clone --depth=1 https://github.com/zsh-users/zsh-syntax-highlighting \
    ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting
  git clone --depth=1 https://github.com/romkatv/powerlevel10k.git \
    ~/.oh-my-zsh/custom/themes/powerlevel10k
  mark omz
fi

########################################
echo ">>> step 5/6 pi packages"
if step pi; then
  # clone the local-path packages first (settings.json references them by path)
  mkdir -p ~/projects && cd ~/projects
  for r in pi-extension-bash-guard pi-extension-tmux-subagent pi-extension-tool-token-est pi-watchdog; do
    [ -d "$r" ] || git clone --depth=1 "https://github.com/GreenHatHG/$r.git" || echo "!! clone failed: $r"
  done
  # maplezzk-pi-extensions: no GitHub remote yet -- see the manual notes below
  pi install npm:@oipsanthony/pi-command-history || echo '!! failed: pi-command-history'
  pi install npm:@diegopetrucci/pi-context-inspector || echo '!! failed: pi-context-inspector'
  pi install npm:@palmtom/pi-models || echo '!! failed: pi-models'
  pi install git:github.com/rays1d/pi-custom-header || echo '!! failed: pi-custom-header'
  pi install ~/projects/pi-extension-bash-guard || echo '!! failed: bash-guard'
  pi install ~/projects/pi-extension-tmux-subagent || echo '!! failed: tmux-subagent'
  pi install ~/projects/pi-extension-tool-token-est || echo '!! failed: tool-token-est'
  pi install ~/projects/pi-watchdog || echo '!! failed: pi-watchdog'
  mark pi
fi

########################################
echo ">>> step 6/6 sanity checks"
if step check; then
  sudo chsh -s "$(command -v zsh)" "$USER" || true
  zsh -c 'source ~/.zshrc >/dev/null 2>&1; echo zsh-ok'
  tmux -V
  byobu --version >/dev/null && echo byobu-ok
  pi --version 2>/dev/null && echo pi-ok || echo '!! pi not found'
  bun --version 2>/dev/null; uv --version 2>/dev/null
  "$DF/bin/check-deployed.sh"
  mark check
fi

########################################
cat <<'EOF'

Manual leftovers (not in git, by design):
- ~/.ssh keys: fresh keypair is cleaner (add pub key to GitHub); or scp the old one
- API keys: fill ~/.config/pi-kitmux/secrets.env (octopus / tavily / firecrawl)
- maplezzk-pi-extensions (pi-metrics): still has no git remote -- push it to a
  private repo, or rsync it from the old box before decommissioning
- p10k: run `p10k configure` once; choices are in dotfiles/notes/p10k-wizard-choices.txt
EOF
echo "done. log in with zsh, run byobu."
