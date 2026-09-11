#!/usr/bin/env node
// Entry point for `hybro`. It locates the frozen CLI for this platform and
// replaces itself with it.
//
// The platform binaries are optionalDependencies, not postinstall downloads:
// npm already resolves os/cpu, so only the matching package is installed, and
// `npm install --ignore-scripts` still yields a working CLI.
//
// No signal handlers are installed on purpose. With stdio inherited, the child
// shares this process's foreground process group, so Ctrl-C reaches it directly;
// forwarding as well would deliver SIGINT twice and the TUI treats that as two
// separate interrupts.

"use strict";

const { spawn } = require("node:child_process");
const { accessSync, constants, chmodSync } = require("node:fs");

const PLATFORM_PACKAGES = {
  "darwin arm64": "@hybroai/cli-darwin-arm64",
  "darwin x64": "@hybroai/cli-darwin-x64",
  "linux arm64": "@hybroai/cli-linux-arm64",
  "linux x64": "@hybroai/cli-linux-x64",
};

function fail(message) {
  process.stderr.write(`hybro: ${message}\n`);
  process.exit(1);
}

const key = `${process.platform} ${process.arch}`;
const packageName = PLATFORM_PACKAGES[key];
if (!packageName) {
  fail(
    `no build for ${key}. Supported: ${Object.keys(PLATFORM_PACKAGES).join(", ")}.`,
  );
}

let binary;
try {
  binary = require.resolve(`${packageName}/hybro/hybro`);
} catch {
  fail(
    `${packageName} is not installed. Reinstall with ` +
      `"npm install -g @hybroai/cli" without --omit=optional.`,
  );
}

// npm preserves the executable bit, but a copied or re-packed tree may not.
try {
  accessSync(binary, constants.X_OK);
} catch {
  try {
    chmodSync(binary, 0o755);
  } catch {
    fail(`${binary} is not executable.`);
  }
}

const child = spawn(binary, process.argv.slice(2), { stdio: "inherit" });

child.on("error", (error) => fail(`cannot start ${binary}: ${error.message}`));
child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code === null ? 1 : code);
});
