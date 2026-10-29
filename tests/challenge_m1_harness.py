"""Empirical Challenger M1_1 Stress Test Harness.

Executes white-box stress testing, regression validation, rapid hash routing fuzzing,
empty subject edge cases, and zero-emoji compliance using headless Microsoft Edge.
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
import unicodedata
import urllib.parse
from pathlib import Path

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding='utf-8')

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
MATH_ARCHIVE_DIR = ROOT_DIR / "math-archive"
OLIMP_DIR = ROOT_DIR / "olimp-kombinatorika"
TESTS_DIR = MATH_ARCHIVE_DIR / "tests"

EXPECTED_OLIMP_HASH = "AC80217ABDB24FDFD02A54C056D31F8DB41FFF1F75AA04F736D9A7506DB50935"

# 1. Emoji / Pictograph Audit
EMOJI_RANGES = [
    (0x1F600, 0x1F64F),  # Emoticons
    (0x1F300, 0x1F5FF),  # Misc Symbols and Pictographs
    (0x1F680, 0x1F6FF),  # Transport and Map
    (0x1F700, 0x1F77F),  # Alchemical Symbols
    (0x1F780, 0x1F7FF),  # Geometric Shapes Extended
    (0x1F800, 0x1F8FF),  # Supplemental Arrows-C
    (0x1F900, 0x1F9FF),  # Supplemental Symbols and Pictographs
    (0x1FA00, 0x1FA6F),  # Chess Symbols
    (0x1FA70, 0x1FAFF),  # Symbols and Pictographs Extended-A
    (0x2600, 0x26FF),    # Misc Symbols
    (0x2700, 0x27BF),    # Dingbats (includes checkmarks U+2713, U+2714)
    (0xFE00, 0xFE0F),    # Variation Selectors
    (0x1F000, 0x1F02F),  # Mahjong
    (0x1F0A0, 0x1F0FF),  # Playing Cards
    (0x2300, 0x23FF),    # Misc Technical
    (0x2B50, 0x2B55),    # Stars
]

def check_emojis_in_file(path: Path):
    content = path.read_text(encoding='utf-8', errors='ignore')
    violations = []
    for i, ch in enumerate(content):
        cp = ord(ch)
        for s, e in EMOJI_RANGES:
            if s <= cp <= e:
                violations.append((i, ch, f"U+{cp:04X}", unicodedata.name(ch, "UNKNOWN")))
                break
    return violations

def run_emoji_audit():
    print("[TEST SUITE 1] Running Exhaustive Emoji Audit across math-archive files...")
    target_files = [
        MATH_ARCHIVE_DIR / "index.html",
        MATH_ARCHIVE_DIR / "README.md",
        MATH_ARCHIVE_DIR / ".nojekyll"
    ]
    all_violations = {}
    for f in target_files:
        if f.exists():
            v = check_emojis_in_file(f)
            if v:
                all_violations[str(f)] = v
                print(f"  FAIL: Found {len(v)} emoji violations in {f.name}: {v[:5]}")
            else:
                print(f"  PASS: 0 emoji violations in {f.name} ({f.stat().st_size} bytes)")
    return len(all_violations) == 0

def run_integrity_audit():
    print("\n[TEST SUITE 2] Checking Source Integrity of olimp-kombinatorika...")
    olimp_index = OLIMP_DIR / "index.html"
    if not olimp_index.exists():
        print("  FAIL: olimp-kombinatorika/index.html does not exist!")
        return False
    h = hashlib.sha256(olimp_index.read_bytes()).hexdigest().upper()
    if h != EXPECTED_OLIMP_HASH:
        print(f"  FAIL: olimp-kombinatorika/index.html SHA-256 mismatch! Expected {EXPECTED_OLIMP_HASH}, got {h}")
        return False
    print(f"  PASS: olimp-kombinatorika/index.html SHA-256 verified ({h})")
    return True

# 2. Browser-Driven In-DOM Stress Test
# Generates runner HTML that embeds index.html in an iframe on the same origin and executes stress suites
RUNNER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>M1 Challenger Stress Test Runner</title>
</head>
<body style="margin:0;padding:0;background:#111;color:#eee;font-family:sans-serif;">
  <div style="padding:10px;background:#222;font-size:14px;">
    <b>Milestone 1 Challenger Stress Harness</b> | <span id="status">Loading application...</span>
  </div>
  <iframe id="app" src="/math-archive/index.html" style="width:1280px;height:900px;border:none;"></iframe>

  <script>
  (function() {
    var results = {
      passes: [],
      failures: [],
      uncaughtErrors: [],
      stats: {}
    };

    function logPass(msg) {
      console.log('PASS: ' + msg);
      results.passes.push(msg);
    }
    function logFail(msg, detail) {
      console.error('FAIL: ' + msg, detail);
      results.failures.push({ message: msg, detail: String(detail || '') });
    }

    window.onerror = function(msg, url, line, col, err) {
      var errStr = msg + ' at ' + url + ':' + line + ':' + col;
      console.error('WINDOW ERROR: ' + errStr);
      results.uncaughtErrors.push(errStr);
    };
    window.onunhandledrejection = function(e) {
      var errStr = 'Unhandled rejection: ' + (e.reason ? (e.reason.stack || e.reason) : e);
      console.error(errStr);
      results.uncaughtErrors.push(errStr);
    };

    var iframe = document.getElementById('app');
    var statusEl = document.getElementById('status');

    function sendReport() {
      statusEl.textContent = 'Completed: ' + results.passes.length + ' passes, ' + results.failures.length + ' failures, ' + results.uncaughtErrors.length + ' errors';
      fetch('/api/results', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(results)
      }).catch(function(e) { console.error('Failed to send report:', e); });
    }

    iframe.onload = function() {
      var win = iframe.contentWindow;
      var doc = iframe.contentDocument;

      // Catch errors inside iframe
      win.onerror = function(msg, url, line, col, err) {
        var errStr = 'IFRAME ERROR: ' + msg + ' at ' + url + ':' + line + ':' + col;
        console.error(errStr);
        results.uncaughtErrors.push(errStr);
      };
      win.onunhandledrejection = function(e) {
        var errStr = 'IFRAME Unhandled rejection: ' + (e.reason ? (e.reason.stack || e.reason) : e);
        console.error(errStr);
        results.uncaughtErrors.push(errStr);
      };

      statusEl.textContent = 'App loaded. Running stress test suites...';
      runSuites(win, doc)
        .catch(function(e) {
          logFail('Fatal unhandled error in runSuites', e.stack || e);
        })
        .finally(function() {
          sendReport();
        });
    };

    function wait(ms) {
      return new Promise(function(resolve) { setTimeout(resolve, ms); });
    }

    async function runSuites(win, doc) {
      var t0 = performance.now();

      // =========================================================================
      // SUITE A: Initial DOM & Settings Verification
      // =========================================================================
      try {
        var initSubj = doc.documentElement.getAttribute('data-subject');
        if (initSubj === 'combinatorics') {
          logPass('Initial documentElement data-subject is combinatorics');
        } else {
          logFail('Initial data-subject mismatch: ' + initSubj);
        }

        var navBtns = doc.querySelectorAll('.subj-btn');
        if (navBtns.length === 4) {
          logPass('All 4 subject navigation buttons present in DOM');
        } else {
          logFail('Expected 4 subject buttons, found: ' + navBtns.length);
        }

        var activeBtns = doc.querySelectorAll('.subj-btn.active');
        if (activeBtns.length === 1 && activeBtns[0].getAttribute('data-subject') === 'combinatorics') {
          logPass('Exactly one active button for combinatorics');
        } else {
          logFail('Unexpected active buttons count: ' + activeBtns.length);
        }

        // Check settings in localStorage
        try {
          var rawSettings = win.localStorage.getItem('math_archive_settings_v1');
          var settings = rawSettings ? JSON.parse(rawSettings) : null;
          if (settings && (settings.subject === 'combinatorics' || settings.currentDomain === 'combinatorics')) {
            logPass('localStorage unified settings stored with subject=combinatorics');
          } else {
            logFail('localStorage settings missing or invalid: ' + rawSettings);
          }
        } catch (lse) {
          logFail('Error reading localStorage settings', lse);
        }

        var combNodes = doc.querySelectorAll('#map .node');
        if (combNodes.length >= 29) {
          logPass('Combinatorics initial graph rendered: ' + combNodes.length + ' nodes');
        } else {
          logFail('Combinatorics initial graph nodes count: ' + combNodes.length);
        }

        var tocItems = doc.querySelectorAll('.toc-l li');
        if (tocItems.length === 29) {
          logPass('Combinatorics initial TOC rendered: 29 topics');
        } else {
          logFail('Combinatorics initial TOC items count: ' + tocItems.length);
        }
      } catch (e) {
        logFail('Suite A exception', e.stack || e);
      }

      // =========================================================================
      // SUITE B: Subject Switching Stress Test & Regressions
      // =========================================================================
      try {
        var subjects = ['algebra', 'number-theory', 'geometry', 'combinatorics'];
        for (var i = 0; i < subjects.length; i++) {
          var target = subjects[i];
          var btn = doc.querySelector('.subj-btn[data-subject="' + target + '"]');
          if (!btn) {
            logFail('Navigation button for ' + target + ' not found!');
            continue;
          }

          btn.click();
          await wait(650); // Wait for veil transition

          var docSubj = doc.documentElement.getAttribute('data-subject');
          if (docSubj === target) {
            logPass('Switched to ' + target + ': data-subject attribute verified');
          } else {
            logFail('Failed to switch to ' + target + ', data-subject is: ' + docSubj);
          }

          var act = doc.querySelectorAll('.subj-btn.active');
          if (act.length === 1 && act[0].getAttribute('data-subject') === target) {
            logPass('Pill active state strictly synced for: ' + target);
          } else {
            logFail('Pill active state out of sync for: ' + target + ' (active count: ' + act.length + ')');
          }

          if (target !== 'combinatorics') {
            var emptyMap = doc.querySelector('.empty-map');
            if (emptyMap && emptyMap.textContent.indexOf('Карта курса пуста') >= 0) {
              logPass(target + ' displays empty map container correctly');
            } else {
              logFail(target + ' missing or invalid .empty-map container', emptyMap ? emptyMap.textContent : 'none');
            }
            var emptyToc = doc.querySelector('.empty-toc');
            if (emptyToc && emptyToc.textContent.indexOf('В этом разделе пока нет') >= 0) {
              logPass(target + ' displays empty toc container correctly');
            } else {
              logFail(target + ' missing or invalid .empty-toc container', emptyToc ? emptyToc.textContent : 'none');
            }
            var contBtn = doc.querySelector('.btn.cont');
            if (contBtn && contBtn.getAttribute('href') === '#') {
              logPass(target + ' continue button has safe href="#"');
            } else {
              logFail(target + ' continue button href unexpected: ' + (contBtn ? contBtn.getAttribute('href') : 'null'));
            }
          } else {
            var reloadedNodes = doc.querySelectorAll('#map .node');
            if (reloadedNodes.length >= 29) {
              logPass('Combinatorics data 100% restored after roundtrip: ' + reloadedNodes.length + ' graph nodes');
            } else {
              logFail('Combinatorics graph corrupted after roundtrip: ' + reloadedNodes.length + ' nodes');
            }
          }
        }

        // Rapid switching stress test: 16 rapid clicks back-to-back without waiting
        statusEl.textContent = 'Running rapid switching stress test...';
        var flurry = ['algebra', 'geometry', 'number-theory', 'combinatorics', 'algebra', 'geometry', 'combinatorics'];
        for (var j = 0; j < flurry.length; j++) {
          var fbtn = doc.querySelector('.subj-btn[data-subject="' + flurry[j] + '"]');
          if (fbtn) fbtn.click();
          await wait(25); // Interrupt before animation finishes
        }
        await wait(750); // Let veil animation settle

        var finalSubj = doc.documentElement.getAttribute('data-subject');
        if (finalSubj === 'combinatorics') {
          logPass('Surviving rapid-fire subject switching; settled on combinatorics');
        } else {
          logFail('Race condition in switchSubject: clicked combinatorics last, but settled on ' + finalSubj);
        }

        // Explicitly restore to combinatorics after rapid switching test
        var combPill = doc.querySelector('.subj-btn[data-subject="combinatorics"]');
        if (combPill) combPill.click();
        await wait(650);

        var mapRoots = doc.querySelectorAll('#map');
        var viewRoots = doc.querySelectorAll('#view');
        if (mapRoots.length === 1 && viewRoots.length === 1) {
          logPass('No duplicate #map or #view containers after stress cycle');
        } else {
          logFail('Duplicate containers detected: map=' + mapRoots.length + ', view=' + viewRoots.length);
        }
      } catch (e) {
        logFail('Suite B exception', e.stack || e);
      }

      // =========================================================================
      // SUITE C: Hash Routing Stress Test
      // =========================================================================
      try {
        statusEl.textContent = 'Running hash routing stress test...';

        // 1. Valid hashes
        var testRoutes = [
          { hash: '#komb', expectSubj: 'combinatorics', checkSelector: '#map .node' },
          { hash: '#alg', expectSubj: 'algebra', checkSelector: '.empty-map' },
          { hash: '#nt', expectSubj: 'number-theory', checkSelector: '.empty-map' },
          { hash: '#geom', expectSubj: 'geometry', checkSelector: '.empty-map' },
          { hash: '#komb', expectSubj: 'combinatorics', checkSelector: '#map .node' },
          { hash: '#t1', expectSubj: 'combinatorics', checkSelector: 'article.topic' },
          { hash: '#t29', expectSubj: 'combinatorics', checkSelector: 'article.topic' },
          { hash: '#p1.1.1', expectSubj: 'combinatorics', checkSelector: 'article.topic' }
        ];

        for (var k = 0; k < testRoutes.length; k++) {
          var tr = testRoutes[k];
          win.location.hash = tr.hash;
          win.dispatchEvent(new win.Event('popstate'));
          await wait(600); // Wait for transition animation to complete

          var curSubj = doc.documentElement.getAttribute('data-subject');
          var elem = doc.querySelector(tr.checkSelector);
          if (curSubj === tr.expectSubj && elem) {
            logPass('Valid hash navigated correctly: ' + tr.hash + ' -> subj=' + curSubj + ' found=' + tr.checkSelector);
          } else {
            logFail('Hash navigation failed for ' + tr.hash + ': subj=' + curSubj + ', found=' + !!elem);
          }
        }

        // 2. Invalid, out-of-bounds, and malformed hashes
        var invalidHashes = [
          '#invalid',
          '#t99',
          '#t0',
          '#-1',
          '#t1-999',
          '#p99.99.99',
          '#alg/t1',
          '#geom/t5',
          '#nt/p1.1.1',
          '#///',
          '#unknown/sub/path',
          '#',
          ''
        ];

        for (var m = 0; m < invalidHashes.length; m++) {
          var inv = invalidHashes[m];
          try {
            win.location.hash = inv;
            win.dispatchEvent(new win.Event('popstate'));
            await wait(50);
            logPass('Invalid hash handled safely: "' + inv + '" without crash');
          } catch (invErr) {
            logFail('Exception thrown on invalid hash "' + inv + '"', invErr.stack || invErr);
          }
        }

        // 3. Rapid hash fuzzing (20 rapid random transitions)
        var fuzzPool = [
          '#komb', '#alg', '#t1', '#invalid', '#geom', '#p1.1.1', '#t99',
          '#nt', '#t29', '#alg/t1', '#geom/p1.1.1', '#', '#t1-1'
        ];
        for (var fz = 0; fz < 20; fz++) {
          var randHash = fuzzPool[Math.floor(Math.random() * fuzzPool.length)];
          win.location.hash = randHash;
          win.dispatchEvent(new win.Event('popstate'));
          await wait(20);
        }
        await wait(400);
        logPass('Surviving rapid hash fuzzing (20 rapid transitions) with 0 crashes');
      } catch (e) {
        logFail('Suite C exception', e.stack || e);
      }

      // =========================================================================
      // SUITE D: Empty Subject States Execution Stress Test
      // =========================================================================
      try {
        statusEl.textContent = 'Running empty subject execution stress test...';

        var emptySubjects = ['algebra', 'number-theory', 'geometry'];
        for (var es = 0; es < emptySubjects.length; es++) {
          var esName = emptySubjects[es];
          var esBtn = doc.querySelector('.subj-btn[data-subject="' + esName + '"]');
          if (esBtn) esBtn.click();
          await wait(650);

          // Verify empty containers
          var empMap = doc.querySelector('.empty-map');
          if (empMap && empMap.textContent.indexOf('Карта курса пуста') >= 0) {
            logPass(esName + ': empty map state verified');
          } else {
            logFail(esName + ': missing empty map indicator', empMap ? empMap.textContent : 'none');
          }

          var empToc = doc.querySelector('.empty-toc');
          if (empToc && empToc.textContent.indexOf('В этом разделе пока нет') >= 0) {
            logPass(esName + ': empty toc state verified');
          } else {
            logFail(esName + ': missing empty toc indicator', empToc ? empToc.textContent : 'none');
          }

          // Test UI interactions: Click continue button
          var contBtn = doc.querySelector('.btn.cont');
          if (contBtn) {
            contBtn.click();
            await wait(100);
            logPass(esName + ': clicking continue button does not throw or corrupt state');
          }

          // Test Search Overlay in Empty Subject
          var searchBtn = doc.querySelector('[data-act="search"]');
          if (searchBtn) {
            searchBtn.click();
            await wait(150);
            var sinput = doc.getElementById('sq');
            if (sinput) {
              sinput.value = 'квадратный корень 5.1';
              sinput.dispatchEvent(new win.Event('input'));
              await wait(250);
              var sres = doc.getElementById('sres');
              if (sres && sres.textContent.indexOf('Ничего не нашлось') >= 0) {
                logPass(esName + ': search in empty subject returns clean message without error');
              } else {
                logFail(esName + ': search result unexpected');
              }
            }
            // Close search overlay
            var closeBtn = doc.querySelector('.ov [data-close]');
            if (closeBtn) closeBtn.click();
            await wait(250);
          }

          // Test About dialog in Empty Subject
          var aboutBtn = doc.querySelector('[data-act="about"]');
          if (aboutBtn) {
            aboutBtn.click();
            await wait(150);
            var ov = doc.querySelector('.ov');
            if (ov && /0[\s\u00A0]+задач/.test(ov.textContent)) {
              logPass(esName + ': about dialog displays 0 tasks cleanly');
            } else {
              logFail(esName + ': about dialog content invalid: ' + (ov ? ov.textContent.slice(0, 80) : 'none'));
            }
            var resetBtn = doc.getElementById('reset-1');
            if (resetBtn && resetBtn.disabled) {
              logPass(esName + ': reset progress button disabled when solved=0');
            } else {
              logFail(esName + ': reset progress button should be disabled');
            }
            var closeAbout = doc.querySelector('.ov [data-close]');
            if (closeAbout) closeAbout.click();
            await wait(250);
          }

          // Test window resize event in Empty Subject
          win.dispatchEvent(new win.Event('resize'));
          await wait(60);
          logPass(esName + ': window resize handled cleanly');
        }
      } catch (e) {
        logFail('Suite D exception', e.stack || e);
      }

      // =========================================================================
      // SUITE E: Visual Veil Animation Geometry and Styling
      // =========================================================================
      try {
        statusEl.textContent = 'Running visual veil animation geometry and styling tests...';
        var veilEl = doc.getElementById('veil');
        if (!veilEl) {
          logFail('Veil element #veil not found in DOM');
        } else {
          logPass('Veil element #veil exists in DOM');

          // 1. Initial / Idle state styling and attributes
          var veilStyle = win.getComputedStyle(veilEl);
          if (veilStyle.position === 'fixed') {
            logPass('Veil position is fixed');
          } else {
            logFail('Veil position unexpected: ' + veilStyle.position);
          }
          if (veilStyle.pointerEvents === 'none') {
            logPass('Veil pointer-events is none');
          } else {
            logFail('Veil pointer-events unexpected: ' + veilStyle.pointerEvents);
          }
          if (parseInt(veilStyle.zIndex, 10) >= 90) {
            logPass('Veil z-index is >= 90 (' + veilStyle.zIndex + ')');
          } else {
            logFail('Veil z-index insufficient: ' + veilStyle.zIndex);
          }
          if (veilEl.hasAttribute('hidden')) {
            logPass('Veil is hidden in idle state');
          } else {
            logFail('Veil should have hidden attribute in idle state');
          }

          // Ensure subject is combinatorics before testing switch to geometry
          var initComb = doc.querySelector('.subj-btn[data-subject="combinatorics"]');
          if (initComb) {
            initComb.click();
            await wait(650);
          }

          // 2. Transition geometry and state lifecycle during switchSubject
          var geomBtn = doc.querySelector('.subj-btn[data-subject="geometry"]');
          if (geomBtn) {
            var btnRect = geomBtn.getBoundingClientRect();
            var expectedCx = btnRect.left + btnRect.width / 2;
            var expectedCy = btnRect.top + btnRect.height / 2;
            var expectedR = Math.hypot(
              Math.max(expectedCx, win.innerWidth - expectedCx),
              Math.max(expectedCy, win.innerHeight - expectedCy)
            ) + 20;

            if (expectedR > Math.hypot(win.innerWidth, win.innerHeight) / 2) {
              logPass('Veil calculated expansion radius covers entire viewport: ' + Math.round(expectedR) + 'px');
            } else {
              logFail('Veil expansion radius calculation too small: ' + expectedR);
            }

            // Trigger switch
            geomBtn.click();
            await wait(60);
            var duringActive = !veilEl.hasAttribute('hidden');
            if (duringActive) {
              logPass('Veil active during subject transition animation');
            } else {
              logFail('Veil was not active during transition');
            }

            // Wait for transition to finish
            await wait(650);

            if (veilEl.hasAttribute('hidden')) {
              logPass('Veil hidden restored after subject transition finishes');
            } else {
              logFail('Veil failed to hide after transition');
            }
            if (!veilEl.style.clipPath || veilEl.style.clipPath === 'none') {
              logPass('Veil clipPath reset cleanly');
            } else {
              logFail('Veil clipPath was not reset: ' + veilEl.style.clipPath);
            }
            if (!veilEl.style.opacity || veilEl.style.opacity === '1' || veilEl.style.opacity === '') {
              logPass('Veil opacity reset cleanly');
            } else {
              logFail('Veil opacity was not reset: ' + veilEl.style.opacity);
            }
          }

          // 3. Topic transition veil test (from map to topic and back)
          var combBtn = doc.querySelector('.subj-btn[data-subject="combinatorics"]');
          if (combBtn) {
            combBtn.click();
            await wait(650);
          }
          var firstTopicNode = doc.querySelector('.node[href="#t1"]') || doc.querySelector('.node[data-n="1"]');
          if (firstTopicNode) {
            firstTopicNode.click();
            await wait(950); // Total map-to-topic animation is 520ms + 300ms = 820ms
            var topicArticle = doc.querySelector('article.topic');
            if (topicArticle) {
              logPass('Navigation from map to topic renders article.topic');
            } else {
              logFail('Topic article not rendered after clicking map node');
            }
            if (veilEl.hasAttribute('hidden')) {
              logPass('Veil hidden after map-to-topic transition completes');
            } else {
              logFail('Veil is not hidden after map-to-topic transition');
            }

            // Return to map
            var backBtn = doc.querySelector('.back') || doc.querySelector('[data-v="map"]');
            if (backBtn) {
              backBtn.click();
              await wait(900); // Total topic-to-map animation is 200ms + 480ms = 680ms
              var mapVisible = doc.querySelector('#map');
              if (mapVisible) {
                logPass('Navigation from topic back to map succeeds');
              } else {
                logFail('Failed to return to map from topic');
              }
              if (veilEl.hasAttribute('hidden')) {
                logPass('Veil hidden after topic-to-map transition completes');
              } else {
                logFail('Veil is not hidden after topic-to-map transition');
              }
            }
          }
        }
      } catch (e) {
        logFail('Suite E exception', e.stack || e);
      }

      // Restore to combinatorics
      var backComb = doc.querySelector('.subj-btn[data-subject="combinatorics"]');
      if (backComb) backComb.click();
      await wait(300);

      var elapsed = Math.round(performance.now() - t0);
      results.stats.elapsedMs = elapsed;
      console.log('Stress test run completed in ' + elapsed + 'ms');

      sendReport();
    }
  })();
  </script>
</body>
</html>
"""

def run_browser_stress_tests():
    print("\n[TEST SUITE 3] Running In-Browser Stress Test via Headless Microsoft Edge...")

    # Write runner HTML to tests directory
    runner_path = TESTS_DIR / "challenger_stress_runner.html"
    runner_path.write_text(RUNNER_HTML, encoding='utf-8')

    results_container = {}

    class TestServerHandler(http.server.SimpleHTTPRequestHandler):
        def translate_path(self, path):
            path = path.split('?', 1)[0].split('#', 1)[0]
            rel = urllib.parse.unquote(path).lstrip('/')
            resolved = str(ROOT_DIR / rel)
            return resolved

        def do_POST(self):
            if self.path == '/api/results':
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length).decode('utf-8')
                results_container.update(json.loads(body))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, fmt, *args):
            print("  [SERVER LOG]", fmt % args)

    # Find free port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]

    httpd = socketserver.TCPServer(('127.0.0.1', port), TestServerHandler)
    server_thread = threading.Thread(target=httpd.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    edge_candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")
    ]
    edge_bin = next((p for p in edge_candidates if p.exists()), None)
    if not edge_bin:
        print("  FAIL: Microsoft Edge executable not found!")
        httpd.shutdown()
        runner_path.unlink(missing_ok=True)
        return False

    url = f"http://127.0.0.1:{port}/math-archive/tests/challenger_stress_runner.html"
    cmd = [
        str(edge_bin),
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        url
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    start_time = time.time()
    timeout_sec = 60

    while time.time() - start_time < timeout_sec:
        if results_container:
            break
        time.sleep(0.2)

    try:
        proc.kill()
    except Exception:
        pass
    httpd.shutdown()
    runner_path.unlink(missing_ok=True)

    if not results_container:
        print(f"  FAIL: Browser stress test timed out after {timeout_sec}s with no results reported.")
        return False

    passes = results_container.get("passes", [])
    failures = results_container.get("failures", [])
    errors = results_container.get("uncaughtErrors", [])
    stats = results_container.get("stats", {})

    print(f"  Execution time: {stats.get('elapsedMs', 'N/A')}ms")
    print(f"  Passes: {len(passes)}")
    print(f"  Failures: {len(failures)}")
    print(f"  Uncaught runtime errors: {len(errors)}")

    for p in passes:
        print(f"    [PASS] {p}")

    if failures:
        print("\n  FAILURES ENCOUNTERED:")
        for f in failures:
            print(f"    [FAIL] {f.get('message')}: {f.get('detail')}")

    if errors:
        print("\n  UNCAUGHT RUNTIME ERRORS:")
        for e in errors:
            print(f"    [ERROR] {e}")

    return len(failures) == 0 and len(errors) == 0

def main():
    print("================================================================================")
    print("  Milestone 1 Empirical Challenger Stress Test Suite")
    print("================================================================================")

    res_emoji = run_emoji_audit()
    res_integrity = run_integrity_audit()
    res_browser = run_browser_stress_tests()

    print("\n================================================================================")
    print("  Summary of Results")
    print("================================================================================")
    print(f"  1. Zero-Emoji Compliance:     {'PASS' if res_emoji else 'FAIL'}")
    print(f"  2. Base Source Integrity:     {'PASS' if res_integrity else 'FAIL'}")
    print(f"  3. Browser Stress Harness:    {'PASS' if res_browser else 'FAIL'}")

    all_passed = res_emoji and res_integrity and res_browser
    verdict = "APPROVE" if all_passed else "REQUEST_CHANGES"
    print(f"\n  OVERALL CHALLENGE VERDICT: {verdict}")
    print("================================================================================")

    sys.exit(0 if all_passed else 1)

if __name__ == "__main__":
    main()
