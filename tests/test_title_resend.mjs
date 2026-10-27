// Behaviour tests for the title resend in kitty-tab-sync.ts.
//
// The bug these pin down: tmux pushes a title only when the rendered string changes, so a
// status change that happens during a network drop is lost while tmux's cache already holds
// the new value — the kitty tab then stays stale forever. The fix pushes again a moment
// later; the network-drop half of that story cannot be reproduced here (it needs tsshd and a
// real link), so what is asserted is the contract it depends on: the repeat push is DEFERRED
// (a resend fired immediately would sit in the same loss window), it carries the same
// visible text and differs only in the zero-width nonce, each later change gets its own,
// a burst collapses into one, quitting flushes a pending one, and an idle session pushes
// nothing at all.
//
// Run: node --experimental-strip-types --test tests/test_title_resend.mjs
//
// Each test gets its own throwaway tmux server and its own fresh copy of the extension
// module (module state such as the pending timer lives at module scope). A `tmux` shim on
// PATH pins the extension's plain `tmux` calls to that server, and the client is attached
// through a real pty so title OSCs are observed on the wire rather than inferred.

import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { after, test } from "node:test";

// Resolve the real binary up front: inside a test the shim is on PATH, so a plain lookup
// later would find the shim. macOS and Linux install tmux in different places.
const REAL_TMUX = spawnSync("/bin/sh", ["-c", "command -v tmux"], {
  encoding: "utf8",
}).stdout.trim();
assert.ok(REAL_TMUX, "tmux is not on PATH — these tests drive a real tmux server");
const RESEND_MS = 800;
const EXTENSION = new URL("../kitty-tab-sync.ts", import.meta.url);

// Production value is 5000; shrink it so each case finishes in about a second.
process.env.PI_TITLE_RESEND_MS = String(RESEND_MS);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(predicate, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await sleep(20);
  }
  return predicate();
}

const visible = (title) =>
  title.replace(/[\u200b\u200c\u200d\u2060\ufeff]/g, "");

const OSC_TITLE = /\x1b\]([0-9]+);([^\x07\x1b]*)(\x07|\x1b\\)/g;

class Sandbox {
  constructor() {
    this.dir = mkdtempSync(join(tmpdir(), "title-resend-"));
    this.socket = `-Ltitle${process.pid}${Math.floor(Math.random() * 1e6)}`;
    this.shim = join(this.dir, "bin");
    mkdirSync(this.shim);
    // The extension runs bare `tmux ...` against whatever the default server is; the shim
    // pins both the socket and the socket directory, so those calls land in this sandbox no
    // matter what env the extension inherited.
    writeFileSync(
      join(this.shim, "tmux"),
      `#!/bin/sh\nTMUX_TMPDIR=${this.dir} exec ${REAL_TMUX} ${this.socket} "$@"\n`,
    );
    chmodSync(join(this.shim, "tmux"), 0o755);
    this.env = {
      ...process.env,
      TERM: "xterm-kitty",
      TMUX_TMPDIR: this.dir,
      PATH: `${this.shim}:${process.env.PATH}`,
    };
    this.buffer = "";
    this.pushed = [];
    this.lastPushAt = 0;
    this.client = null;
  }

  tmux(args) {
    return spawnSync(REAL_TMUX, [this.socket, ...args], {
      env: this.env,
      encoding: "utf8",
    });
  }

  // Only complete OSC frames are counted, and each is dropped from the buffer once seen.
  // Matching the unfinished tail as well counted one frame twice whenever a read split it,
  // which made the idle assertion flaky.
  drain() {
    OSC_TITLE.lastIndex = 0;
    let frame;
    let consumed = 0;
    while ((frame = OSC_TITLE.exec(this.buffer)) !== null) {
      const [, code, payload] = frame;
      if (code === "0" || code === "2") {
        this.pushed.push(payload);
        this.lastPushAt = Date.now();
      }
      consumed = OSC_TITLE.lastIndex;
    }
    if (consumed > 0) this.buffer = this.buffer.slice(consumed);
  }

  async start() {
    this.tmux(["-f", "/dev/null", "new-session", "-d", "-s", "s", "-x", "120", "-y", "30"]);
    for (const args of [
      ["set", "-g", "set-titles", "on"],
      // Same shape and order as tmux.conf: status, repo, tag, nonce last.
      ["set", "-g", "set-titles-string", "#{@pi_total}repo#{@pi_nonce}"],
      ["set", "-g", "status-interval", "60"],
      ["set", "-as", "terminal-features", "xterm-kitty:title"],
    ]) {
      this.tmux(args);
    }
    this.pane = this.tmux(["display", "-p", "-t", "s", "#{pane_id}"]).stdout.trim();

    // stdin must stay open: with /dev/null as stdin the client sees EOF and detaches right
    // away, which silently leaves no client listening for title OSCs.
    this.client = spawn("script", ["-qec", `${REAL_TMUX} ${this.socket} attach -t s`, "/dev/null"], {
      env: this.env,
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.client.stdout.on("data", (chunk) => {
      this.buffer += chunk.toString("utf8");
      this.drain();
    });

    process.env.TMUX = `${this.dir}/default,0,0`;
    process.env.TMUX_PANE = this.pane;
    process.env.PATH = this.env.PATH;

    // The attach itself draws a title and can redraw it once more while the pty settles.
    // Let those land before anyone takes a baseline, or a leftover frame reads as a resend.
    const attached = await waitFor(() => this.titles().length > 0);
    assert.ok(attached, "the tmux client never attached, so no title could be observed");
    await sleep(300);
    return this;
  }

  titles() {
    this.drain();
    return this.pushed;
  }

  // Fresh module instance per case, so a pending timer from one case cannot fire into the next.
  async loadExtension() {
    const module = await import(`${pathToFileURL(EXTENSION.pathname).href}?case=${Math.random()}`);
    const handlers = new Map();
    const events = new Map();
    module.default({
      on(name, fn) {
        handlers.set(name, [...(handlers.get(name) ?? []), fn]);
      },
      events: {
        on(name, fn) {
          events.set(name, [...(events.get(name) ?? []), fn]);
        },
        emit(name, payload) {
          for (const fn of events.get(name) ?? []) fn(payload);
        },
      },
    });
    return {
      async fire(name) {
        for (const fn of handlers.get(name) ?? []) await fn({}, { isIdle: () => false });
      },
    };
  }

  stop() {
    this.client?.kill("SIGKILL");
    this.tmux(["kill-server"]);
    rmSync(this.dir, { recursive: true, force: true });
  }
}

const running = [];
after(() => {
  for (const sandbox of running) sandbox.stop();
});

async function sandbox() {
  const box = new Sandbox();
  running.push(box);
  await box.start();
  return box;
}

// A resend is the only kind of push that repeats the previous visible title: every status
// change alters what the tab shows, the nonce flip does not. Counting these is immune to
// tmux coalescing several immediate pushes and to the order they arrive in.
function resendCount(titles) {
  return titles.filter(
    (title, i) => i > 0 && title !== titles[i - 1] && visible(title) === visible(titles[i - 1]),
  ).length;
}

// The resend is identified by content, never by ordinal: with a burst of changes there may
// be several immediate pushes, and a baseline redraw would shift every index.
async function resendAfter(box, immediateIndex, timeoutMs = RESEND_MS * 5) {
  const immediate = box.titles()[immediateIndex];
  const found = await waitFor(() =>
    box
      .titles()
      .slice(immediateIndex + 1)
      .some((t) => t !== immediate && visible(t) === visible(immediate)),
  timeoutMs);
  return found ? box.titles().at(-1) : null;
}

test("a status change pushes the title again a moment later, visibly unchanged", async () => {
  const box = await sandbox();
  const ext = await box.loadExtension();
  const before = box.titles().length;

  await ext.fire("agent_start");
  assert.ok(await waitFor(() => box.titles().length > before), "no immediate push on agent_start");
  const immediate = box.titles()[before];
  assert.match(visible(immediate), /⏳/, `expected a running marker, got ${JSON.stringify(immediate)}`);

  // The whole point is that this copy goes out LATER: tsshd only starts buffering output
  // once it has seen no client heartbeat for 3s, so a resend fired straight away would be
  // lost in exactly the same way as the original push.
  await sleep(RESEND_MS / 2);
  assert.equal(
    resendCount(box.titles()),
    0,
    "the resend is not deferred — it fires inside the window where the network drop loses it",
  );

  const resend = await resendAfter(box, before);
  assert.ok(
    resend,
    "the resend never arrived — flipping @pi_nonce would have been a no-op",
  );
  assert.equal(visible(resend), visible(immediate), "the resend must not change what the tab shows");
  assert.notEqual(resend, immediate, "the resend must differ in the nonce, or tmux would skip it");
  assert.equal(box.titles().length, before + 2, "exactly one push plus one resend, not a burst");
});

test("a later status change gets a resend of its own", async () => {
  const box = await sandbox();
  const ext = await box.loadExtension();
  const before = box.titles().length;

  await ext.fire("agent_start");
  assert.ok(await waitFor(() => box.titles().length > before), "no immediate push on agent_start");
  assert.ok(await resendAfter(box, before), "the first status change was never resent");
  assert.equal(resendCount(box.titles()), 1);

  // The nonce now holds a value, so the second flip has to move it somewhere else. If both
  // nonce chars were the same, this resend would be a silent no-op and only the first
  // status change of a session's lifetime would ever be retried.
  await ext.fire("agent_settled");
  assert.ok(
    await waitFor(() => resendCount(box.titles()) === 2, RESEND_MS * 5),
    "the second status change was not resent — the nonce flip stopped changing the title",
  );
  assert.match(visible(box.titles().at(-1)), /✅/, "the resent title is not the settled one");
});

test("nothing is pushed while the status stays put", async () => {
  const box = await sandbox();
  const ext = await box.loadExtension();
  const before = box.titles().length;
  await ext.fire("agent_start");
  assert.ok(await waitFor(() => box.titles().length > before), "no immediate push on agent_start");

  assert.ok(await resendAfter(box, before), "no resend to measure idleness after");
  const settled = box.titles().length;

  await sleep(RESEND_MS * 6);
  assert.equal(
    box.titles().length,
    settled,
    "an idle session must not push titles — this is the zero-traffic promise",
  );
});

test("a burst of status changes collapses into one resend", async () => {
  const box = await sandbox();
  const ext = await box.loadExtension();
  const before = box.titles().length;

  await ext.fire("agent_start");
  await ext.fire("agent_settled");
  await ext.fire("agent_start");
  await sleep(RESEND_MS * 3);

  const titles = box.titles();
  assert.ok(titles.length > before, "the burst pushed nothing at all");
  assert.match(
    visible(titles.at(-1)),
    /⏳/,
    "the title does not show the state the burst ended in",
  );
  assert.equal(
    resendCount(titles),
    1,
    "each change in the burst scheduled its own resend — the timer is not debounced",
  );
});

test("quitting with a resend pending pushes it now instead of dropping it", async () => {
  const box = await sandbox();
  // A delay no case here can wait out: the push below can only come from the exit path,
  // not from a timer that happened to fire. A short delay would let the timer rescue it.
  process.env.PI_TITLE_RESEND_MS = "60000";
  const ext = await box.loadExtension();
  const before = box.titles().length;
  await ext.fire("agent_start");
  assert.ok(await waitFor(() => box.titles().length > before), "no immediate push on agent_start");

  const beforeExit = box.titles().length;
  process.emit("exit", 0);

  assert.ok(
    await waitFor(() => resendCount(box.titles().slice(beforeExit)) >= 1, 2000),
    "exit did not flush the pending resend; the next 5s timer dies with the process",
  );
  assert.doesNotMatch(
    visible(box.titles().at(-1)),
    /⏳/,
    "the pushed title still claims a run is in progress after the process exited",
  );
});
