#!/usr/bin/env python3
import json
import os
import re
import subprocess
import time
import glob
from datetime import datetime

# Start of this process, used for the popup's "loaded in" readout: everything from here
# to the fzf launch (ps/lsof/tmux/kitty queries) is what opening the popup costs.
_T0 = time.monotonic()


def run(cmd, timeout=3):
    try:
        return subprocess.check_output(
            cmd, text=True, stderr=subprocess.DEVNULL, timeout=timeout
        )
    except Exception:
        return ""


_working_socket = None  # the kitty socket that worked; reuse it for focus-tab
_title_conf = None  # (tab_title_template, tab_title_max_length), cached


def _socket_candidates():
    cands = []
    env = os.environ.get("KITTY_LISTEN_ON")
    if env:
        cands.append(env)
    # kitty turns listen_on unix:/tmp/mykitty into a real file /tmp/mykitty-<PID>
    cands += [
        "unix:" + s
        for s in sorted(glob.glob("/tmp/mykitty-*"), key=os.path.getmtime, reverse=True)
    ]
    # The empty candidate makes kitty @ probe its own TTY/env — that only works when
    # we run inside kitty itself. Inside a tmux popup the TTY is the pane, so the
    # probe can never succeed yet still costs ~2s per call; skip it there.
    if not os.environ.get("TMUX"):
        cands.append("")
    return cands


def get_title_config():
    """Read the live tab_title_template / tab_title_max_length from kitty.conf.
    Use kitty's own parser (kitty +runpy load_config) so include lines work.
    load_config() with no arg gives defaults only, so pass the config path."""
    global _title_conf
    if _title_conf is not None:
        return _title_conf
    code = (
        "import os, json; from kitty.config import load_config\n"
        "base = os.environ.get('KITTY_CONFIG_DIRECTORY') or os.path.join("
        "os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'kitty')\n"
        "o = load_config(os.path.join(base, 'kitty.conf'))\n"
        "print(json.dumps({'tpl': o.tab_title_template, 'maxlen': o.tab_title_max_length}))"
    )
    out = run(["kitty", "+runpy", code], timeout=8)
    try:
        d = json.loads(out.strip().splitlines()[-1])
        _title_conf = (str(d["tpl"]) if d["tpl"] else "{title}", int(d["maxlen"] or 0))
    except Exception:
        _title_conf = ("{title}", 0)
    return _title_conf


_RENDER_VAR = re.compile(r"\{([^{}]*)\}")


def render_tab_title(template, index, title, maxlen=0):
    """Render a plain-text tab title the way kitty does, so fzf matches the tab bar.
    Handles {index}/{sup.index}/{title}/{title[:N]}/{max_title_length};
    ANSI/symbol slots like {fmt.*} and {bell_symbol} become empty in plain text."""

    def sub(m):
        expr = m.group(1).strip()
        if expr in ("index", "sup.index"):
            return str(index)
        if expr == "title":
            return title
        m2 = re.fullmatch(r"title\[:\s*(\d+)\s*\]", expr)
        if m2:
            return title[: int(m2.group(1))]
        if expr == "max_title_length":
            return str(maxlen or 0)
        return ""  # fmt.* / bell_symbol / activity_symbol / tab.* / num_windows, etc.

    s = _RENDER_VAR.sub(sub, template)
    if maxlen and len(s) > maxlen:
        s = s[:maxlen]
    return s


def kitty_cmd(*args):
    """Run a kitty @ subcommand, finding a working socket (the result is cached)."""
    global _working_socket
    if _working_socket is not None:
        return run(
            ["kitty", "@"]
            + ([f"--to={_working_socket}"] if _working_socket else [])
            + list(args)
        )
    tried = set()
    for sock in _socket_candidates():
        if sock in tried:
            continue
        tried.add(sock)
        out = run(["kitty", "@"] + ([f"--to={sock}"] if sock else []) + list(args))
        if out:
            _working_socket = sock
            return out
    _working_socket = ""
    return ""


def get_kitty_tabs():
    """kitty pane pid -> that pane's tab title info, plus the raw tab list.

    The title is rendered from kitty.conf's tab_title_template (same as the tab bar)
    for display. index is the tab's spot inside its OS window, matching goto_tab N.
    Also returns the per-tab raw titles (unescaped, NOT truncated — truncation only
    happens in the tab bar render) keyed by tab_id: the ⚑host:session tag lives in
    the raw title, and that is how remote sessions map to tabs (pids cannot be
    compared across machines).
    """
    output = kitty_cmd("ls")
    if not output:
        return {}, {}
    try:
        data = json.loads(output)
    except Exception:
        return {}, {}
    tpl, maxlen = get_title_config()
    n_windows = len(data)
    panes = {}  # pid -> {tab_title, tab_id, tab_index, win_index, n_windows}
    tabs_by_id = {}  # tab_id -> {raw_title, tab_index, win_index, n_windows}
    for win_idx, win in enumerate(data, 1):
        for idx, tab in enumerate(win.get("tabs", []), 1):
            title = tab.get("title") or "Unnamed Tab"
            tabs_by_id[tab.get("id")] = {
                "raw_title": title,
                "tab_index": idx,
                "win_index": win_idx,
                "n_windows": n_windows,
            }
            info = {
                "tab_title": render_tab_title(tpl, idx, title, maxlen),
                "tab_id": tab.get("id"),
                "tab_index": idx,
                "win_index": win_idx,
                "n_windows": n_windows,
            }
            for w in tab.get("windows", []):
                if w.get("pid"):
                    panes[w["pid"]] = info
    return panes, tabs_by_id


# tmux list-panes field separator. Not ":::" — users can rename a window at any
# time, and a name with ":::" would silently drop the whole line, hiding an agent
# from the picker.
_PANE_SEP = "\x1f"
_PANE_FIELDS = 13
# Control chars that would break the fzf line and shift --accept-nth row numbers.
_PANE_CTRL = re.compile(r"[\x00-\x1f\x7f]")

# tmux 3.5+ escapes control chars in a format's OUTPUT: our \x1f separator comes
# back as the 4 literal characters \037, a newline as \n, a real backslash doubled.
# So the parser must undo that escaping before splitting on \x1f (a no-op for older
# tmux versions that emit the raw separator byte).
_TMUX_ESCAPES = {"\\": "\\", "n": "\n", "r": "\r", "t": "\t", "f": "\f", "e": "\x1b", "s": " "}
_TMUX_ESCAPE = re.compile(r"\\(?:([0-7]{3})|(.))")


def _tmux_unescape(text):
    def sub(m):
        octal, ch = m.group(1), m.group(2)
        if octal:
            return chr(int(octal, 8))
        # An unknown code keeps its char; the backslash is dropped
        return _TMUX_ESCAPES.get(ch, ch)

    return _TMUX_ESCAPE.sub(sub, text)


def parse_panes(output):
    """Parse `tmux list-panes` output into pane_pid -> window info.

    Fields line up cell by cell with the tmux.conf status bar. Records are
    newline-separated, but a window name is user-editable and may hold a newline,
    and `splitlines()` would split one row into two and shift the fields.
    So the query format also ends with a separator; here we undo tmux's backslash
    escaping, split on the separator to get all fields, then regroup them 13 at a
    time: a newline inside a window name stays inside its field (and is scrubbed by
    _PANE_CTRL), so grouping stays aligned.
    """
    # Unescape the WHOLE output first: tmux 3.5+ turned our \x1f separators into
    # literal \037, so there is no real \x1f to split on until after unescaping
    fields = [_PANE_CTRL.sub("", field) for field in _tmux_unescape(output).split(_PANE_SEP)]
    panes = {}
    for i in range(0, len(fields) - _PANE_FIELDS + 1, _PANE_FIELDS):
        (
            sess,
            win_id,
            win_idx,
            win_name,
            pane_cmd,
            pi_running,
            pi_done,
            n_wins,
            fmt,
            pane_path,
            pane_id,
            ppid,
            done_at,
        ) = fields[i : i + _PANE_FIELDS]
        try:
            panes[int(ppid)] = {
                "session": sess,
                "win_id": win_id,
                "win_idx": int(win_idx),
                "win_name": win_name,
                "pane_cmd": pane_cmd,
                "pi_running": pi_running == "1",
                "pi_done": pi_done == "1",
                "sess_win_count": int(n_wins),
                "fmt": fmt,
                "pane_path": pane_path,
                "pane_pid": int(ppid),
                "pane_id": pane_id,
                # Raw "@pi_done_at" (epoch seconds): keep the string and let
                # format_done_at() decide whether it is usable, matching the lazy
                # "pi_running == '1'" style above.
                "done_at": done_at,
            }
        except ValueError:
            continue
    return panes


def get_ps_table():
    """One ps call returns (pid_map, ppid_map).
    pid_map: pid -> (name, cmdline); ppid_map: pid -> ppid."""
    out = run(["ps", "axo", "pid=,ppid=,command="], timeout=5)
    pid_map, ppid_map = {}, {}
    for line in out.splitlines():
        line = line.lstrip()
        parts = line.split(None, 2)
        if len(parts) == 3:
            try:
                pid, ppid, cmd = int(parts[0]), int(parts[1]), parts[2]
            except ValueError:
                continue
            name = cmd.split()[0].rsplit("/", 1)[-1].lower()
            pid_map[pid] = (name, cmd)
            ppid_map[pid] = ppid
    return pid_map, ppid_map


def ancestor_chain(pid, ppid_map):
    """pid -> [pid, parent, grandparent, ...], ending at launchd(1)."""
    chain, cur = [], pid
    while cur not in chain:
        chain.append(cur)
        nxt = ppid_map.get(cur)
        if nxt is None or nxt <= 1:
            break
        cur = nxt
    return chain


def get_cwd(pid):
    # /proc first: instant, and no lsof needed (some boxes have none); Mac has no
    # /proc so it falls through to lsof as before
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        pass
    out = run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], timeout=2)
    for line in out.splitlines():
        if line.startswith("n/"):
            return line[1:]
    return "Unknown"


def is_pi(name, cmdline):
    if "switch-pi-agent" in cmdline:
        return False
    c = cmdline.strip()
    return (
        "pi-coding-agent" in cmdline or name == "pi" or c == "pi" or c.startswith("pi ")
    )


# The cross-machine identity tag: tmux.conf's set-titles-string injects ⚑host:session,
# mosh syncs the title to the Mac kitty tab, and the picker matches it there — pids
# cannot be compared across machines, the title is the only channel that crosses.
_TITLE_TAG = "⚑"


def map_sessions_by_title(tabs_by_id):
    """tmux session name -> kitty tab info, matched via the ⚑host:session title tag.

    Matching runs on the RAW title (kitty ls does not truncate it; only the tab bar
    render does). The tag sits at the END of set-titles-string, so an over-long tab
    bar title drops the tag first and shows the clean "⏳N repo" part; the raw title
    keeps it, so match anywhere in the title. The tag's host is cut to 6 chars to
    keep the tag short, so it is checked as a PREFIX of this machine's hostname
    (full equality would never match the truncated tag). Spoofing via a repo name
    containing the tag char is still blocked: the fake tag's host would have to be
    a prefix of this machine's hostname to pass.
    """
    import re

    tag_re = re.compile(rf"{re.escape(_TITLE_TAG)}([^\s]+)")
    my_host = os.uname().nodename.split(".")[0]
    by_session = {}
    for tab in tabs_by_id.values():
        m = tag_re.search(tab["raw_title"])
        if m:
            # 'host:session' — host disambiguates two VPSes running the same session
            # name; the local host wins so picker rows point at this machine's tab.
            # Prefix match: the title only carries the first 6 chars of the hostname.
            host, _, sess = m.group(1).partition(":")
            if host and not my_host.startswith(host):
                continue
            key = sess or m.group(1)
            if key not in by_session or tab["tab_index"] < by_session[key]["tab_index"]:
                by_session[key] = tab
    return by_session


def collect_agents():
    pid_map, ppid_map = get_ps_table()
    kitty_panes, tabs_by_id = get_kitty_tabs()
    session_by_title = map_sessions_by_title(tabs_by_id)

    # tmux client tty per session (for jump_to's targeted switch-client). The tty is
    # a VPS-local fact from list-clients; which kitty tab each session lives in comes
    # from the title tag above (remote) or the pid ancestry below (same machine).
    client_tty_by_session = {}
    out = run(
        [
            "tmux",
            "list-clients",
            "-F",
            "#{client_tty}:::#{client_session}",
        ]
    )
    for line in out.strip().splitlines():
        parts = line.split(":::")
        if len(parts) == 2 and parts[0]:
            client_tty_by_session[parts[1]] = parts[0]

    # tmux pane info, indexed by pane_pid. State comes from pane-level @pi_running
    # / @pi_done (not the window-level @pi_win view), so two panes in one window
    # can show their own state. @pi_done_at is the completion time the picker prints
    # next to ✅.
    # Also grab @pi_win_fmt (name) and pane_current_path (for the git-based place/name).
    # Note `#()` does not run inside list-panes: the name part of @pi_win_fmt, @pi_repo,
    # and @pi_where are all empty there (all come from `#()`), so win_label() /
    # pane_location() call the same helper again to fill in place and project name.
    # An enclave window even needs the name filled in.
    out = run(
        [
            "tmux",
            "list-panes",
            "-a",
            "-F",
            _PANE_SEP.join(
                (
                    "#{session_name}",
                    "#{window_id}",
                    "#{window_index}",
                    "#{window_name}",
                    "#{pane_current_command}",
                    "#{@pi_running}",
                    "#{@pi_done}",
                    "#{session_windows}",
                    "#{E:@pi_win_fmt}",
                    "#{pane_current_path}",
                    "#{pane_id}",
                    "#{pane_pid}",
                    "#{@pi_done_at}",
                )
            )
            # The trailing separator puts the record-boundary newline at the start
            # of the next field, so parse_panes can cut every 13 fields; see parse_panes.
            + _PANE_SEP,
        ]
    )
    tmux_panes = parse_panes(out)

    agents, seen = [], set()
    for pid, (name, cmdline) in pid_map.items():
        if not is_pi(name, cmdline):
            continue
        chain = ancestor_chain(pid, ppid_map)
        t_info = next((tmux_panes[p] for p in chain if p in tmux_panes), None)
        pane_key = t_info["pane_id"] if t_info else f"proc-{pid}"
        if pane_key in seen:
            continue
        seen.add(pane_key)

        # Remote (title tag) first: it also works when the client's pid ancestry is
        # on another machine; pid matching stays as the same-machine fallback
        k_info = (session_by_title.get(t_info["session"]) if t_info else None) or next(
            (kitty_panes[p] for p in chain if p in kitty_panes),
            None,
        )

        cwd = get_cwd(pid)
        folder = os.path.basename(cwd) if cwd != "Unknown" else "Unknown"
        where, repo = pane_location(t_info["pane_path"]) if t_info else ("", "")
        session = t_info["session"] if t_info else "No Byobu"
        win_name = t_info["win_name"] if t_info else "N/A"
        tab_title = k_info["tab_title"] if k_info else "No Kitty Tab"

        agents.append(
            {
                "pid": pid,
                "folder": folder,
                "session": session,
                "win_name": win_name,
                "tab_title": tab_title,
                "tab_id": str(k_info["tab_id"]) if k_info else "",
                "tab_index": k_info["tab_index"] if k_info else 0,
                "win_index": k_info["win_index"] if k_info else 0,
                "win_count": k_info["n_windows"] if k_info else 1,
                "win_idx": t_info["win_idx"] if t_info else 0,
                "sess_win_count": t_info["sess_win_count"] if t_info else 1,
                "pane_cmd": t_info["pane_cmd"] if t_info else "",
                "pi_running": t_info["pi_running"] if t_info else False,
                "pi_done": t_info["pi_done"] if t_info else False,
                "done_at": t_info["done_at"] if t_info else "",
                "fmt": t_info["fmt"] if t_info else "",
                "where": where,
                "repo": repo,
                "pane_tmux_pid": t_info["pane_pid"] if t_info else 0,
                "pane_id": t_info["pane_id"] if t_info else "",
                "win_id": t_info["win_id"] if t_info else "",
                "client_tty": client_tty_by_session.get(t_info["session"], "")
                if t_info
                else "",
            }
        )
    return agents


# Match tmux.conf's window-status-format: the status marker and window label share
# yellow (⏳/✅ are emoji, so the real color comes from the font; this only matches the
# status bar's #[fg=yellow]).
_TMUX_HELPER = os.path.expanduser("~/.local/bin/tmux-pane-command.py")
_REPO_HELPER = os.path.expanduser("~/.local/bin/tmux-pane-repo.py")
_ANSI_RESET = "\x1b[0m"
_ANSI_YELLOW = "\x1b[33m"
_ANSI_DIM = "\x1b[2m"


def pane_location(pane_path):
    """Get a pane's (place, project): `wt:5` / `main` plus the repo name; ("", "") outside git.

    tmux `#()` only runs in persistent format strings like the status bar or a title,
    so list-panes sees an empty @pi_loc. Call the same helper here (one source of
    truth) instead of copying the git rules. If the helper fails (not deployed, etc.)
    we also fall back to "", so the picker just loses a @place.
    """
    out = run([_REPO_HELPER, pane_path]).strip()
    repo, sep, where = out.partition("/")
    if not sep:
        return "", ""
    return where, repo


def win_label(agent):
    """Window label, matching the tmux status bar @pi_win_fmt (name@place).

    Name: tmux already rendered it (with the 18-column cut and pane_in_mode's #W),
    so use it. In enclave windows `#()` does not run in list-panes and the name is
    empty, so call tmux-pane-command.py for the real app name; if that fails, fall
    back to the window name.
    Place: `#()` also does not run, so the place part is always empty in list-panes;
    pane_location() calls the same helper again (rules not copied). Outside git
    there is no place, so the label is just the name.
    """
    name = agent["fmt"]
    if not name and agent["pane_cmd"] == "enclave":
        name = run([_TMUX_HELPER, str(agent["pane_tmux_pid"]), "enclave"]).strip()
    name = name or agent["win_name"]
    where = agent["where"]
    return f"{name}@{where}" if where else name


def cwd_suffix(folder, label, repo="", has_tab=False):
    """Return the cwd note at the end of a picker row, skipping a repeated project name.

    `repo` comes from pane_location() -> tmux-pane-repo.py, the same source as the
    kitty tab title's @pi_repo; we never parse the project name back out of the
    rendered title. With no kitty tab there is no parent to dedupe against, so only
    the worktree rule applies and the cwd stays.
    """
    location = label.rsplit("@", 1)[1] if "@" in label else ""
    if location.startswith("wt:") and location[3:] == folder:
        return ""
    if has_tab and repo and folder == repo:
        return ""
    return f"cwd:{folder}"


def pane_marker(agent):
    """Pane-level three-state marker: ⏳ running / ✅ done and unseen / empty.

    Read pane-level @pi_running / @pi_done, not the window-level @pi_win roll-up: if
    one pane runs and another is done, the window view only shows ⏳ and hides the done one.
    """
    if agent.get("pi_running"):
        return "⏳ "
    if agent.get("pi_done"):
        return "✅ "
    return ""


def format_done_at(value, now=None):
    """Render @pi_done_at (epoch seconds) as the local completion time.

    Same local day -> "HH:MM:SS", another day -> "MM-DD HH:MM"; anything missing or
    unparsable -> "". The picker shows this next to ✅ so an unseen entry tells you
    when its run finished.
    """
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    done = datetime.fromtimestamp(ts)
    reference = now or datetime.now()
    if (done.year, done.month, done.day) == (
        reference.year,
        reference.month,
        reference.day,
    ):
        return done.strftime("%H:%M:%S")
    return done.strftime("%m-%d %H:%M")


def format_child(agent, show_session=True):
    """Picker child row: locate context, marker, and name@place.

    Returns (text, ansi): text has no color (for tests and match-back), ansi goes to
    fzf --ansi. When the kitty tab group already shows the session in its parent,
    show_session is False and the child keeps only the needed window number; groups
    with no kitty tab keep the session as context. The window number shows only when
    a session has more than one window, matching tmux.conf's
    `#{?#{e|>:#{session_windows},1},#I: ,}`.
    """
    index = f"{agent['win_idx']}: " if agent["sess_win_count"] > 1 else " "
    session = f"{agent['session']}:" if show_session else ""
    prefix = f"   └ {agent['pid']:<6} {session}{index}"
    marker = pane_marker(agent)
    label = win_label(agent)
    cwd = cwd_suffix(
        agent["folder"],
        label,
        agent.get("repo", ""),
        bool(agent.get("tab_id")),
    )
    cwd_column = f"  {cwd}" if cwd else ""
    # Only a done-and-not-running pane has a completion time; ⏳ hides a done flag,
    # so the time stays off running rows. No timestamp (old state) just shows ✅.
    done_at = (
        format_done_at(agent.get("done_at", ""))
        if agent.get("pi_done") and not agent.get("pi_running")
        else ""
    )
    done_column = f" ({done_at})" if done_at else ""
    text = f"{prefix}{marker}{label}{cwd_column}{done_column}"
    ansi = prefix
    if marker:
        ansi += f"{_ANSI_YELLOW}{marker}{_ANSI_RESET}"
    ansi += label + cwd_column
    if done_column:
        ansi += f"{_ANSI_DIM}{done_column}{_ANSI_RESET}"
    return text, ansi


def build_rows(agents):
    """Turn the agent list into rows grouped by kitty tab (group header + indented children).

    Returns list[dict], each with:
    - kind: "tab" (header) or "agent" (child)
    - text: plain text, used to match the fzf output exactly
    - ansi: colored text, fed to fzf --ansi
    - tab_id: the kitty tab (empty if none)
    - agent: only on children, points at the original agent dict
    The header shows "tab title · session" (with a `[win N]` prefix when there are
    several OS windows); groups are ordered by (OS window, tab index), and the
    group with no kitty tab goes last.
    """
    groups = {}
    for a in agents:
        if a["tab_id"]:
            key = ("tab", a["tab_id"])
            order = (a["win_index"], a["tab_index"])
            title = a["tab_title"]
        else:
            key = ("none", "")
            order = (10**9, 10**9)
            title = "No Kitty Tab"
        g = groups.setdefault(
            key, {"order": order, "tab_id": a["tab_id"], "title": title, "agents": []}
        )
        g["agents"].append(a)

    rows = []
    for grp in sorted(groups.values(), key=lambda item: item["order"]):
        header = grp["title"]
        first = grp["agents"][0]
        if grp["tab_id"]:
            header = f"{header} · {first['session']}"
            if first["win_count"] > 1:
                header = f"[win {first['win_index']}] {header}"
        rows.append(
            {
                "kind": "tab",
                "text": header,
                "ansi": f"\x1b[1;36m{header}\x1b[0m",
                "tab_id": grp["tab_id"],
                "agent": None,
            }
        )
        # Within a group: unseen (✅) first, then running (⏳), then idle; same-state
        # rows go by window index (matching the tab bar left to right, not window
        # name order). Group order stays as-is, still mirroring the kitty tab bar.
        for a in sorted(
            grp["agents"],
            key=lambda item: (
                item["session"],
                not item["pi_done"],
                not item["pi_running"],
                item["win_idx"],
                item["pid"],
            ),
        ):
            text, ansi = format_child(a, show_session=not bool(grp["tab_id"]))
            rows.append(
                {
                    "kind": "agent",
                    "text": text,
                    "ansi": ansi,
                    "tab_id": a["tab_id"],
                    "agent": a,
                }
            )
    return rows


def jump_to(target, our_tty):
    """Jump rule: switch only the *target's* tmux client to the target session/window/pane.
    Never touch our own client in the current tab, or the current window would move."""
    if target["tab_id"]:
        kitty_cmd("focus-tab", "-m", f"id:{target['tab_id']}")
    elif target["session"] != "No Byobu":
        # No kitty tab: open a new tab and attach, so a misclick can't overwrite the
        # current tab.
        # kitty @ has no new-tab subcommand, so use launch --type=tab.
        # Do not pass --tab-title: an explicit title makes kitty treat it as overridden
        # (title_overridden), skipping tab_title_template and freezing it; leaving it
        # out lets the title follow the active window through the template, like a
        # manual attach.
        kitty_cmd(
            "launch", "--type=tab", "tmux", "attach-session", "-t", target["session"]
        )
    if target["session"] != "No Byobu":
        t_tty = target["client_tty"]
        if target["tab_id"] and t_tty and our_tty and t_tty != our_tty:
            # Target client is in another kitty tab: use -c to switch just that client;
            # our own client in this tab is untouched.
            run(["tmux", "switch-client", "-c", t_tty, "-t", target["session"]])
        elif target["tab_id"]:
            # Target is in this tab (t_tty == our_tty), or the tab's client is
            # unmapped: switching our own client is enough.
            run(["tmux", "switch-client", "-t", target["session"]])
        # With no kitty tab we already opened a new tab, so leave the current client alone.
        if target["win_id"]:
            run(["tmux", "select-window", "-t", target["win_id"]])
        if target["pane_id"]:
            run(["tmux", "select-pane", "-t", target["pane_id"]])


def main():
    agents = collect_agents()
    if not agents:
        print("No running Pi Coding Agent found. [Press Enter to exit]")
        input()
        return

    rows = build_rows(agents)
    running = sum(1 for a in agents if a["pi_running"])
    done = sum(1 for a in agents if a["pi_done"])
    # Everything up to here is what opening the popup costs (process start -> fzf): ps / lsof /
    # tmux / kitty queries. Show it in the header so a slow open is visible, not just felt.
    elapsed = time.monotonic() - _T0
    # Keep the header on two lines: a single long line gets clipped by fzf on narrow
    # popups, and the tail ("loaded in …") is exactly what you want to see when opening.
    header = (
        "Pick a Pi Agent (↑/↓ select, Enter jump, Esc cancel)\n"
        f"⏳{running} running · ✅{done} unseen · grouped by Kitty tab"
        f" · loaded in {elapsed:.2f}s"
    )
    # On Enter, --accept-nth='{n}' returns the row's index in the input (with --no-sort
    # that is the rows index). So even two tabs with the same title still land on the
    # exact row picked, without using display text as a unique key.
    fzf_cmd = [
        "fzf",
        "--header",
        header,
        "--reverse",
        "--ansi",
        "--no-sort",
        "--accept-nth={n}",
        "--height=100%",
    ]

    try:
        proc = subprocess.Popen(
            fzf_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )
        selected, _ = proc.communicate(input="\n".join(r["ansi"] for r in rows))
    except FileNotFoundError:
        print("fzf not found. Run: brew install fzf")
        input()
        return
    if proc.returncode != 0 or not selected or not selected.strip():
        return

    try:
        row = rows[int(selected.strip())]
    except (ValueError, IndexError):
        return

    # Group header: just focus that kitty tab; a "No Kitty Tab" header does nothing.
    if row["kind"] == "tab":
        if row["tab_id"]:
            kitty_cmd("focus-tab", "-m", f"id:{row['tab_id']}")
        return

    try:
        our_tty = os.ttyname(0)
    except OSError:
        our_tty = None
    jump_to(row["agent"], our_tty)


if __name__ == "__main__":
    main()
