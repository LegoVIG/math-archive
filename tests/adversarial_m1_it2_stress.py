"""Adversarial Challenger M1 Iteration 2 Stress Test Suite.

Adversarially stress-tests:
1. Data schema parsing & mutation isolation across all 4 subjects.
2. Hash routing edge cases: malformed hashes, cross-subject topic jumps (#alg/t1 vs #komb/t1), rapid fuzzing.
3. Responsive layout scaling across desktop (1920x1080, 1280x800), tablet (768x1024), and mobile (375x667, 320x568).
"""

import hashlib
import http.server
import json
import os
import re
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding='utf-8')

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
MATH_ARCHIVE_DIR = ROOT_DIR / "math-archive"
TESTS_DIR = MATH_ARCHIVE_DIR / "tests"

RUNNER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>M1 Iteration 2 Adversarial Stress Runner</title>
</head>
<body style="margin:0;padding:0;background:#111;color:#eee;font-family:sans-serif;">
  <div style="padding:10px;background:#222;font-size:14px;">
    <b>Adversarial Stress Harness M1_it2_2</b> | <span id="status">Loading...</span>
  </div>
  <iframe id="app" src="/math-archive/index.html" style="width:1280px;height:900px;border:none;"></iframe>

  <script>
  (function() {
    var results = { passes: [], failures: [], uncaughtErrors: [] };

    function logPass(msg) {
      console.log('[PASS] ' + msg);
      results.passes.push(msg);
    }
    function logFail(msg, detail) {
      console.error('[FAIL] ' + msg, detail);
      results.failures.push({ message: msg, detail: String(detail || '') });
    }

    window.onerror = function(msg, url, line, col, err) {
      var errStr = 'TOP: ' + msg + ' at ' + url + ':' + line;
      results.uncaughtErrors.push(errStr);
    };

    var iframe = document.getElementById('app');
    var statusEl = document.getElementById('status');

    function sendReport() {
      fetch('/api/results', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(results)
      }).catch(function(e) { console.error('Report error:', e); });
    }

    iframe.onload = function() {
      var win = iframe.contentWindow;
      var doc = iframe.contentDocument;

      win.onerror = function(msg, url, line, col, err) {
        var errStr = 'IFRAME: ' + msg + ' at ' + url + ':' + line;
        results.uncaughtErrors.push(errStr);
      };

      statusEl.textContent = 'Running adversarial stress tests...';
      runStress(win, doc)
        .catch(function(e) {
          logFail('Fatal unhandled error in runStress', e.stack || e);
        })
        .finally(function() {
          sendReport();
        });
    };

    function wait(ms) {
      return new Promise(function(resolve) { setTimeout(resolve, ms); });
    }

    async function setViewport(w, h) {
      iframe.style.width = w + 'px';
      iframe.style.height = h + 'px';
      iframe.contentWindow.dispatchEvent(new Event('resize'));
      await wait(60);
    }

    async function runStress(win, doc) {
      // =========================================================================
      // SUITE 1: Data Schema Parsing & Mutation Isolation
      // =========================================================================
      try {
        // 1.1 Verify getSubjectMeta for all 4 domains
        var domains = ['combinatorics', 'algebra', 'number-theory', 'geometry'];
        for (var i = 0; i < domains.length; i++) {
          var d = domains[i];
          var meta = win.getSubjectMeta(d);
          if (!meta || typeof meta.total !== 'number') {
            logFail(d + ': getSubjectMeta returned invalid meta');
          } else if (d === 'combinatorics' && meta.total !== 3014) {
            logFail('combinatorics: expected total 3014, got ' + meta.total);
          } else if (d !== 'combinatorics' && meta.total !== 0) {
            logFail(d + ': expected empty total 0, got ' + meta.total);
          } else {
            logPass(d + ': getSubjectMeta schema validated (total=' + meta.total + ')');
          }
        }

        // 1.2 In-Memory Mutation Isolation Test
        win.reindexSubjectData('algebra');
        var algMeta = win.META;
        // Deliberately mutate algebra's in-memory chapter array and PIDX
        algMeta.ch.push({ n: 999, t: 'Poison Topic', secs: [] });
        win.PIDX['999.1.1'] = { ch: 999, sec: 1, d: 1 };
        
        // Switch to number-theory and verify it is not contaminated
        win.reindexSubjectData('number-theory');
        if (win.META.ch.length === 0 && !win.PIDX['999.1.1']) {
          logPass('Mutation isolation: number-theory unaffected by algebra in-memory mutation');
        } else {
          logFail('Mutation isolation failure: number-theory contaminated with ' + JSON.stringify(win.META.ch));
        }

        // Switch to combinatorics and verify integrity
        win.reindexSubjectData('combinatorics');
        if (win.META.ch.length === 29 && win.ALL_IDS.length === 3014 && !win.PIDX['999.1.1']) {
          logPass('Mutation isolation: combinatorics untouched (29 topics, 3014 tasks)');
        } else {
          logFail('Mutation isolation failure: combinatorics affected, topics=' + win.META.ch.length + ', tasks=' + win.ALL_IDS.length);
        }

        // Switch back to algebra; reindexSubjectData should re-fetch a clean template
        win.reindexSubjectData('algebra');
        if (win.META.ch.length === 0 && !win.PIDX['999.1.1']) {
          logPass('Mutation isolation: algebra re-indexed with clean zero-state template');
        } else {
          logFail('Mutation persistence failure: algebra retained mutated state');
        }

        // Switch back to combinatorics for subsequent router tests
        win.reindexSubjectData('combinatorics');
      } catch (e) {
        logFail('Suite 1 exception', e.stack || e);
      }

      // =========================================================================
      // SUITE 2: Hash Routing Edge Cases & Cross-Subject Jumps
      // =========================================================================
      try {
        // 2.1 Cross-subject topic jump #alg/t1 vs #komb/t1
        var r1 = win.parseHash('#alg/t1');
        if (r1.v === 'map' && win.curSubject === 'algebra') {
          logPass('Routing #alg/t1: falls back to map view in algebra (no t1 in empty algebra)');
        } else {
          logFail('Routing #alg/t1: unexpected result ' + JSON.stringify(r1) + ' subj=' + win.curSubject);
        }

        var r2 = win.parseHash('#komb/t1');
        if (r2.v === 'topic' && r2.ch === 1 && win.curSubject === 'combinatorics') {
          logPass('Routing #komb/t1: resolves to topic 1 in combinatorics');
        } else {
          logFail('Routing #komb/t1: unexpected result ' + JSON.stringify(r2) + ' subj=' + win.curSubject);
        }

        // 2.2 Cross-subject problem jump #geom/p1.1.1 vs #komb/p1.1.1
        var r3 = win.parseHash('#geom/p1.1.1');
        if (r3.v === 'map' && win.curSubject === 'geometry') {
          logPass('Routing #geom/p1.1.1: falls back to map in geometry (problem does not exist)');
        } else {
          logFail('Routing #geom/p1.1.1: unexpected result ' + JSON.stringify(r3) + ' subj=' + win.curSubject);
        }

        var r4 = win.parseHash('#komb/p1.1.1');
        if (r4.v === 'topic' && r4.ch === 1 && r4.pid === '1.1.1' && win.curSubject === 'combinatorics') {
          logPass('Routing #komb/p1.1.1: resolves to topic 1, problem 1.1.1 in combinatorics');
        } else {
          logFail('Routing #komb/p1.1.1: unexpected result ' + JSON.stringify(r4) + ' subj=' + win.curSubject);
        }

        // 2.3 Bare subject slugs with uppercase & mixed case
        var slugs = [
          { hash: '#ALG', expected: 'algebra' },
          { hash: '#Geom', expected: 'geometry' },
          { hash: '#NT', expected: 'number-theory' },
          { hash: '#Komb', expected: 'combinatorics' },
          { hash: '#algebra', expected: 'algebra' },
          { hash: '#geometry', expected: 'geometry' },
          { hash: '#number-theory', expected: 'number-theory' },
          { hash: '#combinatorics', expected: 'combinatorics' }
        ];
        for (var j = 0; j < slugs.length; j++) {
          var s = slugs[j];
          var route = win.parseHash(s.hash);
          if (route.v === 'map' && win.curSubject === s.expected) {
            logPass('Routing slug ' + s.hash + ' -> resolved to ' + s.expected);
          } else {
            logFail('Routing slug ' + s.hash + ' failed: ' + JSON.stringify(route) + ' subj=' + win.curSubject);
          }
        }

        // 2.4 Malformed, extreme & boundary hashes
        var adversarialHashes = [
          '#',
          '##',
          '###',
          '#/',
          '#///',
          '#../../../etc/passwd',
          '#<script>alert(1)</script>',
          '#??foo=bar&baz=1',
          '#invalid_domain_xyz',
          '#t0',
          '#t-1',
          '#t9999',
          '#t1-0',
          '#t1-9999',
          '#p0.0.0',
          '#p99.99.99',
          '#p1.1.9999',
          '#komb/invalid/deep/nested/path',
          '#null',
          '#undefined',
          '#NaN',
          '#%20%20%20',
          '#komb/t1/extra/slashes/after',
          '#alg/t999'
        ];

        for (var k = 0; k < adversarialHashes.length; k++) {
          var badHash = adversarialHashes[k];
          var res = win.parseHash(badHash);
          if (res && res.v) {
            logPass('Malformed hash safely handled: ' + JSON.stringify(badHash) + ' -> ' + res.v);
          } else {
            logFail('Malformed hash crashed or returned null: ' + badHash);
          }
        }

        // 2.5 Live DOM routing through window.location.hash changes
        win.location.hash = '#komb';
        win.dispatchEvent(new win.Event('popstate'));
        await wait(750);
        win.location.hash = '#t1';
        win.dispatchEvent(new win.Event('popstate'));
        await wait(750);
        var topicEl = doc.querySelector('article.topic');
        if (topicEl) {
          logPass('Live DOM routing to #t1 rendered article.topic element');
        } else {
          logFail('Live DOM routing to #t1 failed to render article.topic');
        }

        // Jump to algebra via hash
        win.location.hash = '#alg';
        win.dispatchEvent(new win.Event('popstate'));
        await wait(750);
        var emptyMap = doc.querySelector('.empty-map');
        if (emptyMap && win.curSubject === 'algebra') {
          logPass('Live DOM routing from topic to #alg switched to algebra empty-map');
        } else {
          logFail('Live DOM routing to #alg failed, emptyMap=' + !!emptyMap + ' subj=' + win.curSubject);
        }

        // Return to combinatorics map
        win.location.hash = '#komb';
        win.dispatchEvent(new win.Event('popstate'));
        await wait(750);
        var mapNodes = doc.querySelectorAll('#map .node');
        if (mapNodes.length > 25 && win.curSubject === 'combinatorics') {
          logPass('Live DOM routing back to #komb restored graph nodes (' + mapNodes.length + ')');
        } else {
          logFail('Live DOM routing back to #komb failed, nodes=' + mapNodes.length);
        }
      } catch (e) {
        logFail('Suite 2 exception', e.stack || e);
      }

      // =========================================================================
      // SUITE 3: Responsive Layout Scaling Across Viewports
      // =========================================================================
      try {
        var barEl = doc.getElementById('bar');
        var subjNav = doc.querySelector('.subj-nav');

        // 3.1 Desktop: 1920x1080
        await setViewport(1920, 1080);
        var barW_1920 = barEl.scrollWidth;
        var clientW_1920 = barEl.clientWidth;
        if (barW_1920 <= clientW_1920) {
          logPass('Desktop 1920x1080: .bar fits perfectly without overflow (' + barW_1920 + 'px <= ' + clientW_1920 + 'px)');
        } else {
          logFail('Desktop 1920x1080: .bar overflows by ' + (barW_1920 - clientW_1920) + 'px');
        }

        var fullLabel1920 = doc.querySelector('.subj-label-full');
        var fullStyle1920 = fullLabel1920 ? win.getComputedStyle(fullLabel1920).display : 'none';
        if (fullStyle1920 !== 'none') {
          logPass('Desktop 1920x1080: full labels visible (display=' + fullStyle1920 + ')');
        } else {
          logFail('Desktop 1920x1080: full labels unexpectedly hidden');
        }

        // 3.2 Tablet: 768x1024
        await setViewport(768, 1024);
        var barW_768 = barEl.scrollWidth;
        var clientW_768 = barEl.clientWidth;
        if (barW_768 <= clientW_768) {
          logPass('Tablet 768x1024: .bar fits without overflow (' + barW_768 + 'px <= ' + clientW_768 + 'px)');
        } else {
          logFail('Tablet 768x1024: .bar overflows by ' + (barW_768 - clientW_768) + 'px');
        }

        var shortLabel768 = doc.querySelector('.subj-label-short');
        var shortStyle768 = shortLabel768 ? win.getComputedStyle(shortLabel768).display : 'none';
        var fullStyle768 = fullLabel1920 ? win.getComputedStyle(fullLabel1920).display : 'none';
        if (shortStyle768 !== 'none' && fullStyle768 === 'none') {
          logPass('Tablet 768x1024: short labels active, full labels hidden');
        } else {
          logFail('Tablet 768x1024: label responsive query failed (short=' + shortStyle768 + ', full=' + fullStyle768 + ')');
        }

        // 3.3 Mobile: 375x667 (Standard iPhone)
        await setViewport(375, 667);
        var barW_375 = barEl.scrollWidth;
        var clientW_375 = barEl.clientWidth;
        if (barW_375 <= clientW_375) {
          logPass('Mobile 375x667: .bar fits without overflow (' + barW_375 + 'px <= ' + clientW_375 + 'px)');
        } else {
          logFail('Mobile 375x667: .bar overflows by ' + (barW_375 - clientW_375) + 'px');
        }

        var navBtns375 = doc.querySelectorAll('.subj-btn');
        if (navBtns375.length === 4) {
          logPass('Mobile 375x667: all 4 subject buttons remain present');
        } else {
          logFail('Mobile 375x667: missing subject buttons');
        }

        // Check empty map on mobile
        win.switchSubject('algebra');
        await wait(100);
        var emptyMap375 = doc.querySelector('.empty-map');
        if (emptyMap375 && emptyMap375.scrollWidth <= 375) {
          logPass('Mobile 375x667: algebra .empty-map fits cleanly within 375px');
        } else {
          logFail('Mobile 375x667: .empty-map overflow');
        }

        // 3.4 Ultra-Narrow Mobile: 320x568 (iPhone SE / 5 / mini)
        await setViewport(320, 568);
        var barW_320 = barEl.scrollWidth;
        var clientW_320 = barEl.clientWidth;
        if (barW_320 <= clientW_320) {
          logPass('Ultra-Narrow 320x568: .bar fits without overflow (' + barW_320 + 'px <= ' + clientW_320 + 'px)');
        } else {
          logFail('Ultra-Narrow 320x568: .bar overflows by ' + (barW_320 - clientW_320) + 'px');
        }

        // Under 380px, labels should be hidden (icon-only mode)
        var labelEl = doc.querySelector('.subj-btn span:not(.subj-icon)');
        var labelStyle320 = labelEl ? win.getComputedStyle(labelEl).display : 'none';
        if (labelStyle320 === 'none') {
          logPass('Ultra-Narrow 320x568: icon-only mode activated, text labels suppressed');
        } else {
          logFail('Ultra-Narrow 320x568: text labels not suppressed (display=' + labelStyle320 + ')');
        }

        // Test clicking navigation buttons on 320px
        var geomBtn = doc.querySelector('.subj-btn[data-subject="geometry"]');
        if (geomBtn) {
          geomBtn.click();
          await wait(150);
          if (win.curSubject === 'geometry') {
            logPass('Ultra-Narrow 320x568: button click switched to geometry successfully');
          } else {
            logFail('Ultra-Narrow 320x568: click failed to switch to geometry');
          }
        }

        // Reset to desktop
        await setViewport(1280, 900);
        win.switchSubject('combinatorics');
        await wait(100);
        logPass('Viewport restored to 1280x900 desktop default');
      } catch (e) {
        logFail('Suite 3 exception', e.stack || e);
      }
    }
  })();
  </script>
</body>
</html>
"""


class StressHttpHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT_DIR), **kwargs)

    def do_POST(self):
        if self.path == "/api/results":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            self.server.test_results = json.loads(body)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Keep test runner output clean
        pass


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def run_adversarial_stress():
    print("================================================================================")
    print("  Milestone 1 Iteration 2 Adversarial Challenger Stress Test")
    print("================================================================================")

    # 1. Write the runner HTML
    runner_file = TESTS_DIR / "adversarial_stress_runner.html"
    runner_file.write_text(RUNNER_HTML, encoding="utf-8")

    port = get_free_port()
    server = socketserver.TCPServer(("127.0.0.1", port), StressHttpHandler)
    server.test_results = None

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    runner_url = f"http://127.0.0.1:{port}/math-archive/tests/adversarial_stress_runner.html"

    # Locate Edge or Chrome
    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    browser_exe = None
    for p in edge_paths:
        if os.path.exists(p):
            browser_exe = p
            break

    if not browser_exe:
        print("[FAIL] No supported browser found (Edge/Chrome).")
        server.shutdown()
        return False

    print(f"Launching headless browser: {browser_exe}")
    browser_cmd = [
        browser_exe,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-extensions",
        "--window-size=1400,1000",
        runner_url,
    ]

    proc = subprocess.Popen(browser_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    t_start = time.time()
    timeout = 40.0
    while time.time() - t_start < timeout:
        if server.test_results is not None:
            break
        time.sleep(0.3)

    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        proc.kill()

    server.shutdown()
    if runner_file.exists():
        try:
            runner_file.unlink()
        except Exception:
            pass

    results = server.test_results
    if not results:
        print("[FAIL] Stress test timed out without receiving results report!")
        return False

    passes = results.get("passes", [])
    failures = results.get("failures", [])
    errors = results.get("uncaughtErrors", [])

    print(f"\nCompleted in {time.time() - t_start:.2f}s:")
    print(f"  Passes:   {len(passes)}")
    print(f"  Failures: {len(failures)}")
    print(f"  Errors:   {len(errors)}")

    for p in passes:
        print(f"  {p}")

    if failures:
        print("\n--- FAILURES ---")
        for f in failures:
            print(f"  [FAIL] {f.get('message')}: {f.get('detail')}")

    if errors:
        print("\n--- UNCAUGHT RUNTIME ERRORS ---")
        for e in errors:
            print(f"  [ERROR] {e}")

    success = len(failures) == 0 and len(errors) == 0
    print("\n================================================================================")
    print(f"  ADVERSARIAL STRESS VERDICT: {'APPROVE' if success else 'REQUEST_CHANGES'}")
    print("================================================================================")
    return success


if __name__ == "__main__":
    ok = run_adversarial_stress()
    sys.exit(0 if ok else 1)
