/**
 * Evolution Hub Dashboard Plugin
 *
 * Visualizes the self-evolution batch pipeline in the Hermes dashboard.
 * Components:
 *   1. Status cards — batch health, skill progress, last heartbeat
 *   2. Batch Complete banner — shown when all skills are done
 *   3. Queue table — all skills sorted by size, with status/model/error
 *   4. Log viewer — live tail with ERROR filter and full download
 *   5. Skill detail panel — wrapper JSON + git history + revert
 *   6. Sidebar controls — Start / Stop / Reset with confirmation modals
 *
 * No build step needed — plain IIFE using the Plugin SDK globals.
 * Dynamic visual effects via injected CSS keyframe animations.
 */
(function () {
  "use strict";

  // ── Load p5.js from CDN for topology view ─────────────────────────
  (function loadP5() {
    if (document.querySelector('script[src*="p5.js"]')) return;
    var s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/p5.js/1.11.3/p5.min.js";
    s.defer = true;
    document.head.appendChild(s);
  })();

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const { React } = SDK;
  const { Card, CardHeader, CardTitle, CardContent, Badge, Button, Separator } = SDK.components;
  const { useState, useEffect, useRef } = SDK.hooks;
  const { cn, timeAgo } = SDK.utils;

  // ── Constants ─────────────────────────────────────────────────────────

  const POLL_INTERVAL = 5000;
  const LOG_LINES = 50;

  // ── Animated counter ─────────────────────────────────────────────────

  function AnimatedCount(_ref) {
    var value = _ref.value;
    var className = _ref.className;
    var _prev = useRef(value);
    var _anim = useState("");

    useEffect(function () {
      if (_prev.current !== value) {
        _anim[1]("ev-count-change");
        var t = setTimeout(function () { _anim[1](""); }, 300);
        _prev.current = value;
        return function () { clearTimeout(t); };
      }
    }, [value]);

    return React.createElement("span", { className: cn(className, _anim[0]) }, value);
  }

  // ── Elapsed time helper ──────────────────────────────────────────────

  function elapsedSince(isoString) {
    if (!isoString) return null;
    try {
      var diff = Date.now() - new Date(isoString).getTime();
      if (diff < 0 || isNaN(diff)) return null;
      var secs = Math.floor(diff / 1000);
      var mins = Math.floor(secs / 60);
      var hrs = Math.floor(mins / 60);
      if (hrs > 0) return hrs + "h " + (mins % 60) + "m";
      if (mins > 0) return mins + "m " + (secs % 60) + "s";
      return secs + "s";
    } catch (e) { return null; }
  }

  // ── Colour helpers ───────────────────────────────────────────────────

  function statusColor(status, stale) {
    if (stale) return "text-destructive";
    switch (status) {
      case "running":   return "text-success";
      case "stopped":   return "text-muted-foreground";
      case "unknown":   return "text-warning";
      default:          return "text-muted-foreground";
    }
  }

  function skillStatusBadge(st) {
    switch (st) {
      case "running":         return { label: "Running",        cls: "evolution-status-running" };
      case "completed":       return { label: "Completed",      cls: "evolution-status-completed" };
      case "no_improvement":  return { label: "No Improvement", cls: "evolution-status-completed" };
      case "failed":          return { label: "Failed",         cls: "evolution-status-failed" };
      default:                return { label: "Pending",        cls: "evolution-status-pending" };
    }
  }

  // ── Sidebar Controls (slot: sidebar) ─────────────────────────────────

  function SidebarControls() {
    var _s = useState(null);
    var batchStatus = _s[0];
    var setBatchStatus = _s[1];
    var _s2 = useState(null);
    var currentSkill = _s2[0];
    var setCurrentSkill = _s2[1];
    var _s3 = useState(null);
    var confirmAction = _s3[0];
    var setConfirmAction = _s3[1];
    var _s4 = useState(false);
    var sending = _s4[0];
    var setSending = _s4[1];

    function pollHealth() {
      SDK.fetchJSON("/api/plugins/evolution-hub/batch-health")
        .then(function (data) {
          setBatchStatus(data.status || "unknown");
          setCurrentSkill(data.current_skill);
        })
        .catch(function () {});
    }

    useEffect(function () {
      pollHealth();
      var iv = setInterval(pollHealth, POLL_INTERVAL);
      return function () { clearInterval(iv); };
    }, []);

    function doAction(action) {
      setSending(true);
      SDK.fetchJSON("/api/plugins/evolution-hub/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: action }),
      })
        .then(function () {
          setConfirmAction(null);
          pollHealth();
        })
        .catch(function () {})
        .finally(function () { setSending(false); });
    }

    var isRunning = batchStatus === "running";

    return React.createElement("div", { className: "flex flex-col gap-3 px-3 py-2" },
      React.createElement("div", { className: "flex items-center gap-2" },
        React.createElement("span", { className: "text-xs font-medium text-muted-foreground uppercase tracking-wider" }, "Pipeline Control"),
        isRunning && React.createElement(Badge, {
          className: "ev-live-badge text-[10px] h-4 px-1.5 bg-success/20 text-success border-success/40",
          variant: "outline",
        }, "LIVE"),
      ),
      React.createElement("div", { className: "flex items-center gap-2 text-xs" },
        React.createElement("div", {
          className: cn(
            "w-2 h-2 rounded-full",
            isRunning ? "bg-success ev-dot-running" : "bg-muted-foreground"
          ),
        }),
        React.createElement("span", { className: "font-courier" }, batchStatus || "\u2014"),
        currentSkill && currentSkill !== "none" && React.createElement("span", {
          className: "text-muted-foreground truncate max-w-[140px]",
          title: currentSkill,
        }, "\u00B7 " + currentSkill),
      ),
      React.createElement(Separator, null),
      React.createElement(Button, {
        className: "evolution-control-btn w-full text-xs h-8",
        variant: "default",
        onClick: function () { doAction("start"); },
        disabled: sending,
      }, sending ? "..." : "\u25B6 Start"),
      confirmAction === "stop"
        ? React.createElement("div", { className: "flex gap-1" },
            React.createElement(Button, {
              className: "evolution-control-btn flex-1 text-xs h-8",
              variant: "destructive",
              onClick: function () { doAction("stop"); },
              disabled: sending,
            }, sending ? "..." : "Confirm Stop"),
            React.createElement(Button, {
              className: "evolution-control-btn text-xs h-8",
              variant: "outline",
              onClick: function () { setConfirmAction(null); },
              disabled: sending,
            }, "Cancel"),
          )
        : React.createElement(Button, {
            className: "evolution-control-btn w-full text-xs h-8",
            variant: "outline",
            onClick: function () { setConfirmAction("stop"); },
          }, "\u25A0 Stop"),
      confirmAction === "reset"
        ? React.createElement("div", { className: "flex gap-1" },
            React.createElement(Button, {
              className: "evolution-control-btn flex-1 text-xs h-8",
              variant: "destructive",
              onClick: function () { doAction("reset"); },
              disabled: sending,
            }, sending ? "..." : "Confirm Reset"),
            React.createElement(Button, {
              className: "evolution-control-btn text-xs h-8",
              variant: "outline",
              onClick: function () { setConfirmAction(null); },
              disabled: sending,
            }, "Cancel"),
          )
        : React.createElement(Button, {
            className: "evolution-control-btn w-full text-xs h-8",
            variant: "outline",
            onClick: function () { setConfirmAction("reset"); },
          }, "\u21BA Reset"),
    );
  }

  // ── Status icon ──────────────────────────────────────────────────────

  function StatusIcon(_ref) {
    var status = _ref.status;
    var cls = skillStatusBadge(status).cls;
    var colors = {
      "evolution-status-running": "text-success",
      "evolution-status-completed": "text-accent",
      "evolution-status-failed": "text-destructive",
      "evolution-status-pending": "text-muted-foreground",
    };
    var color = colors[cls] || "text-muted-foreground";
    var isRunning = status === "running";
    return React.createElement("span", {
      className: cn(
        color + " text-base leading-none mr-1",
        isRunning && "ev-dot-running inline-block"
      ),
    },
      isRunning       ? "\u25B6"
      : status === "completed" || status === "no_improvement" ? "\u2713"
      : status === "failed" ? "\u2717"
      : "\u25CB"
    );
  }

  // ── Progress Bar ──────────────────────────────────────────────────────

  function ProgressBar(_ref2) {
    var done = _ref2.done;
    var total = _ref2.total;
    var batchComplete = _ref2.batchComplete;
    var pct = total > 0 ? Math.round((done / total) * 100) : 0;

    return React.createElement("div", { className: "mt-3" },
      React.createElement("div", {
        className: "h-1.5 w-full rounded-full overflow-hidden",
        style: { background: "rgba(59, 130, 246, 0.12)" },
      },
        React.createElement("div", {
          className: cn(
            "ev-progress-fill h-full rounded-full transition-all duration-500",
            batchComplete ? "bg-success" : "bg-accent"
          ),
          style: { width: pct + "%" },
        }),
      ),
      React.createElement("div", { className: "flex justify-between mt-1" },
        React.createElement("span", { className: "text-[10px] text-muted-foreground" },
          pct + "% complete"
        ),
        React.createElement("span", { className: "text-[10px] text-muted-foreground" },
          done + " / " + total
        ),
      ),
    );
  }

  // ── Log viewer sub-component ─────────────────────────────────────────

  function LogViewer(_ref3) {
    var logData = _ref3.logData;
    var className = _ref3.className;

    if (!logData || !logData.log) {
      return React.createElement("pre", { className: cn("evolution-log-viewer p-3 text-xs overflow-auto", className) },
        React.createElement("span", { className: "text-muted-foreground" },
          logData === null ? "Loading log..." : (logData && logData.message ? logData.message : "No log content")
        ),
      );
    }

    var lines = logData.log.split("\n");
    var totalLines = logData.lines || 0;

    return React.createElement("pre", {
      className: cn("evolution-log-viewer p-3 text-xs overflow-auto", className),
    },
      lines.map(function (line, i) {
        var lineNum = totalLines - lines.length + i + 1;
        var isError = /ERROR|TRACEBACK|EXCEPTION|FATAL|STALLED/i.test(line);
        var isEmpty = !line.trim();
        return React.createElement("div", {
          key: i,
          className: cn(
            "leading-relaxed",
            isError && "text-destructive font-medium",
            i >= lines.length - 2 && "ev-log-new"
          ),
        },
          React.createElement("span", {
            className: cn(
              "select-none mr-2 w-8 inline-block text-right",
              isError ? "text-destructive/60" : "text-muted-foreground/40"
            ),
          }, lineNum),
          isError
            ? React.createElement("span", { className: "text-destructive" }, "\u26A0 ")
            : null,
          isEmpty ? "\u00A0" : line
        );
      }),
    );
  }

  // ── Expandable inline skill detail (row accordion) ───────────────────

  function ExpandableSkillDetail(_ref4) {
    var skillName = _ref4.skillName;
    var onClose = _ref4.onClose;
    var _h = useState(null);
    var history = _h[0];
    var setHistory = _h[1];
    var _w = useState(null);
    var wrapperResults = _w[0];
    var setWrapperResults = _w[1];
    var _r = useState(null);
    var confirmRevert = _r[0];
    var setConfirmRevert = _r[1];
    var _r2 = useState(null);
    var revertResult = _r2[0];
    var setRevertResult = _r2[1];
    var _s5 = useState(false);
    var reverting = _s5[0];
    var setReverting = _s5[1];

    useEffect(function () {
      setHistory(null);
      setWrapperResults(null);
      setConfirmRevert(null);
      setRevertResult(null);
      var enc = encodeURIComponent(skillName);
      SDK.fetchJSON("/api/plugins/evolution-hub/skill-history?name=" + enc)
        .then(setHistory).catch(function () { setHistory({ commits: [], error: "Failed to load" }); });
      SDK.fetchJSON("/api/plugins/evolution-hub/skill-result?name=" + enc)
        .then(setWrapperResults).catch(function () {});
    }, [skillName]);

    function doRevert(sha) {
      setReverting(true);
      setRevertResult(null);
      SDK.fetchJSON("/api/plugins/evolution-hub/skill-revert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skill: skillName, commit_sha: sha }),
      })
        .then(function (data) {
          setRevertResult(data);
          setConfirmRevert(null);
          SDK.fetchJSON("/api/plugins/evolution-hub/skill-history?name=" + encodeURIComponent(skillName))
            .then(setHistory).catch(function () {});
        })
        .catch(function (err) {
          setRevertResult({ success: false, detail: "Network error: " + (err.message || err) });
        })
        .finally(function () { setReverting(false); });
    }

    var commits = history && history.commits ? history.commits : [];

    return React.createElement("div", {
      className: "px-4 py-3 bg-accent/5 border-b border-border/40 flex flex-col gap-3",
    },
      // Header with close button
      React.createElement("div", { className: "flex items-center justify-between" },
        React.createElement("span", { className: "text-xs font-medium text-muted-foreground uppercase tracking-wider" },
          "Skill Detail: " + skillName
        ),
        React.createElement(Button, {
          className: "text-xs h-5 w-5 p-0",
          variant: "ghost",
          onClick: onClose,
        }, "\u2716"),
      ),

      // Revert result
      revertResult && React.createElement("div", {
        className: cn(
          "text-xs p-2 rounded flex items-center gap-2",
          revertResult.success
            ? "bg-success/10 border border-success/30 text-success"
            : "bg-destructive/10 border border-destructive/30 text-destructive"
        ),
      },
        React.createElement("span", null, revertResult.success ? "\u2714\uFE0F" : "\u26A0\uFE0F"),
        React.createElement("span", null,
          revertResult.success
            ? "Reverted to " + (revertResult.reverted_to ? revertResult.reverted_to.substring(0, 12) : "previous commit")
            : (revertResult.detail || revertResult.reason || "unknown error")
        ),
      ),

      // Wrapper + History in 2-column layout
      React.createElement("div", { className: "grid grid-cols-2 gap-4" },

        // Left: Evolution result
        React.createElement("div", null,
          React.createElement("span", { className: "text-[10px] font-medium text-muted-foreground uppercase tracking-wider block mb-1" }, "Evolution Result"),
          wrapperResults && wrapperResults.found
            ? React.createElement("pre", {
                className: "evolution-log-viewer p-2 text-xs overflow-auto max-h-24",
              }, JSON.stringify(wrapperResults.data, null, 2))
            : React.createElement("span", { className: "text-xs text-muted-foreground" }, "No evolution data yet"),
        ),

        // Right: Git history
        React.createElement("div", null,
          React.createElement("span", { className: "text-[10px] font-medium text-muted-foreground uppercase tracking-wider block mb-1" }, "Git History"),
          React.createElement("span", { className: "text-[10px] text-muted-foreground block mb-1" },
            "Commits for .claude/skills/" + skillName + "/SKILL.md"
          ),
          commits.length === 0
            ? React.createElement("span", { className: "text-xs text-muted-foreground" },
                history === null ? "Loading..." : (history.error || "No history")
              )
            : React.createElement("div", { className: "flex flex-col gap-0.5 max-h-24 overflow-y-auto" },
                commits.map(function (c, idx) {
                  var isFirst = idx === 0;
                  var isConfirming = confirmRevert === c.sha;
                  return React.createElement("div", {
                    key: c.sha,
                    className: cn(
                      "grid grid-cols-12 gap-1 text-[10px] items-center px-1.5 py-1 rounded",
                      isFirst && "bg-accent/10"
                    ),
                  },
                    React.createElement("span", { className: "col-span-3 font-courier text-muted-foreground" },
                      c.sha.substring(0, 8)
                    ),
                    React.createElement("span", { className: "col-span-3 text-muted-foreground" },
                      c.date ? c.date.substring(0, 10) : "\u2014"
                    ),
                    React.createElement("span", { className: cn("col-span-4 truncate", isFirst && "font-medium text-accent") },
                      c.message
                    ),
                    React.createElement("div", { className: "col-span-2 text-right" },
                      isConfirming
                        ? React.createElement("div", { className: "flex gap-0.5 justify-end" },
                            React.createElement(Button, {
                              className: "text-[10px] h-4 px-1",
                              variant: "destructive",
                              onClick: function () { doRevert(c.sha); },
                              disabled: reverting,
                            }, reverting ? "..." : "Yes"),
                            React.createElement(Button, {
                              className: "text-[10px] h-4 px-1",
                              variant: "ghost",
                              onClick: function () { setConfirmRevert(null); },
                              disabled: reverting,
                            }, "X"),
                          )
                        : isFirst
                          ? React.createElement("span", { className: "text-[10px] text-muted-foreground" }, "HEAD")
                          : React.createElement(Button, {
                              className: "text-[10px] h-4 px-1 text-destructive border-destructive/30",
                              variant: "outline",
                              onClick: function () { setConfirmRevert(c.sha); },
                            }, "\u21A9"),
                    ),
                  );
                }),
              ),
        ),
      ),

      // Revert warning
      confirmRevert && React.createElement("div", {
        className: "text-[10px] p-1.5 rounded bg-destructive/10 border border-destructive/30 text-destructive flex items-center gap-1",
      },
        React.createElement("span", null, "\u26A0\uFE0F"),
        React.createElement("span", null,
          "Restores skill to commit " + confirmRevert.substring(0, 10) + ". Working dir must be clean. Audited."
        ),
      ),
    );
  }

  // ── Skill Detail Modal (from Generative Cockpit's Modal pattern) ────

  function EvoModal(_ref5) {
    var skillName = _ref5.skillName;
    var onClose = _ref5.onClose;

    useEffect(function () {
      function handleKey(e) {
        if (e.key === "Escape" && onClose) onClose();
      }
      document.addEventListener("keydown", handleKey);
      return function () { document.removeEventListener("keydown", handleKey); };
    }, [onClose]);

    useEffect(function () {
      document.body.style.overflow = "hidden";
      return function () { document.body.style.overflow = ""; };
    }, []);

    return React.createElement("div", {
      className: "evo-modal-backdrop",
      onClick: function (e) { if (e.target === e.currentTarget) onClose(); },
    },
      React.createElement("div", { className: "evo-modal-content card-notched" },
        React.createElement("div", { className: "flex items-center justify-between mb-3" },
          React.createElement("span", { className: "text-xs font-medium text-muted-foreground uppercase tracking-wider" }, "Skill Detail"),
          React.createElement("span", { className: "text-sm font-medium" }, skillName.replace(/-/g, " ")),
          React.createElement(Button, {
            className: "text-xs h-5 w-5 p-0",
            variant: "ghost",
            onClick: onClose,
          }, "\u2716"),
        ),
        React.createElement(ExpandableSkillDetail, {
          skillName: skillName,
          onClose: onClose,
        }),
      ),
    );
  }

  // ── p5.js Topology View ────────────────────────────────────────────

  function TopologyView(_ref6) {
    var skills = _ref6.skills;
    var onNodeClick = _ref6.onNodeClick;
    var containerRef = useRef(null);
    var labelLayerRef = useRef(null);
    var canvasSize = useRef({ w: 800, h: 600 });
    var hoveredNode = useRef(null);
    var _p5ok = useState(false);
    var p5Ready = _p5ok[0];
    var setP5Ready = _p5ok[1];
    var _lab = useState([]);
    var labels = _lab[0];
    var setLabels = _lab[1];

    // Poll for p5.js availability (loaded async from CDN)
    useEffect(function () {
      if (window.p5) { setP5Ready(true); return; }
      var iv = setInterval(function () {
        if (window.p5) { setP5Ready(true); clearInterval(iv); }
      }, 200);
      return function () { clearInterval(iv); };
    }, []);

    useEffect(function () {
      if (!p5Ready || !containerRef.current) return;
      var p5 = window.p5;
      var container = containerRef.current;
      if (!container) return;

      var hasData = skills && skills.length > 0;

      canvasSize.current = {
        w: container.offsetWidth || 800,
        h: Math.max(500, Math.min(800, (skills.length || 44) * 16)),
      };

      // Colors
      var C = {
        bg: [10, 14, 26],
        pending: [100, 116, 139],
        running: [34, 197, 94],
        completed: [96, 165, 250],
        failed: [239, 68, 68],
        edge: [30, 41, 59],
        text: [200, 210, 220],
        grid: [15, 20, 35],
      };

      // Compute node positions
      var nodes = [];
      if (hasData) {
        var cx = canvasSize.current.w / 2;
        var cy = canvasSize.current.h * 0.45;
        var maxR = Math.min(canvasSize.current.w, canvasSize.current.h) * 0.42;
        var goldenAngle = Math.PI * (3 - Math.sqrt(5));
        nodes = skills.map(function (s, i) {
          var t = Math.pow((i + 1) / skills.length, 0.55);
          var r = Math.max(30, t * maxR);
          var theta = i * goldenAngle;
          var x = cx + r * Math.cos(theta);
          var y = cy + r * Math.sin(theta);
          if (s.improvement != null) y -= s.improvement * 150;
          return {
            name: s.name,
            x: Math.max(50, Math.min(canvasSize.current.w - 50, x)),
            y: Math.max(50, Math.min(canvasSize.current.h - 50, y)),
            r: s.status === "running" ? 10 : 6,
            baseR: s.status === "running" ? 10 : 6,
            status: s.status,
            improvement: s.improvement,
            label: s.name.replace(/-/g, " "),
          };
        });
        // Repulsion
        for (var iter = 0; iter < 5; iter++) {
          for (var i = 0; i < nodes.length; i++) {
            for (var j = i + 1; j < nodes.length; j++) {
              var a = nodes[i], b = nodes[j];
              var dx = b.x - a.x, dy = b.y - a.y;
              var dist = Math.sqrt(dx * dx + dy * dy);
              if (dist < 25 && dist > 0.1) {
                var force = (25 - dist) / 25 * 8;
                a.x -= dx / dist * force; a.y -= dy / dist * force;
                b.x += dx / dist * force; b.y += dy / dist * force;
              }
            }
          }
        }
      }

      // Build HTML label data
      var labelData = [];
      for (var ni = 0; ni < nodes.length; ni++) {
        var n = nodes[ni];
        var shortName = n.label.length > 12 ? n.label.substring(0, 10) + ".." : n.label;
        var isRunning = n.status === "running";
        var isFailed = n.status === "failed";
        var showLabel = isRunning || isFailed;
        if (showLabel) {
          labelData.push({
            id: n.name,
            x: n.x, y: n.y + n.r + 8,
            text: shortName,
            color: isRunning ? "#22c55e" : "#ef4444",
            isRunning: isRunning,
            fontSize: "14px",
          });
        }
        if (n.improvement != null && !isRunning && !isFailed) {
          var sc = n.improvement >= 0 ? "#60a5fa" : "#ef4444";
          labelData.push({
            id: n.name + "-score",
            x: n.x, y: n.y - n.r - 6,
            text: (n.improvement >= 0 ? "+" : "") + (n.improvement * 100).toFixed(1) + "%",
            color: sc,
            small: true,
            fontSize: "12px",
          });
        }
      }
      setLabels(labelData);

      // p5 sketch — draws ONLY shapes (no text, no labels)
      var sketch = function (p) {
        var mouseNode = null;

        p.setup = function () {
          var c = p.createCanvas(canvasSize.current.w, canvasSize.current.h);
          c.parent(container);
          p.frameRate(24);
        };

        p.draw = function () {
          p.background(C.bg[0], C.bg[1], C.bg[2]);
          var hasFocus = mouseNode !== null;

          // Grid
          var gs = Math.max(40, canvasSize.current.w / 20);
          p.stroke(C.grid[0], C.grid[1], C.grid[2], 25);
          p.strokeWeight(0.5);
          for (var gx = 0; gx < canvasSize.current.w; gx += gs) p.line(gx, 0, gx, canvasSize.current.h);
          for (var gy = 0; gy < canvasSize.current.h; gy += gs) p.line(0, gy, canvasSize.current.w, gy);

          // Y-axis zones
          var zones = [[0.2,"+5%",8],[0.35,"+2%",5],[0.5,"0%",12],[0.65,"-2%",5],[0.8,"-5%",8]];
          for (var zi = 0; zi < zones.length; zi++) {
            var zy = canvasSize.current.h * zones[zi][0];
            p.noStroke();
            p.fill(10, 14, 26, zones[zi][2]);
            p.rect(0, zy - 15, canvasSize.current.w, 30);
          }

          // Edges
          if (nodes.length > 1) {
            p.noFill();
            p.stroke(C.edge[0], C.edge[1], C.edge[2], 35);
            p.strokeWeight(0.5);
            for (var ei = 0; ei < nodes.length - 1; ei++) {
              p.line(nodes[ei].x, nodes[ei].y, nodes[ei + 1].x, nodes[ei + 1].y);
            }
          }

          // Nodes
          for (var ni = 0; ni < nodes.length; ni++) {
            var n = nodes[ni];
            var isHovered = mouseNode === ni;
            var isDimmed = hasFocus && !isHovered;

            var col = C.pending, glow = 0, r = n.baseR;
            if (n.status === "running") { col = C.running; glow = 14; r = n.baseR + 2 * Math.sin(Date.now() * 0.005 + ni); }
            else if (n.status === "completed" || n.status === "no_improvement") { col = C.completed; glow = 4; }
            else if (n.status === "failed") { col = C.failed; glow = 6; r = n.baseR + 1 * Math.sin(Date.now() * 0.01 + ni * 2); }

            if (isDimmed) { glow = 0; r = n.baseR * 0.5; }

            if (glow > 0 && !isDimmed) {
              var gr = glow + 4 * Math.sin(Date.now() * 0.004 + ni);
              p.noStroke();
              p.fill(col[0], col[1], col[2], 18);
              p.ellipse(n.x, n.y, gr * 2);
            }

            var jx = 0, jy = 0;
            if (n.status === "failed" && !isDimmed) { jx = (Math.random() - 0.5) * 2; jy = (Math.random() - 0.5) * 2; }

            var alpha = isDimmed ? 25 : (n.status === "pending" ? 70 : 200);
            p.noStroke();
            p.fill(col[0], col[1], col[2], alpha);
            p.ellipse(n.x + jx, n.y + jy, r * 2);

            if (n.status === "running" && !isDimmed) {
              p.noFill();
              p.stroke(col[0], col[1], col[2], 120);
              p.strokeWeight(1.2);
              p.ellipse(n.x, n.y, (r + 8 + 4 * Math.sin(Date.now() * 0.005 + ni)) * 2);
            }
          }

          // Track mouse position for HTML overlay
          mouseNode = null;
          for (var mi = 0; mi < nodes.length; mi++) {
            var mn = nodes[mi];
            if (p.dist(p.mouseX, p.mouseY, mn.x, mn.y) < mn.r + 15) {
              mouseNode = mi;
              break;
            }
          }

          // Update HTML tooltip overlay
          var tooltipEl = document.getElementById("evo-topo-tooltip");
          if (mouseNode !== null && tooltipEl) {
            var tn = nodes[mouseNode];
            var tc = tn.status === "running" ? "#22c55e" : tn.status === "failed" ? "#ef4444" : tn.status === "completed" || tn.status === "no_improvement" ? "#60a5fa" : "#94a3b8";
            tooltipEl.textContent = tn.label + " [" + tn.status + "]" + (tn.improvement != null ? " (" + (tn.improvement >= 0 ? "+" : "") + (tn.improvement * 100).toFixed(1) + "%)" : "");
            tooltipEl.style.left = Math.max(0, Math.min(canvasSize.current.w - 300, tn.x - 100)) + "px";
            tooltipEl.style.top = (tn.y - 40) + "px";
            tooltipEl.style.display = "block";
            tooltipEl.style.borderColor = tc;
            tooltipEl.style.color = tc;
          } else if (tooltipEl) {
            tooltipEl.style.display = "none";
          }
        };

        // Click handler
        p.mouseClicked = function () {
          if (onNodeClick && mouseNode !== null) {
            onNodeClick(nodes[mouseNode].name);
          }
        };
      };

      try {
        var instance = new p5(sketch);
        return function () { instance.remove(); };
      } catch(e) {
        return function () {};
      }
    }, [skills, p5Ready]);

    return React.createElement("div", {
      className: "relative w-full rounded overflow-hidden",
      style: { minHeight: "500px" },
    },
      // p5 canvas container
      React.createElement("div", { ref: containerRef, className: "w-full", style: { minHeight: "500px" } }),

      // HTML tooltip overlay (browser-native text, respects zoom/font size)
      React.createElement("div", {
        id: "evo-topo-tooltip",
        className: "absolute pointer-events-none z-50 px-3 py-1.5 rounded border text-sm",
        style: {
          display: "none",
          background: "rgba(10, 14, 26, 0.92)",
          backdropFilter: "blur(6px)",
          fontSize: "14px",
          lineHeight: "1.5",
          maxWidth: "320px",
          whiteSpace: "nowrap",
          fontWeight: "500",
        },
      }),

      // HTML labels overlay (for running/failed skill names)
      labels.length > 0 && React.createElement("div", {
        ref: labelLayerRef,
        className: "absolute inset-0 pointer-events-none",
      },
        labels.map(function (lb) {
          return React.createElement("div", {
            key: lb.id,
            className: "absolute",
            style: {
              left: lb.x + "px",
              top: lb.y + "px",
              transform: "translateX(-50%)",
              color: lb.color,
              fontSize: lb.small ? "12px" : "14px",
              fontWeight: "500",
              fontFamily: "ui-monospace, monospace",
              lineHeight: "1.3",
              whiteSpace: "nowrap",
              textShadow: lb.isRunning ? "0 0 8px rgba(34,197,94,0.5)" : "0 0 4px rgba(0,0,0,0.8)",
              pointerEvents: "none",
              userSelect: "none",
            },
          }, lb.text);
        }),
      ),

      // Loading placeholder
      !p5Ready && React.createElement("div", {
        className: "flex items-center justify-center h-64 text-xs text-muted-foreground",
      }, "Loading p5.js from Cloudflare CDN..."),
    );
  }

  // ── Grid/Kanban View ──────────────────────────────────────────────

  function GridView(_ref6) {
    var skills = _ref6.skills;
    var selectedSkill = _ref6.selectedSkill;
    var onSkillClick = _ref6.onSkillClick;
    if (!skills) return null;

    // Bucket skills by status
    var buckets = { running: [], pending: [], completed: [], failed: [] };
    for (var gi = 0; gi < skills.length; gi++) {
      var s = skills[gi];
      if (s.status === "running") buckets.running.push(s);
      else if (s.status === "failed") buckets.failed.push(s);
      else if (s.status === "completed" || s.status === "no_improvement") buckets.completed.push(s);
      else buckets.pending.push(s);
    }

    // Sort pending by size (smallest first, like queue)
    buckets.pending.sort(function (a, b) { return a.size_kb - b.size_kb; });

    var columns = [
      { key: "running",   label: "Running",      icon: "\u25B6",  color: "text-success",         bg: "bg-success/5",    skills: buckets.running },
      { key: "pending",   label: "Pending",      icon: "\u25CB",  color: "text-muted-foreground", bg: "bg-transparent",  skills: buckets.pending },
      { key: "completed", label: "Completed",    icon: "\u2713",  color: "text-accent",           bg: "bg-accent/5",     skills: buckets.completed },
      { key: "failed",    label: "Failed",        icon: "\u2717",  color: "text-destructive",      bg: "bg-destructive/5", skills: buckets.failed },
    ];

    return React.createElement("div", { className: "flex gap-3 items-stretch" },
      columns.map(function (col) {
        return React.createElement("div", { key: col.key,
          className: cn("flex flex-col gap-1 rounded p-2 flex-1 min-w-0", col.bg),
          style: { minHeight: "200px" },
        },
          // Column header
          React.createElement("div", {
            className: cn("text-xs font-medium uppercase tracking-wider flex items-center gap-1.5 pb-1 mb-1 border-b border-border/40", col.color),
          },
            React.createElement("span", null, col.icon),
            React.createElement("span", null, col.label),
            React.createElement("span", { className: "text-muted-foreground font-normal" }, col.skills.length),
          ),

          // Skill cards
          col.skills.length === 0
            ? React.createElement("div", { className: "text-[10px] text-muted-foreground/50 text-center py-4 italic" }, "Empty")
            : col.skills.map(function (skill) {
                var isSelected = selectedSkill === skill.name;
                var isRun = skill.status === "running";
                var elapsed = isRun ? elapsedSince(skill.last_evolved) : null;
                var shortModel = skill.model ? skill.model.split("/").pop() : null;
                return React.createElement("div", { key: skill.name },
                  // Card
                  React.createElement("div", {
                    className: cn(
                      "flex flex-col gap-0.5 px-2 py-1.5 rounded cursor-pointer border text-xs transition-colors",
                      isSelected
                        ? "border-accent/50 bg-accent/10"
                        : "border-border/30 bg-background/40 hover:bg-foreground/5 hover:border-border/60"
                    ),
                    onClick: function () { onSkillClick(skill.name); },
                    title: skill.error || skill.name,
                  },
                    // Row 1: Name + status dot
                    React.createElement("div", { className: "flex items-center gap-1.5" },
                      React.createElement("span", {
                        className: cn(
                          "ev-dot-glowing",
                          skill.status === "running" ? "ev-dot-running"
                          : skill.status === "failed" ? "ev-dot-failed"
                          : skill.status === "completed" || skill.status === "no_improvement" ? "ev-dot-completed"
                          : "ev-dot-pending"
                        ),
                      }),
                      React.createElement("span", {
                        className: "truncate font-medium text-[13px]",
                        title: skill.name,
                      }, skill.name.replace(/-/g, " ")),
                      isRun && elapsed && React.createElement("span", {
                        className: "text-[10px] text-muted-foreground shrink-0 ml-auto font-courier",
                      }, "[" + elapsed + "]"),
                    ),

                    // Row 2: Size + Model
                    React.createElement("div", { className: "flex items-center gap-2 text-[10px] text-muted-foreground" },
                      React.createElement("span", { className: "font-courier" }, skill.size_kb.toFixed(1) + "KB"),
                      shortModel && React.createElement("span", { className: "truncate" }, shortModel),
                    ),

                    // Row 3: Score
                    skill.improvement != null && React.createElement("div", {
                      className: cn("text-[11px] font-medium", skill.improvement >= 0 ? "text-accent" : "text-destructive"),
                    },
                      (skill.improvement >= 0 ? "+" : "") + (skill.improvement * 100).toFixed(1) + "%"
                    ),
                  ),

                  // No inline detail — uses modal instead
                );
              }),
        );
      }),
    );
  }

  // ── Main page ────────────────────────────────────────────────────────

  function EvolutionHubPage() {
    var _h2 = useState(null);
    var health = _h2[0];
    var setHealth = _h2[1];
    var _q = useState(null);
    var queue = _q[0];
    var setQueue = _q[1];
    var _l = useState(null);
    var logData = _l[0];
    var setLogData = _l[1];
    var _s6 = useState(null);
    var selectedSkill = _s6[0];
    var setSelectedSkill = _s6[1];
    var _e = useState(false);
    var showErrorsOnly = _e[0];
    var setShowErrorsOnly = _e[1];
    var _d = useState(false);
    var downloadingLog = _d[0];
    var setDownloadingLog = _d[1];
    var _prevDone = useRef(0);
    var _view = useState("queue");
    var view = _view[0];
    var setView = _view[1];
    var _ts = useState(null);
    var topologySkill = _ts[0];
    var setTopologySkill = _ts[1];
    var _ms = useState(null);
    var modalSkill = _ms[0];
    var setModalSkill = _ms[1];

    function fetchAll() {
      SDK.fetchJSON("/api/plugins/evolution-hub/batch-health")
        .then(setHealth).catch(function () {});
      SDK.fetchJSON("/api/plugins/evolution-hub/queue-status")
        .then(function (data) {
          setQueue(data);
          // Detect progress change for celebration
          if (data && data.summary) {
            var d = data.summary.completed + data.summary.no_improvement + data.summary.failed;
            if (d > _prevDone.current && _prevDone.current > 0) {
              // Progress was made — triggers celebrate animation on counter
            }
            _prevDone.current = d;
          }
        })
        .catch(function () {});
      var logPath = showErrorsOnly
        ? "/api/plugins/evolution-hub/log/errors"
        : "/api/plugins/evolution-hub/log?tail=" + LOG_LINES;
      SDK.fetchJSON(logPath)
        .then(function (data) {
          if (showErrorsOnly && data.errors) {
            setLogData({ log: data.errors.join("\n"), lines: data.count });
          } else {
            setLogData(data);
          }
        })
        .catch(function () {});
    }

    useEffect(function () {
      fetchAll();
      var iv = setInterval(fetchAll, POLL_INTERVAL);
      return function () { clearInterval(iv); };
    }, [showErrorsOnly]);

    function handleSkillClick(name) {
      if (selectedSkill === name) {
        setSelectedSkill(null);
        setModalSkill(null);
        return;
      }
      setSelectedSkill(name);
      setModalSkill(name);
    }

    function handleTopologyClick(name) {
      setTopologySkill(name);
      setSelectedSkill(name);
    }

    function handleDownloadLog() {
      setDownloadingLog(true);
      SDK.fetchJSON("/api/plugins/evolution-hub/log/download")
        .then(function (data) {
          if (data && data.log) {
            var blob = new Blob([data.log], { type: "text/plain" });
            var url = URL.createObjectURL(blob);
            var a = document.createElement("a");
            a.href = url;
            a.download = "batch_size_aware.log";
            a.click();
            URL.revokeObjectURL(url);
          }
        })
        .catch(function () {})
        .finally(function () { setDownloadingLog(false); });
    }

    // Derived state
    var summary = queue ? queue.summary : null;
    var total = summary ? summary.total : 0;
    var done = summary ? (summary.completed + summary.no_improvement + summary.failed) : 0;
    var batchComplete = queue ? queue.batch_complete : false;
    var healthColor = health ? statusColor(health.status, health.stale) : "text-muted-foreground";
    var isStale = health ? health.stale : false;
    var isRunning = health && health.status === "running" && !isStale;
    var errorCount = summary ? summary.failed : 0;

    return React.createElement("div", {
      className: cn("evo-ambient-glow flex flex-col gap-5", isRunning && "ev-shimmer"),
    },

      // ── Sub-navigation tabs ──
      React.createElement("div", { className: "flex items-center gap-1 border-b border-border pb-2 mb-1" },
        React.createElement(Button, {
          className: cn("text-xs h-7 px-3 rounded-none border-b-2", view === "queue"
            ? "border-accent text-accent" : "border-transparent text-muted-foreground hover:text-foreground"
          ),
          variant: "ghost",
          onClick: function () { setView("queue"); },
        }, "\u2630 Queue"),
        React.createElement(Button, {
          className: cn("text-xs h-7 px-3 rounded-none border-b-2", view === "grid"
            ? "border-accent text-accent" : "border-transparent text-muted-foreground hover:text-foreground"
          ),
          variant: "ghost",
          onClick: function () { setView("grid"); },
        }, "\u25A6 Grid"),
        React.createElement(Button, {
          className: cn("text-xs h-7 px-3 rounded-none border-b-2", view === "topology"
            ? "border-accent text-accent" : "border-transparent text-muted-foreground hover:text-foreground"
          ),
          variant: "ghost",
          onClick: function () { setView("topology"); },
        }, "\u25C9 Topology"),
      ),

      // ── Queue View ──
      view === "queue" && React.createElement(React.Fragment, null,

      // ── Batch Complete Banner ──
      batchComplete && React.createElement(Card, {
        className: "card-notched border border-success/40 bg-success/5 ev-celebrate",
      },
        React.createElement(CardContent, { className: "flex items-center gap-3 py-3" },
          React.createElement("span", { className: "text-2xl" }, "\u2714\uFE0F"),
          React.createElement("div", null,
            React.createElement("div", { className: "font-semibold text-sm text-success" },
              "Batch Complete \u2014 " + total + "/" + total + " skills processed"
            ),
            React.createElement("div", { className: "text-xs text-muted-foreground mt-0.5" },
              "All skills have been evolved. Use the sidebar controls to reset and run again, or download the log for a post-mortem report."
            ),
          ),
        ),
      ),

      // ── 3 Status Cards ──
      React.createElement("div", { className: "grid grid-cols-3 gap-4" },
        React.createElement(Card, { className: "card-notched" },
          React.createElement(CardHeader, { className: "pb-2 flex flex-row items-center justify-between" },
            React.createElement(CardTitle, { className: "text-xs font-medium text-muted-foreground uppercase tracking-wider" }, "Batch Status"),
            isRunning && React.createElement(Badge, {
              className: "ev-live-badge text-[10px] h-4 px-1.5 bg-success/20 text-success border-success/40",
              variant: "outline",
            }, "LIVE"),
          ),
          React.createElement(CardContent, { className: "pt-0" },
            React.createElement("div", { className: "flex items-center gap-2" },
              React.createElement("div", {
                className: cn(
                  "w-3 h-3 rounded-full",
                  isStale ? "bg-destructive ev-dot-stale"
                  : isRunning ? "bg-success ev-dot-running"
                  : "bg-muted-foreground"
                ),
              }),
              React.createElement("span", { className: "text-lg font-semibold " + healthColor },
                health ? (isStale ? "Stale" : health.status) : "\u2014"
              ),
            ),
            React.createElement("div", { className: "text-xs text-muted-foreground mt-1" },
              isStale
                ? React.createElement("div", { className: "flex items-center gap-2" },
                    React.createElement("span", null, "Heartbeat >10min old \u2014 batch may be down"),
                    React.createElement(Button, {
                      className: "text-xs h-6 px-2 ml-auto",
                      variant: "destructive",
                      onClick: function () {
                        SDK.fetchJSON("/api/plugins/evolution-hub/control", {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ action: "start" }),
                        });
                      },
                    }, "\u21BA Restart Batch"),
                  )
              : health && health.loop_step ? "Step: " + health.loop_step
              : "\u2014"
            ),
          ),
        ),
        React.createElement(Card, { className: "card-notched" },
          React.createElement(CardHeader, { className: "pb-2" },
            React.createElement(CardTitle, { className: "text-xs font-medium text-muted-foreground uppercase tracking-wider" }, "Skills Progress"),
          ),
          React.createElement(CardContent, { className: "pt-0" },
            React.createElement("div", { className: "flex items-baseline gap-2" },
              React.createElement(AnimatedCount, {
                value: done,
                className: "text-lg font-semibold",
              }),
              React.createElement("span", { className: "text-xs text-muted-foreground" }, "/ " + total + (batchComplete ? " complete" : " done")),
            ),
            summary && React.createElement("div", { className: "flex gap-3 mt-1 text-xs text-muted-foreground" },
              React.createElement("span", null, "\u25B6 " + summary.running),
              React.createElement("span", { className: "text-accent" }, "\u2713 " + (summary.completed + summary.no_improvement)),
              React.createElement("span", {
                className: cn("text-destructive", errorCount > 0 && "ev-error-glow inline-block rounded px-0.5"),
              }, "\u2717 " + errorCount),
              React.createElement("span", { className: "text-muted-foreground" }, "\u25CB " + summary.pending),
            ),
            React.createElement(ProgressBar, {
              done: done,
              total: total,
              batchComplete: batchComplete,
            }),
          ),
        ),
        React.createElement(Card, { className: "card-notched" },
          React.createElement(CardHeader, { className: "pb-2" },
            React.createElement(CardTitle, { className: "text-xs font-medium text-muted-foreground uppercase tracking-wider" }, "Last Heartbeat"),
          ),
          React.createElement(CardContent, { className: "pt-0" },
            React.createElement("span", { className: "text-lg font-semibold font-courier" },
              health && health.last_heartbeat
                ? (function () {
                    try {
                      var t = timeAgo(health.last_heartbeat);
                      return (typeof t === "string" && t.length > 0 && !t.includes("NaN")) ? t : health.last_heartbeat.substring(0, 19) + "Z";
                    } catch(e) {
                      return health.last_heartbeat.substring(0, 19) + "Z";
                    }
                  })()
                : "\u2014"
            ),
            React.createElement("div", { className: "text-xs text-muted-foreground mt-1" },
              health && health.last_heartbeat ? health.last_heartbeat : "No heartbeat yet"
            ),
          ),
        ),
      ),

      // ── Queue Table ──
      React.createElement(Card, { className: "card-notched" },
        React.createElement(CardHeader, { className: "pb-2 flex flex-row items-center justify-between" },
          React.createElement("div", null,
            React.createElement(CardTitle, { className: "text-sm font-medium" }, "Evolution Queue"),
            React.createElement("span", { className: "text-xs text-muted-foreground mt-1 block" },
              "Sorted by size (smallest first) \u2014 polling every " + (POLL_INTERVAL / 1000) + "s"
            ),
          ),
          summary && React.createElement(Badge, {
            variant: "outline",
            className: cn("text-xs", isRunning && "ev-live-badge border-success/40 text-success"),
          },
            summary.running > 0 ? "Running" : (batchComplete ? "Complete" : "Idle")
          ),
        ),
        React.createElement(CardContent, { className: "pt-0 overflow-x-auto" },
          React.createElement("div", {
            className: "grid grid-cols-10 gap-1 text-xs font-medium text-muted-foreground uppercase tracking-wider px-2 py-1 border-b border-border"
          },
            React.createElement("span", { className: "col-span-1" }, ""),
            React.createElement("span", { className: "col-span-3" }, "Skill"),
            React.createElement("span", { className: "col-span-1 text-right" }, "KB"),
            React.createElement("span", { className: "col-span-2" }, "Status"),
            React.createElement("span", { className: "col-span-2 truncate" }, "Model"),
            React.createElement("span", { className: "col-span-1 text-right" }, "Score"),
          ),
          queue && queue.skills && queue.skills.length > 0
            ? queue.skills.map(function (skill) {
                var badge = skillStatusBadge(skill.status);
                var isSelected = selectedSkill === skill.name;
                var isRun = skill.status === "running";
                var elapsed = isRun ? elapsedSince(skill.last_evolved) : null;
                return React.createElement("div", { key: skill.name },
                  // Main row
                  React.createElement("div", {
                    className: cn(
                      "evolution-queue-row grid grid-cols-10 gap-1 text-xs items-center px-2 py-1 border-b border-border/40 cursor-pointer",
                      isSelected && "bg-accent/10",
                      isRun && "ev-row-running"
                    ),
                    onClick: function () { handleSkillClick(skill.name); },
                    title: skill.error || skill.name,
                  },
                    React.createElement("div", { className: "col-span-1 flex items-center" },
                      React.createElement(StatusIcon, { status: skill.status }),
                    ),
                    React.createElement("span", { className: "col-span-3 truncate font-medium flex items-center gap-1", title: skill.name },
                      React.createElement("span", { className: "truncate" }, skill.name),
                      elapsed && React.createElement("span", { className: "text-[10px] text-muted-foreground shrink-0 font-courier" }, "[" + elapsed + "]"),
                    ),
                    React.createElement("span", { className: "col-span-1 text-right font-courier text-muted-foreground flex items-center justify-end" },
                      skill.size_kb.toFixed(1)
                    ),
                    React.createElement("span", { className: "col-span-2 flex items-center " + badge.cls },
                      badge.label
                    ),
                    React.createElement("span", { className: "col-span-2 truncate font-courier text-muted-foreground flex items-center", title: skill.model || "" },
                      skill.model ? skill.model.split("/").pop() : "\u2014"
                    ),
                    React.createElement("span", { className: "col-span-1 text-right font-courier flex items-center justify-end" },
                      skill.improvement != null
                        ? (skill.improvement >= 0 ? "+" : "") + (skill.improvement * 100).toFixed(1) + "%"
                        : "\u2014"
                    ),
                  ),
                  // No inline detail — uses modal instead
                );
              })
            : React.createElement("div", { className: "text-xs text-muted-foreground text-center py-4" },
                queue === null ? "Loading..." : "No skills in queue"
              ),
        ),
      ),

      // ── Log Viewer ──
      React.createElement(Card, { className: "card-notched" },
        React.createElement(CardHeader, { className: "pb-2 flex flex-row items-center justify-between" },
          React.createElement(CardTitle, { className: "text-sm font-medium" }, "Batch Log"),
          React.createElement("div", { className: "flex items-center gap-2" },
            React.createElement(Button, {
              className: cn("text-xs h-7 px-2", showErrorsOnly && "ev-error-glow"),
              variant: showErrorsOnly ? "destructive" : "outline",
              onClick: function () { setShowErrorsOnly(!showErrorsOnly); },
              title: showErrorsOnly ? "Show all log lines" : "Show errors only",
            }, showErrorsOnly ? "\u26A0 Errors" : "All"),
            React.createElement(Button, {
              className: "text-xs h-7 px-2",
              variant: "outline",
              onClick: handleDownloadLog,
              disabled: downloadingLog,
              title: "Download full log for post-mortem analysis",
            }, downloadingLog ? "..." : "\u2B07"),
            React.createElement("span", { className: "text-xs text-muted-foreground" },
              showErrorsOnly ? "Errors" : "Last " + (logData ? logData.lines : 0) + " lines"
            ),
          ),
        ),
        React.createElement(CardContent, { className: "pt-0" },
          React.createElement(LogViewer, { logData: logData, className: "max-h-80" }),
        ),
      ),
      ), // end Fragment (queue view)

      // ── Topology View ──
      view === "topology" && React.createElement(Card, { className: "card-notched" },
        React.createElement(CardHeader, { className: "pb-2" },
          React.createElement(CardTitle, { className: "text-sm font-medium" }, "Pipeline Topology"),
          React.createElement("span", { className: "text-xs text-muted-foreground mt-1 block" },
            "44 skills arranged by size (inner = small, outer = large) \u2014 y-axis reflects score improvement \u2014 click a node to inspect"
          ),
        ),
        React.createElement(CardContent, { className: "pt-0 relative" },
          React.createElement(TopologyView, { skills: queue ? queue.skills : [], onNodeClick: handleTopologyClick }),

          // Action overlay for clicked topology node
          topologySkill && React.createElement("div", {
            className: "absolute bottom-2 left-2 flex items-center gap-2 p-2 rounded border border-border/40",
            style: { background: "rgba(15, 23, 42, 0.92)", backdropFilter: "blur(8px)", zIndex: 10 },
          },
            React.createElement("span", { className: "text-xs font-medium" }, topologySkill),
            React.createElement(Button, {
              className: "text-xs h-6 px-2",
              variant: "outline",
              onClick: function () { setView("queue"); },
            }, "\u2630 View in Queue"),
            React.createElement(Button, {
              className: "text-xs h-6 px-2",
              variant: "ghost",
              onClick: function () { setTopologySkill(null); },
            }, "\u2716"),
          ),
        ),
      ),

      // ── Grid View ──
      view === "grid" && React.createElement(GridView, {
        skills: queue ? queue.skills : [],
        selectedSkill: selectedSkill,
        onSkillClick: handleSkillClick,
      }),

      // ── Skill Detail Modal ──
      modalSkill && React.createElement(EvoModal, {
        skillName: modalSkill,
        onClose: function () { setModalSkill(null); setSelectedSkill(null); },
      }),

    ); // end outer div
  }

  // ── Generative Canvas (header-banner slot) ──────────────────────────
  // Perlin noise flow-field particle system with k-means color extraction.
  // Writes --gen-palette-* CSS vars on :root for real-time theme cascade.
  // Adapted from Generative Cockpit's GenCanvas pattern.

  var GenCanvas = (function () {
    var p5Inst = null;
    var frameCount = 0;
    var palette = { bg: "#0a0e1a", mid: "#3b82f6", fg: "#60a5fa" };
    var particles = [];
    var NUM_P = 120;
    var running = false;

    function fbm(x, y, o) {
      o = o || 4;
      var v = 0, a = 1, f = 1, s = 0;
      for (var i = 0; i < o; i++) { v += p5Inst.noise(x*f, y*f)*a; s += a; a *= 0.5; f *= 2; }
      return v / s;
    }

    function init(p) {
      p.disableFriendlyErrors = true;
      p.createCanvas(p.windowWidth, 120);
      p.colorMode(p.HSB, 360, 100, 100, 100);
      p.noStroke();
      for (var i = 0; i < NUM_P; i++) {
        particles.push({
          x: p.random(p.width), y: p.random(p.height),
          speed: p.random(0.6, 2.0), sz: p.random(1.5, 4.0),
          hue: p.random(180, 260), life: p.random(1),
          dec: p.random(0.003, 0.01), osc: p.random(p.TWO_PI)
        });
      }
    }

    function aurora(p) {
      p.background(0, 0, 3, 18);
      var t = p.millis() * 0.0003;
      for (var i = 0; i < particles.length; i++) {
        var pt = particles[i];
        var n = fbm(pt.x*0.002 + t*0.3, pt.y*0.002 + t*0.15);
        pt.x += Math.cos(n * p.TWO_PI * 2) * pt.speed;
        pt.y += Math.sin(n * p.TWO_PI * 2) * pt.speed;
        pt.life -= pt.dec; pt.osc += 0.05;
        pt.hue = (pt.hue + 0.08) % 360;
        if (pt.life <= 0 || pt.x < 0 || pt.x > p.width || pt.y < 0 || pt.y > p.height) {
          pt.x = p.random(p.width); pt.y = p.random(p.height);
          pt.life = 1; pt.hue = 180 + p.random(80);
        }
        p.fill(pt.hue, 70, 70 + Math.sin(pt.osc)*15, pt.life * 55);
        p.ellipse(pt.x, pt.y, pt.sz);
      }
    }

    function extractColors(p) {
      if (frameCount % 15 !== 0) return;
      p.loadPixels();
      var d = p.pixels;
      var samples = [];
      var step = Math.max(1, Math.floor(d.length / 16000 / 4));
      for (var i = 0; i < d.length; i += step * 4) {
        if (d[i+3] > 100) {
          var br = (d[i] + d[i+1] + d[i+2]) / 3;
          if (br > 5 && br < 240) samples.push([d[i], d[i+1], d[i+2]]);
        }
      }
      if (samples.length < 9) return;

      var cents = [
        samples[Math.floor(Math.random() * samples.length)],
        samples[Math.floor(Math.random() * samples.length)],
        samples[Math.floor(Math.random() * samples.length)]
      ];
      for (var iter = 0; iter < 6; iter++) {
        var clusters = [[], [], []];
        for (var s = 0; s < samples.length; s++) {
          var minDist = Infinity, assign = 0;
          for (var c = 0; c < 3; c++) {
            var dr = samples[s][0] - cents[c][0], dg = samples[s][1] - cents[c][1], db = samples[s][2] - cents[c][2];
            var dist = dr*dr + dg*dg + db*db;
            if (dist < minDist) { minDist = dist; assign = c; }
          }
          clusters[assign].push(samples[s]);
        }
        for (var c2 = 0; c2 < 3; c2++) {
          if (clusters[c2].length > 0) {
            var sr = 0, sg = 0, sb = 0;
            for (var s2 = 0; s2 < clusters[c2].length; s2++) { sr += clusters[c2][s2][0]; sg += clusters[c2][s2][1]; sb += clusters[c2][s2][2]; }
            cents[c2] = [Math.round(sr/clusters[c2].length), Math.round(sg/clusters[c2].length), Math.round(sb/clusters[c2].length)];
          }
        }
      }
      cents.sort(function (a, b) {
        return (a[0]*0.299 + a[1]*0.587 + a[2]*0.114) - (b[0]*0.299 + b[1]*0.587 + b[2]*0.114);
      });
      var toHex = function(v) { return Math.min(255, Math.max(0, v)).toString(16).padStart(2, "0"); };
      palette = {
        bg:  "#" + cents[0].map(toHex).join(""),
        mid: "#" + cents[1].map(toHex).join(""),
        fg:  "#" + cents[2].map(toHex).join("")
      };
      var root = document.documentElement;
      root.style.setProperty("--gen-palette-bg", palette.bg);
      root.style.setProperty("--gen-palette-mid", palette.mid);
      root.style.setProperty("--gen-palette-fg", palette.fg);
    }

    function draw(p) {
      frameCount++;
      aurora(p);
      extractColors(p);
    }

    function initP5() {
      var Sketch = function(p) {
        p.setup = function() { init(p); };
        p.draw  = function() { draw(p); };
        p.windowResized = function() { p.resizeCanvas(p.windowWidth, 120); };
      };
      p5Inst = new window.p5(Sketch, document.getElementById("evo-gen-canvas"));
    }

    function start() {
      if (running) return;
      running = true;
      if (window.p5) { initP5(); return; }
      // p5.js may already be loading from the topology CDN — poll briefly
      var attempts = 0;
      var iv = setInterval(function () {
        if (window.p5) { clearInterval(iv); initP5(); }
        else if (++attempts > 25) { clearInterval(iv); console.warn("[evo] p5 CDN timeout"); }
      }, 200);
    }

    function stop() {
      running = false;
      if (p5Inst) { p5Inst.remove(); p5Inst = null; }
    }

    return {
      start: start,
      stop: stop,
      getPalette: function() { return palette; },
    };
  })();

  // ── Header Banner Content (header-banner slot) ─────────────────────

  function HeaderBannerContent() {
    var _pal = useState({ bg: "#0a0e1a", mid: "#3b82f6", fg: "#60a5fa" });
    var pal = _pal[0];
    var setPal = _pal[1];

    useEffect(function () {
      GenCanvas.start();
      var iv = setInterval(function () { setPal(Object.assign({}, GenCanvas.getPalette())); }, 1200);
      return function () { clearInterval(iv); GenCanvas.stop(); };
    }, []);

    return React.createElement("div", {
      style: {
        display: "flex", alignItems: "center", gap: "0.75rem",
        padding: "0 0.5rem", height: "100%",
      }
    },
      React.createElement("div", {
        id: "evo-gen-canvas",
        style: {
          width: "200px", height: "100px", borderRadius: "6px",
          overflow: "hidden", flexShrink: 0,
          border: "1px solid rgba(59, 130, 246, 0.15)",
        }
      }),
      React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: "0.15rem" } },
        React.createElement("span", {
          style: { fontSize: "0.6rem", letterSpacing: "0.1em", opacity: 0.6, color: "var(--gen-palette-mid, #3b82f6)", fontFamily: "JetBrains Mono, monospace" }
        }, "EVOLUTION HUB"),
        React.createElement("div", { style: { display: "flex", gap: "4px" } },
          React.createElement("div", { style: { width: 10, height: 10, borderRadius: "50%", backgroundColor: pal.bg, border: "1px solid rgba(255,255,255,0.15)", boxShadow: "0 0 4px " + pal.bg, transition: "background-color 0.8s ease, box-shadow 0.8s ease" } }),
          React.createElement("div", { style: { width: 10, height: 10, borderRadius: "50%", backgroundColor: pal.mid, border: "1px solid rgba(255,255,255,0.15)", boxShadow: "0 0 4px " + pal.mid, transition: "background-color 0.8s ease, box-shadow 0.8s ease" } }),
          React.createElement("div", { style: { width: 10, height: 10, borderRadius: "50%", backgroundColor: pal.fg, border: "1px solid rgba(255,255,255,0.15)", boxShadow: "0 0 4px " + pal.fg, transition: "background-color 0.8s ease, box-shadow 0.8s ease" } }),
        ),
      ),
    );
  }

  // ── Registration ─────────────────────────────────────────────────────

  window.__HERMES_PLUGINS__.register("evolution-hub", EvolutionHubPage);
  window.__HERMES_PLUGINS__.registerSlot("evolution-hub", "sidebar", SidebarControls);
  window.__HERMES_PLUGINS__.registerSlot("evolution-hub", "header-banner", HeaderBannerContent);
})();
