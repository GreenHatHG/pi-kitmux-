#!/usr/bin/env python3
"""switch-pi-agent: fzf 选择并跳转到运行中的 Pi Coding Agent 所在的 Kitty Tab / Byobu Pane.

纯标准库实现：
- 进程表用单次 `ps axo` 调用获取（比 psutil 全量扫描更快且无依赖）
- cwd 只对匹配到的少量 pi 进程用 lsof 查询
- kitty socket 自动发现（KITTY_LISTEN_ON > /tmp/mykitty-* 最新），避免连死 socket 挂起
- 所有外部命令带 timeout，绝不阻塞几十秒
"""

import json
import os
import re
import subprocess
import glob

CMD_TIMEOUT = 3  # 单条外部命令超时（秒）


def run(cmd, timeout=CMD_TIMEOUT):
    try:
        return subprocess.check_output(
            cmd, text=True, stderr=subprocess.DEVNULL, timeout=timeout
        )
    except Exception:
        return ""


_working_socket = None  # 探测成功的 kitty socket，复用给 focus-tab
_title_conf = None  # (tab_title_template, tab_title_max_length)，动态缓存


def _socket_candidates():
    cands = []
    env = os.environ.get("KITTY_LISTEN_ON")
    if env:
        cands.append(env)
    # kitty listen_on unix:/tmp/mykitty 实际创建的是 /tmp/mykitty-<PID>
    cands += [
        "unix:" + s
        for s in sorted(glob.glob("/tmp/mykitty-*"), key=os.path.getmtime, reverse=True)
    ]
    cands.append("")  # kitty @ 自身的 TTY/环境探测
    return cands


def get_title_config():
    """动态读取 kitty.conf 生效的 tab_title_template / tab_title_max_length。
    用 kitty 自带解析器（kitty +runpy load_config），自动处理 include。
    注意 load_config() 无参只返回默认值，须显式传配置文件路径。"""
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
    """按 kitty 的模板渲染纯文本 tab 标题，用于 fzf 显示与 tab bar 一致。
    支持 {index}/{sup.index}/{title}/{title[:N]}/{max_title_length}；
    {fmt.*}、{bell_symbol} 等 ANSI/符号占位符在纯文本环境忽略为空。"""

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
        return ""  # fmt.* / bell_symbol / activity_symbol / tab.* / num_windows 等

    s = _RENDER_VAR.sub(sub, template)
    if maxlen and len(s) > maxlen:
        s = s[:maxlen]
    return s


def kitty_cmd(*args):
    """执行 kitty @ 子命令，自动发现可用 socket（结果缓存）。"""
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
    """kitty pane pid -> 该 pane 所在 tab 的标题信息。
    标题按 kitty.conf 的 tab_title_template 动态渲染（与 tab bar 显示一致），
    index 为 tab 在所属 OS window 内的位置（与 goto_tab N 对应）。"""
    output = kitty_cmd("ls")
    if not output:
        return {}
    try:
        data = json.loads(output)
    except Exception:
        return {}
    tpl, maxlen = get_title_config()
    panes = {}  # pid -> {tab_title, tab_id}
    for win in data:
        for idx, tab in enumerate(win.get("tabs", []), 1):
            title = tab.get("title") or "Unnamed Tab"
            info = {
                "tab_title": render_tab_title(tpl, idx, title, maxlen),
                "tab_id": tab.get("id"),
            }
            for w in tab.get("windows", []):
                if w.get("pid"):
                    panes[w["pid"]] = info
    return panes


def get_ps_table():
    """单次 ps 调用，返回 (pid_map, ppid_map)。
    pid_map: pid -> (name, cmdline)；ppid_map: pid -> ppid。"""
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
    """pid -> [pid, parent, grandparent, ...]，包含最后的终点祖先，到 launchd(1) 为止。"""
    chain, cur = [], pid
    while cur not in chain:
        chain.append(cur)
        nxt = ppid_map.get(cur)
        if nxt is None or nxt <= 1:
            break
        cur = nxt
    return chain


def get_cwd(pid):
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


def collect_agents():
    pid_map, ppid_map = get_ps_table()
    kitty_panes = get_kitty_tabs()

    # tmux client -> kitty tab 映射
    session_to_kitty = {}
    client_tty_by_tab = {}  # kitty tab_id -> 该 tab 内 tmux client 的 tty
    out = run(
        [
            "tmux",
            "list-clients",
            "-F",
            "#{client_pid}:::#{client_tty}:::#{client_session}",
        ]
    )
    for line in out.strip().splitlines():
        parts = line.split(":::")
        if len(parts) == 3:
            try:
                cpid = int(parts[0])
            except ValueError:
                continue
            for p in ancestor_chain(cpid, ppid_map):
                if p in kitty_panes:
                    session_to_kitty[parts[2]] = kitty_panes[p]
                    if parts[1]:
                        client_tty_by_tab[kitty_panes[p]["tab_id"]] = parts[1]
                    break

    # tmux pane 信息（pane_pid 索引）
    tmux_panes = {}
    out = run(
        [
            "tmux",
            "list-panes",
            "-a",
            "-F",
            "#{session_name}:::#{window_id}:::#{window_name}:::#{pane_id}:::#{pane_pid}",
        ]
    )
    for line in out.strip().splitlines():
        parts = line.split(":::")
        if len(parts) == 5:
            sess, win_id, win_name, pane_id, ppid = parts
            try:
                tmux_panes[int(ppid)] = {
                    "session": sess,
                    "win_id": win_id,
                    "win_name": win_name,
                    "pane_id": pane_id,
                }
            except ValueError:
                continue

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

        k_info = (session_to_kitty.get(t_info["session"]) if t_info else None) or next(
            (kitty_panes[p] for p in chain if p in kitty_panes), None
        )

        cwd = get_cwd(pid)
        folder = os.path.basename(cwd) if cwd != "Unknown" else "Unknown"
        session = t_info["session"] if t_info else "No Byobu"
        win_name = t_info["win_name"] if t_info else "N/A"
        tab_title = k_info["tab_title"] if k_info else "No Kitty Tab"

        agents.append(
            {
                "pid": pid,
                "display": (
                    f"{pid:<6} │ Kitty: [{tab_title:<30}] │ "
                    f"Byobu: [{session[:10]}:{win_name[:12]:<12}] │ CWD: {folder}"
                ),
                "tab_id": str(k_info["tab_id"]) if k_info else "",
                "pane_id": t_info["pane_id"] if t_info else "",
                "win_id": t_info["win_id"] if t_info else "",
                "session": session,
                "client_tty": client_tty_by_tab.get(k_info["tab_id"], "")
                if k_info
                else "",
            }
        )
    return agents


def main():
    agents = collect_agents()
    if not agents:
        print("未检测到运行中的 Pi Coding Agent。[按 Enter 退出]")
        input()
        return

    header = (
        f"{'PID':<6} │ {'Kitty Tab':<30} │ {'Byobu Window':<27} │ 工作目录\n"
        "选择 Pi Agent (↑/↓ 选择, Enter 确认跳转, Esc 取消):"
    )
    fzf_cmd = ["fzf", "--header", header, "--reverse", "--ansi", "--height=100%"]

    try:
        proc = subprocess.Popen(
            fzf_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )
        selected, _ = proc.communicate(input="\n".join(a["display"] for a in agents))
    except FileNotFoundError:
        print("未找到 fzf，请先运行: brew install fzf")
        input()
        return
    if proc.returncode != 0 or not selected or not selected.strip():
        return

    selected = selected.strip()
    target = next((a for a in agents if a["display"] == selected), None)
    if not target:
        return

    # 跳转原则：只把 *目标* 所在的 tmux client 切到目标 session/window/pane，
    # 绝不动当前 tab 里自己的 client（否则当前窗口会被拖走）。
    try:
        our_tty = os.ttyname(0)
    except OSError:
        our_tty = None

    if target["tab_id"]:
        kitty_cmd("focus-tab", "-m", f"id:{target['tab_id']}")
    elif target["session"] != "No Byobu":
        # 无 kitty tab：开一个新 tab attach，避免误点把当前 tab 内容覆盖掉。
        # 注意 kitty @ 没有 new-tab 子命令，须用 launch --type=tab。
        # 不传 --tab-title：显式标题会被 kitty 视为覆盖（title_overridden），
        # 不套 tab_title_template 且冻结不动；不传则和手动 attach 一样，
        # 标题由活动窗口自动更新并走模板渲染。
        kitty_cmd(
            "launch", "--type=tab", "tmux", "attach-session", "-t", target["session"]
        )
    if target["session"] != "No Byobu":
        t_tty = target["client_tty"]
        if target["tab_id"] and t_tty and our_tty and t_tty != our_tty:
            # 目标 client 在另一个 kitty tab：用 -c 精确切那个 client，
            # 当前 tab 里自己的 client 不受影响
            run(["tmux", "switch-client", "-c", t_tty, "-t", target["session"]])
        elif target["tab_id"]:
            # 目标就在当前 tab（t_tty == our_tty），或该 tab 内 client 未映射：
            # 切自己的 client 即可
            run(["tmux", "switch-client", "-t", target["session"]])
        # 无 kitty tab 时已新开 tab attach，不动当前 client
        if target["win_id"]:
            run(["tmux", "select-window", "-t", target["win_id"]])
        if target["pane_id"]:
            run(["tmux", "select-pane", "-t", target["pane_id"]])


if __name__ == "__main__":
    main()
