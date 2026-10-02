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
    n_windows = len(data)
    panes = {}  # pid -> {tab_title, tab_id, tab_index, win_index, n_windows}
    for win_idx, win in enumerate(data, 1):
        for idx, tab in enumerate(win.get("tabs", []), 1):
            title = tab.get("title") or "Unnamed Tab"
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
    return panes


# tmux list-panes 输出分隔符。不用 ":::"：窗口名用户可随手改（, 重命名），
# 含 ":::" 会让整行被静默丢弃、agent 从 picker 里消失。
_PANE_SEP = "\x1f"
_PANE_FIELDS = 12
# 会撑破 fzf 行、让 --accept-nth 行号错位的控制字符
_PANE_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def parse_panes(output):
    """解析 `tmux list-panes` 输出，返回 pane_pid -> 窗口信息。

    字段与 tmux.conf 的状态栏逐格对齐。记录之间是换行，但窗口名可被用户
    随意改写、可能含换行，`splitlines()` 会把一行撑成两行导致字段错位。
    所以查询格式在末尾也多带一个分隔符，这里直接按分隔符切出全部字段、
    再每 12 个一组重组：窗口名里的换行会留在字段内部（随后被 _PANE_CTRL
    抹掉），不会影响分组对齐。
    """
    fields = [_PANE_CTRL.sub("", field) for field in output.split(_PANE_SEP)]
    panes = {}
    for i in range(0, len(fields) - _PANE_FIELDS + 1, _PANE_FIELDS):
        (
            sess,
            win_id,
            win_idx,
            win_name,
            pane_cmd,
            pi_win,
            bell,
            act,
            n_wins,
            fmt,
            pane_id,
            ppid,
        ) = fields[i : i + _PANE_FIELDS]
        try:
            panes[int(ppid)] = {
                "session": sess,
                "win_id": win_id,
                "win_idx": int(win_idx),
                "win_name": win_name,
                "pane_cmd": pane_cmd,
                "pi_win": pi_win,
                "bell": bell == "1",
                "act": act == "1",
                "sess_win_count": int(n_wins),
                "fmt": fmt,
                "pane_pid": int(ppid),
                "pane_id": pane_id,
            }
        except ValueError:
            continue
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

    # tmux pane 信息（pane_pid 索引）。字段与 tmux.conf 的状态栏逐格对齐：
    # 窗口序号 / @pi_win（⏳✅）/ bell·activity / @pi_win_fmt（名称@位置）。
    # 注意 `#()` 在 list-panes 里不执行，enclave 窗口的 @pi_win_fmt 只剩 "@位置"，
    # 真实应用名由 win_label() 另调 helper 补上。
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
                    "#{@pi_win}",
                    "#{window_bell_flag}",
                    "#{window_activity_flag}",
                    "#{session_windows}",
                    "#{E:@pi_win_fmt}",
                    "#{pane_id}",
                    "#{pane_pid}",
                )
            )
            # 末尾分隔符让记录边界的换行落在下一字段开头，parse_panes 才能按
            # 固定 12 个一组切开；详见 parse_panes 文档
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
                "pi_win": t_info["pi_win"] if t_info else "",
                "bell": t_info["bell"] if t_info else False,
                "act": t_info["act"] if t_info else False,
                "fmt": t_info["fmt"] if t_info else "",
                "pane_tmux_pid": t_info["pane_pid"] if t_info else 0,
                "pane_id": t_info["pane_id"] if t_info else "",
                "win_id": t_info["win_id"] if t_info else "",
                "client_tty": client_tty_by_tab.get(k_info["tab_id"], "")
                if k_info
                else "",
            }
        )
    return agents


# 与 tmux.conf 的 window-status-format 保持一致：窗口标签黄、bell ◉ 红、activity ● 青
_TMUX_HELPER = os.path.expanduser("~/.local/bin/tmux-pane-command.py")
_ANSI_RESET = "\x1b[0m"
_ANSI_YELLOW = "\x1b[33m"
_ANSI_RED = "\x1b[31m"
_ANSI_CYAN = "\x1b[36m"


def win_label(agent):
    """窗口标签，与 tmux 状态栏 @pi_win_fmt 的渲染结果一致（名称@位置）。

    非 enclave 窗口：tmux 已经渲染好（含 18 列截断与 @pi_where），直接采用，
    不在 Python 里重抄一遍 @pi_where / 截断逻辑，避免两处漂移。
    enclave 窗口：`#()` 在 list-panes 里不执行，@pi_win_fmt 只剩 "@位置"，
    故在此实调 tmux-pane-command.py 取真实应用名补到 "@" 之前；
    取不到时退回窗口名（比硬编码 "enclave" 更有信息量）。
    """
    fmt = agent["fmt"]
    if agent["pane_cmd"] != "enclave" or "@" not in fmt:
        return fmt  # 含 pane_in_mode 时 @pi_win_fmt 就是 #W，无位置的边角情况
    name = run([_TMUX_HELPER, str(agent["pane_tmux_pid"]), "enclave"]).strip()
    return f"{name or agent['win_name']}@{fmt.split('@', 1)[1]}"


def format_child(agent):
    """picker 子行：与 tmux 状态栏同一套「序号 + 标记 + 名称@位置 + 灯」。

    返回 (text, ansi)：text 无色（供测试与回匹配），ansi 喂给 fzf --ansi。
    序号前缀只在 session 多于一个窗口时显示，与 tmux.conf 的
    `#{?#{e|>:#{session_windows},1},#I: ,}` 一致（单窗口退为一个空格，避免
    `win-5:⏳` 这种粘连）；bell（◉ 红）优先于 activity（● 青），与 tmux 的
    `#{?window_bell_flag,◉,#{?window_activity_flag,●,}}` 一致。
    """
    index = f"{agent['win_idx']}: " if agent["sess_win_count"] > 1 else " "
    prefix = f"   └ {agent['pid']:<6} {agent['session']}:{index}"
    marker = agent["pi_win"]  # "⏳ " / "✅ " / ""，取值自带尾随空格，不要再补
    label = win_label(agent)
    light, light_ansi = "", ""
    if agent["bell"]:
        light, light_ansi = "◉", _ANSI_RED
    elif agent["act"]:
        light, light_ansi = "●", _ANSI_CYAN

    text = (
        f"{prefix}{marker}{label}"
        + (f" {light}" if light else "")
        + f"  {agent['folder']}"
    )
    ansi = prefix
    if marker:
        ansi += f"{_ANSI_YELLOW}{marker}{_ANSI_RESET}"
    ansi += label
    if light:
        ansi += f" {light_ansi}{light}{_ANSI_RESET}"
    ansi += f"  {agent['folder']}"
    return text, ansi


def build_rows(agents):
    """把 agent 列表渲染成按 kitty tab 分组的行（组头 + 缩进子行）。

    返回 list[dict]，每项：
    - kind: "tab"（组头）或 "agent"（子行）
    - text: 无色文本，用于与 fzf 输出精确回匹配
    - ansi: 带 ANSI 颜色的显示文本，喂给 fzf --ansi
    - tab_id: 所属 kitty tab（无则为空串）
    - agent: 仅子行有，指向原 agent dict
    组顺序按 (OS window, tab 序号)，无 kitty tab 的组排在最后。
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
        if grp["tab_id"] and first["win_count"] > 1:
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
        # 按窗口序号排，与 tab 栏从左到右的顺序对齐（而不是按窗口名字母序）
        for a in sorted(
            grp["agents"],
            key=lambda item: (item["session"], item["win_idx"], item["pid"]),
        ):
            text, ansi = format_child(a)
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
    """跳转原则：只把 *目标* 所在的 tmux client 切到目标 session/window/pane，
    绝不动当前 tab 里自己的 client（否则当前窗口会被拖走）。"""
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


def main():
    agents = collect_agents()
    if not agents:
        print("未检测到运行中的 Pi Coding Agent。[按 Enter 退出]")
        input()
        return

    rows = build_rows(agents)
    header = "选择 Pi Agent (↑/↓ 选择, Enter 跳转, Esc 取消) —— 按 Kitty Tab 分组"
    # 回车时用 --accept-nth='{n}' 回传该行在输入里的序号（--no-sort 下即 rows 下标）。
    # 即便两个 tab 标题完全相同，也能精确定位到被选中的那一条，不依赖显示文本做唯一键。
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
        print("未找到 fzf，请先运行: brew install fzf")
        input()
        return
    if proc.returncode != 0 or not selected or not selected.strip():
        return

    try:
        row = rows[int(selected.strip())]
    except (ValueError, IndexError):
        return

    # 组头：只聚焦该 kitty tab；无 tab 的组头（No Kitty Tab）无动作
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
