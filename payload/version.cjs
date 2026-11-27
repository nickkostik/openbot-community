"use strict";

var fs = require("fs");
var path = require("path");
var { execFileSync } = require("child_process");

var HEADER = "x-openbot-version";
var stamped;

function readInstallStamp() {
  try {
    return JSON.parse(fs.readFileSync(path.join(__dirname, "version.json"), "utf8"));
  } catch (err) {
    return null;
  }
}

var releaseRoot = path.dirname(__dirname);
var releaseStore = path.dirname(releaseRoot);
var nativeStamp = path.basename(releaseStore) === ".openbot-rust-releases"
  && /^[a-f0-9]{64}$/.test(path.basename(releaseRoot))
  && readInstallStamp();
var managedService = nativeStamp && nativeStamp.runtime === "rust"
  && Number.isInteger(nativeStamp.port) && nativeStamp.port >= 1 && nativeStamp.port <= 65535
  ? Object.freeze({ sandData: path.dirname(releaseStore), port: nativeStamp.port })
  : null;

function readGitCommit() {
  try {
    var out = execFileSync(
      "git",
      ["-C", path.join(__dirname, ".."), "rev-parse", "HEAD"],
      { encoding: "utf8", timeout: 2000, stdio: ["ignore", "pipe", "ignore"] },
    );
    var sha = String(out || "").trim();
    if (/^[0-9a-f]{7,40}$/i.test(sha)) return sha;
  } catch (err) {
    /* tarball installs have no git */
  }
  return "";
}

function openBotVersion() {
  var fromEnv = process.env.OPENBOT_COMMIT;
  if (typeof fromEnv === "string" && fromEnv.trim()) {
    return fromEnv.trim();
  }
  if (stamped === undefined) {
    var row = nativeStamp || readInstallStamp();
    var commit = row && typeof row.commit === "string" ? row.commit.trim() : "";
    stamped = commit || readGitCommit() || "unknown";
  }
  return stamped;
}

function applyOpenBotVersionHeader(headers) {
  if (!headers || typeof headers !== "object") return headers;
  headers[HEADER] = openBotVersion();
  return headers;
}

exports.HEADER = HEADER;
exports.openBotVersion = openBotVersion;
exports.applyOpenBotVersionHeader = applyOpenBotVersionHeader;
exports.managedService = managedService;
