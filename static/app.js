const API = "";
let gameId = null;
const HUMAN_ID = "player_0";
let lastStateJSON = "";
let raiseOpen = false;
let botMoveInProgress = false;

// Thinking-panel/log dedup keys
let lastThinkingKey = null;
let lastLogKey = null;
let lastHandResultKey = null;

// Read URL params: ?game_id=xxx&spectator=1&arena=1&hands=N
var _params = new URLSearchParams(window.location.search);
var SPECTATOR_MODE = _params.get("spectator") === "1";
var ARENA_MODE    = _params.get("arena")     === "1";
var _initialGameId = _params.get("game_id") || null;
var MAX_HANDS = parseInt(_params.get("hands") || "0", 10);

function el(id) {
  return document.getElementById(id);
}

function clearGame() {
  gameId = null;
  lastStateJSON = "";
  raiseOpen = false;
  botMoveInProgress = false;
  lastThinkingKey = null;
  lastHandResultKey = null;
  el("game-id").textContent = "";
  el("players").innerHTML = "";
  el("community-cards").innerHTML = "";
  el("pot").textContent = "Pot: 0";
  el("message").textContent = "Game ended or server restarted. Click New Game to play.";
  el("action-buttons").innerHTML = "";
  var modal = el("game-summary-modal");
  if (modal) modal.style.display = "none";
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function cardEl(code, hidden) {
  var wrap = document.createElement("span");
  wrap.className = "card-wrap";
  wrap.setAttribute("data-code", hidden ? "?" : code);
  if (hidden) {
    var img = document.createElement("img");
    img.src = "/static/img/cards/card.png";
    img.alt = "?";
    img.className = "card card-img card-back";
    img.onerror = function () {
      img.style.display = "none";
      var s = document.createElement("span");
      s.className = "card card-text card-back";
      s.textContent = "?";
      wrap.appendChild(s);
    };
    wrap.appendChild(img);
    return wrap;
  }
  var img = document.createElement("img");
  img.src = "/static/img/cards/" + code + ".png";
  img.alt = code;
  img.className = "card card-img";
  img.onerror = function () {
    img.style.display = "none";
    var s = document.createElement("span");
    s.className = "card card-text";
    s.textContent = code;
    wrap.appendChild(s);
  };
  wrap.appendChild(img);
  return wrap;
}

// Compute seat position on an ellipse.
// Player 0 is at the bottom (angle 0 = 6 o'clock), others spread evenly clockwise.
// Returns { top: "XX%", left: "YY%" }
function seatPosition(playerIndex, totalPlayers) {
  // Angle in radians: start at bottom (π/2 in standard math = 6 o'clock),
  // go clockwise. We use: angle = (2π * i / n) starting from bottom.
  // In CSS coordinates: top grows downward, left grows rightward.
  // bottom = angle 0 → (left:50%, top:93%)
  // clockwise means increasing angle goes left-then-up-then-right-then-down
  var angle = (2 * Math.PI * playerIndex) / totalPlayers;
  // Parametric ellipse: x = cx + rx*sin(angle), y = cy + ry*cos(angle)
  // sin(0)=0,cos(0)=1 → bottom center ✓
  // Going clockwise: sin increases (→ left decreases), cos decreases (→ top decreases)
  // We want clockwise visually, so left player first:
  // angle increases → go counter-clockwise in math → clockwise on screen if we negate sin
  // Actually let's just be explicit:
  //   CSS left% = 50 + rx * sin(angle)   [positive angle → right on screen? no...]
  // Let me think simply:
  //   angle=0 → bottom center: left=50%, top=93%  (cos=1 → top is high%)
  //   angle=π → top center:    left=50%, top=7%   (cos=-1 → top is low%)
  //   Going clockwise from bottom means next seat is bottom-LEFT,
  //   so sin should be negative for small positive angles.
  // Formula:
  //   left = 50 - rx * sin(angle)
  //   top  = 50 + ry * cos(angle)
  // For 9–10 players use a larger ry so the top/bottom seats hug the table
  // rail (they overflow slightly into the dark padding area, which looks natural)
  // and rx=40 keeps the left/right sides from clipping excessively.
  var rx = totalPlayers >= 9 ? 34 : 44; // horizontal radius in %
  var ry = totalPlayers >= 9 ? 42: 43; // vertical radius in %
  // Push diagonal seats (corners) further out so they don't overlap neighbors.
  // diagonalness is 1.0 at 45/135/225/315° and 0.0 at 0/90/180/270°.
  var diagonalness = Math.abs(Math.sin(2 * angle));
  var boost = 1 + 0.22 * diagonalness;
  var left = 50 - rx * boost * Math.sin(angle);
  var top  = 50 + ry * boost * Math.cos(angle);

  // For 10-player games, nudge the two left-side and two right-side seats
  // (players 2/3 and 7/8) apart vertically.  They share the same horizontal
  // column (sin(72°)=sin(108°)), so extra vertical spread is the only way
  // to prevent overlap without shrinking the cards.
  // Nudge direction: below-centre seats move down (+), above-centre move up (−).
  if (totalPlayers === 10) {
    var vNudges10 = [0, 0, 4, -4, 0, 0, 0, -4, 4, 0];
    var hNudges10 = [0, 0, -9, -9, 0, 0, 0, 9, 9, 0];
    top  += vNudges10[playerIndex] || 0;
    left += hNudges10[playerIndex] || 0;
  }
  if (totalPlayers === 9) {
    // P2(80°) and P3(120°) are the left-side pair; P6(240°) and P7(280°) the right-side pair.
    // v-nudge: below-centre seats move down (+), above-centre move up (−).
    // h-nudge: left pair moves left (−), right pair moves right (+).
    var vNudges9 = [0, 3, 3, -3, 0, 0, -3, 3, 3];
    var hNudges9 = [0, 3, -8, -8, 0, 0,  8, 8, -3];
    top  += vNudges9[playerIndex] || 0;
    left += hNudges9[playerIndex] || 0;
  }

  return { top: top + "%", left: left + "%" };
}

// Get the cards key for a player (used to decide if cards need rebuilding)
function cardsKey(p) {
  if (p.hole_cards && p.hole_cards.length > 0) {
    return p.hole_cards.join(",");
  }
  return "hidden";
}

function renderPlayers(state) {
  var container = el("players");
  var currentId = state.current_player_id;
  var n = state.players.length;
  var dealerIdx = state.dealer_index != null ? state.dealer_index : -1;
  var sbIdx = state.small_blind_index != null ? state.small_blind_index : -1;
  var bbIdx = state.big_blind_index != null ? state.big_blind_index : -1;

  // Index existing player divs by player id
  var existingDivs = {};
  var oldChildren = container.querySelectorAll(".player");
  for (var c = 0; c < oldChildren.length; c++) {
    var pid = oldChildren[c].getAttribute("data-player-id");
    if (pid) existingDivs[pid] = oldChildren[c];
  }

  var usedIds = {};

  for (var i = 0; i < n; i++) {
    var p = state.players[i];
    usedIds[p.id] = true;

    var pos = seatPosition(i, n);
    var existing = existingDivs[p.id];

    if (existing) {
      // Update in place — no rebuild, no flash

      // Update position
      existing.style.top = pos.top;
      existing.style.left = pos.left;

      // Update className
      var cls = "player";
      if (p.folded || p.busted) cls += " folded";
      if (p.busted) cls += " busted";
      if (p.id === currentId) cls += " current-turn";
      if (p.id === HUMAN_ID) cls += " is-human";
      if (existing.className !== cls) existing.className = cls;

      // Update name + badges
      var label = (!SPECTATOR_MODE && p.id === HUMAN_ID) ? "Human (You)" : (p.display_name || p.id);
      var badges = "";
      if (i === dealerIdx) badges += '<span class="badge badge-dealer">D</span>';
      if (i === sbIdx) badges += '<span class="badge badge-sb">SB</span>';
      if (i === bbIdx) badges += '<span class="badge badge-bb">BB</span>';
      var nameEl = existing.querySelector(".name");
      var nameHTML = label + badges;
      if (nameEl.innerHTML !== nameHTML) nameEl.innerHTML = nameHTML;

      // Update stack
      var stackEl = existing.querySelector(".stack");
      var stackText = p.busted ? "BUST" : "Stack: " + p.stack;
      if (stackEl.textContent !== stackText) stackEl.textContent = stackText;

      // Update bet
      var betEl = existing.querySelector(".bet");
      var betText = p.busted ? "" : "Bet: " + p.current_bet;
      if (betEl.textContent !== betText) betEl.textContent = betText;

      // Only rebuild cards if they actually changed
      var newCK = cardsKey(p);
      if (existing.getAttribute("data-cards") !== newCK) {
        existing.setAttribute("data-cards", newCK);
        var cardsEl = existing.querySelector(".cards");
        cardsEl.innerHTML = "";
        if (p.busted) {
          // no cards shown for eliminated players
        } else if (p.hole_cards && p.hole_cards.length > 0) {
          for (var j = 0; j < p.hole_cards.length; j++) {
            cardsEl.appendChild(cardEl(p.hole_cards[j], false));
          }
        } else {
          cardsEl.appendChild(cardEl("?", true));
          cardsEl.appendChild(cardEl("?", true));
        }
      }
    } else {
      // Create new player div
      var div = document.createElement("div");
      div.setAttribute("data-player-id", p.id);

      var cls = "player";
      if (p.folded || p.busted) cls += " folded";
      if (p.busted) cls += " busted";
      if (p.id === currentId) cls += " current-turn";
      if (p.id === HUMAN_ID) cls += " is-human";
      div.className = cls;

      div.style.top = pos.top;
      div.style.left = pos.left;

      var label = (!SPECTATOR_MODE && p.id === HUMAN_ID) ? "Human (You)" : (p.display_name || p.id);
      var badges = "";
      if (i === dealerIdx) badges += '<span class="badge badge-dealer">D</span>';
      if (i === sbIdx) badges += '<span class="badge badge-sb">SB</span>';
      if (i === bbIdx) badges += '<span class="badge badge-bb">BB</span>';

      var ck = cardsKey(p);
      div.setAttribute("data-cards", ck);

      div.innerHTML =
        '<div class="name">' + label + badges + '</div>' +
        '<div class="stack">' + (p.busted ? "BUST" : "Stack: " + p.stack) + '</div>' +
        '<div class="bet">' + (p.busted ? "" : "Bet: " + p.current_bet) + '</div>' +
        '<div class="cards"></div>';
      var cardsEl = div.querySelector(".cards");
      if (p.busted) {
        // no cards shown for eliminated players
      } else if (p.hole_cards && p.hole_cards.length > 0) {
        for (var j = 0; j < p.hole_cards.length; j++) {
          cardsEl.appendChild(cardEl(p.hole_cards[j], false));
        }
      } else {
        cardsEl.appendChild(cardEl("?", true));
        cardsEl.appendChild(cardEl("?", true));
      }
      container.appendChild(div);
    }
  }

  // Remove player divs no longer in the state
  for (var pid in existingDivs) {
    if (!usedIds[pid]) {
      existingDivs[pid].remove();
    }
  }
}

// Differential community card rendering — only append new cards
function renderCommunity(codes) {
  var container = el("community-cards");
  var existing = container.querySelectorAll(".card-wrap");
  var existingCodes = [];
  for (var i = 0; i < existing.length; i++) {
    existingCodes.push(existing[i].getAttribute("data-code") || "");
  }

  // If new codes are a strict extension of existing, just append
  var isExtension = codes.length >= existingCodes.length;
  if (isExtension) {
    for (var i = 0; i < existingCodes.length; i++) {
      if (existingCodes[i] !== codes[i]) {
        isExtension = false;
        break;
      }
    }
  }

  if (isExtension && existingCodes.length > 0) {
    for (var i = existingCodes.length; i < codes.length; i++) {
      container.appendChild(cardEl(codes[i], false));
    }
  } else {
    container.innerHTML = "";
    for (var i = 0; i < codes.length; i++) {
      container.appendChild(cardEl(codes[i], false));
    }
  }
}

function renderPot(pot) {
  el("pot").textContent = "Pot: " + pot;
}

function renderProgress(state) {
  var container = el("tournament-progress");
  // Show in arena mode always; show in any mode when MAX_HANDS is set.
  if (!container || (!ARENA_MODE && MAX_HANDS <= 0)) return;
  var handNum = (state.hands_played || 0) + 1;
  if (MAX_HANDS > 0) {
    handNum = Math.min(handNum, MAX_HANDS);
    el("progress-fill").style.width = (handNum / MAX_HANDS * 100) + "%";
    el("progress-label").textContent = "Hand " + handNum + " / " + MAX_HANDS;
  } else {
    el("progress-fill").style.width = "0%";
    el("progress-label").textContent = "Hand " + handNum;
  }
  container.style.display = "flex";
}

// ── Game-over summary ─────────────────────────────────────────────────────────

function shouldShowSummary(state) {
  var phase = state.phase;
  if (phase !== "hand_over" && phase !== "showdown") return false;
  // Arena.py explicitly flagged the session as finished (spectator mode)
  if (state.arena_finished) return true;
  // Natural end: only 1 player has chips (works in any mode)
  var active = (state.players || []).filter(function(p) { return p.stack > 0; });
  if (active.length <= 1 && (state.players || []).length > 1) return true;
  // Human arena mode: --hands N limit reached (arena.py doesn't drive hands here)
  if (ARENA_MODE && !SPECTATOR_MODE && MAX_HANDS > 0) {
    if ((state.hands_played || 0) + 1 >= MAX_HANDS) return true;
  }
  return false;
}

function computeRankings(players, bustOrder) {
  var bustMap = {};
  for (var i = 0; i < bustOrder.length; i++) {
    bustMap[bustOrder[i].player_id] = bustOrder[i];
  }

  var alive  = players.filter(function(p) { return p.stack > 0; });
  var busted = players.filter(function(p) { return p.stack <= 0; });

  // Alive: most chips = best rank
  alive.sort(function(a, b) { return b.stack - a.stack; });

  // Busted: later bust = better rank (sort by hand_number descending)
  busted.sort(function(a, b) {
    var ha = bustMap[a.id] ? bustMap[a.id].hand_number : -1;
    var hb = bustMap[b.id] ? bustMap[b.id].hand_number : -1;
    return hb - ha;
  });

  var maxStack = alive.length > 0 ? alive[0].stack : 1;
  var ranked = [];

  alive.forEach(function(p, i) {
    ranked.push({ rank: i + 1, type: "alive", player: p, maxStack: maxStack });
  });
  busted.forEach(function(p, i) {
    var info = bustMap[p.id];
    ranked.push({
      rank: alive.length + i + 1,
      type: "busted",
      player: p,
      hand_number: info ? info.hand_number + 1 : "?",  // 1-indexed display
    });
  });
  return ranked;
}

function renderSummary(state) {
  var modal = el("game-summary-modal");
  if (!modal) return;

  var ranked      = computeRankings(state.players || [], state.bust_order || []);
  var handsPlayed = (state.hands_played || 0) + 1;  // +1: first hand isn't counted by next_hand()
  var failStats   = state.failure_stats || {};
  var medals      = ["🥇", "🥈", "🥉"];
  var twoCol = ranked.length > 4;

  // Shared helper: extract failure stats for a player
  function getFailInfo(r) {
    var pf = failStats[r.player.id] || {};
    var totalMoves = pf.total_moves || 0;
    var totalFails = (pf.timeout || 0) + (pf.parse_error || 0) + (pf.parse_error_rescued || 0) + (pf.api_error || 0);
    return { pf: pf, totalMoves: totalMoves, totalFails: totalFails };
  }

  // Single-column layout: horizontal row + separate failure line below (≤4 players)
  function buildPlayerRow(r) {
    var medal = medals[r.rank - 1] || ("#" + r.rank);
    var name  = (!SPECTATOR_MODE && r.player.id === HUMAN_ID) ? "Human" : (r.player.display_name || r.player.id);
    var fi = getFailInfo(r);

    var out = '<div class="summary-row rank-' + r.rank + '">';
    out += '<span class="summary-medal">' + medal + '</span>';
    out += '<span class="summary-name">' + escapeHtml(name) + '</span>';
    if (r.type === "alive") {
      out += '<span class="summary-chips">' + r.player.stack.toLocaleString() + ' chips</span>';
    } else {
      out += '<span class="summary-bust">busted (hand ' + r.hand_number + ')</span>';
    }
    out += '</div>';

    if (fi.totalMoves > 0) {
      var failCls = fi.totalFails > 0 ? " has-failures" : "";
      out += '<div class="summary-failures' + failCls + '">';
      if (fi.totalFails === 0) {
        out += '✓ ' + fi.totalMoves + ' moves &mdash; 0 failures';
      } else {
        var pct = Math.round(fi.totalFails / fi.totalMoves * 100);
        var parts = [];
        if (fi.pf.timeout)             parts.push(fi.pf.timeout + ' timeout' + (fi.pf.timeout > 1 ? 's' : ''));
        if (fi.pf.parse_error)         parts.push(fi.pf.parse_error + ' parse error' + (fi.pf.parse_error > 1 ? 's' : '') + ' (fallback)');
        if (fi.pf.parse_error_rescued) parts.push(fi.pf.parse_error_rescued + ' parse error' + (fi.pf.parse_error_rescued > 1 ? 's' : '') + ' (guardrail ✓)');
        if (fi.pf.api_error)           parts.push(fi.pf.api_error + ' api error' + (fi.pf.api_error > 1 ? 's' : ''));
        out += fi.totalFails + '/' + fi.totalMoves + ' moves failed (' + pct + '%) &mdash; ' + parts.join(', ');
      }
      out += '</div>';
    }
    return out;
  }

  // Grid-card layout: one self-contained card per player (5+ players, two-col grid).
  // All three sections always rendered so CSS grid can stretch cards to equal height.
  function buildPlayerCard(r) {
    var medal = medals[r.rank - 1] || ("#" + r.rank);
    var name  = (!SPECTATOR_MODE && r.player.id === HUMAN_ID) ? "Human" : (r.player.display_name || r.player.id);
    var fi = getFailInfo(r);

    var out = '<div class="spc rank-' + r.rank + '">';

    // Medal + name on one line
    out += '<div class="spc-header">';
    var medalCls = r.rank > 3 ? "spc-medal spc-rank-num" : "spc-medal";
    out += '<span class="' + medalCls + '">' + medal + '</span>';
    out += '<span class="spc-name">' + escapeHtml(name) + '</span>';
    out += '</div>';

    // Chips or bust status
    if (r.type === "alive") {
      out += '<div class="spc-result spc-chips">' + r.player.stack.toLocaleString() + ' chips</div>';
    } else {
      out += '<div class="spc-result spc-bust">busted (hand ' + r.hand_number + ')</div>';
    }

    // Failure line — always present (blank when no data) to keep card heights consistent
    var failCls = fi.totalFails > 0 ? " has-failures" : "";
    out += '<div class="spc-fail' + failCls + '">';
    if (fi.totalMoves > 0) {
      if (fi.totalFails === 0) {
        out += '✓ ' + fi.totalMoves + ' moves &mdash; 0 failures';
      } else {
        var pct = Math.round(fi.totalFails / fi.totalMoves * 100);
        out += fi.totalFails + '/' + fi.totalMoves + ' failed (' + pct + '%)';
      }
    }
    out += '</div>';

    out += '</div>'; // .spc
    return out;
  }

  var html = '<div class="summary-card' + (twoCol ? ' two-col' : '') + '">';
  html += '<h2 class="summary-title">🏆 Game Over</h2>';
  html += '<p class="summary-subtitle">Final results &mdash; ' + handsPlayed + ' hand' + (handsPlayed !== 1 ? "s" : "") + ' played</p>';

  if (twoCol) {
    // Column-major order: left col = ranks 1..half, right col = ranks half+1..n
    // Interleave so CSS grid rows read: (1,5), (2,6), (3,7), (4,8)
    html += '<div class="summary-rankings two-col">';
    var half = Math.ceil(ranked.length / 2);
    var leftCol  = ranked.slice(0, half);
    var rightCol = ranked.slice(half);
    for (var ci = 0; ci < half; ci++) {
      html += buildPlayerCard(leftCol[ci]);
      if (rightCol[ci]) html += buildPlayerCard(rightCol[ci]);
    }
    html += '</div>';
  } else {
    html += '<div class="summary-rankings">';
    ranked.forEach(function(r) { html += buildPlayerRow(r); });
    html += '</div>';
  }

  html += '<div class="summary-buttons">';
  if (ARENA_MODE && !SPECTATOR_MODE) {
    html += '<button class="primary" id="summary-play-again">Play Again</button>';
  }
  html += '<button id="summary-close">Close</button>';
  html += '</div>';
  html += '</div>';  // .summary-card

  modal.innerHTML = html;
  modal.style.display = "flex";

  // Nothing to do here — arena.py stays alive and the leaderboard persists.

  var playAgainBtn = el("summary-play-again");
  if (playAgainBtn) {
    playAgainBtn.onclick = function() {
      modal.style.display = "none";
      el("btn-new-game").click();
    };
  }
  el("summary-close").onclick = function() { modal.style.display = "none"; };
}

// ── End summary ───────────────────────────────────────────────────────────────

var _FAILURE_LABELS = {
  "timeout":               "⏱ timeout",
  "parse_error":           "⚠ parse error",
  "parse_error_rescued":   "⚠ parse error [guardrail ✓]",
  "api_error":             "✗ api error",
};

var _FAILURE_CLASSES = {
  "timeout":               "failure-timeout",
  "parse_error":           "failure-parse-error",
  "parse_error_rescued":   "failure-parse-rescued",
  "api_error":             "failure-api-error",
};

function _thinkingKey(lastAction) {
  return (lastAction.player_id || "") + "|" +
         (lastAction.action_label || "") + "|" +
         (lastAction.thinking || "") + "|" +
         (lastAction.failure_reason || "");
}

function _failureBadgeHtml(failureReason) {
  if (!failureReason) return "";
  var label = _FAILURE_LABELS[failureReason] || failureReason;
  var cls   = _FAILURE_CLASSES[failureReason] || "";
  return '<span class="thinking-failure-badge ' + cls + '">' + label + '</span>';
}

// Action-only summary for the human-mode sidebar (no reasoning, just name → action).
function _buildActionOnlyHtml(lastAction) {
  var name = (lastAction.player_id === HUMAN_ID)
    ? "Human"
    : (lastAction.display_name || lastAction.player_id || "Bot");
  var actionLabel = lastAction.action_label || "";
  var html = '<span class="thinking-name">' + escapeHtml(name) + ':</span>';
  if (actionLabel) {
    html += ' <span class="thinking-action">' + escapeHtml(actionLabel) + '</span>';
  }
  return html;
}

function _buildThinkingHtml(lastAction) {
  var name = lastAction.display_name || lastAction.player_id || "Bot";
  var thinking = lastAction.thinking || "";
  var actionLabel = lastAction.action_label || "";
  var failure = lastAction.failure_reason || null;

  var html = '<span class="thinking-name">' + escapeHtml(name) + ':</span>';
  html += _failureBadgeHtml(failure);
  if (thinking) {
    html += ' <span class="thinking-text">' + escapeHtml(thinking) + '</span>';
  }
  if (actionLabel) {
    // Only mark "(fallback)" for true fallbacks; guardrail-rescued actions are real.
    var isTrueFallback = failure && failure !== "parse_error_rescued";
    var label = isTrueFallback ? actionLabel + " (fallback)" : actionLabel;
    html += '<span class="thinking-action"> → ' + escapeHtml(label) + '</span>';
  }
  return html;
}

function renderThinking(lastAction) {
  if (!lastAction) return;

  if (SPECTATOR_MODE) {
    // Spectator: update the live thinking panel + append to log.
    var key = _thinkingKey(lastAction);
    if (key === lastThinkingKey) return;
    lastThinkingKey = key;
    var panel = el("thinking-panel");
    panel.innerHTML = _buildThinkingHtml(lastAction);
    panel.style.display = "block";
    appendToThinkingLog(lastAction, key);
  } else {
    // Human mode: append action-only entry for every player (no thinking panel).
    appendToThinkingLog(lastAction, null);
  }
}

function appendToThinkingLog(lastAction, key) {
  if (!key) key = _thinkingKey(lastAction);
  if (key === lastLogKey) return;
  lastLogKey = key;

  var entry = document.createElement("div");
  entry.className = "thinking-log-entry";
  // Spectator sees full thinking; human sees action label only (no reasoning).
  entry.innerHTML = SPECTATOR_MODE
    ? _buildThinkingHtml(lastAction)
    : _buildActionOnlyHtml(lastAction);

  var log = el("thinking-log");
  log.appendChild(entry);
  log.scrollTop = log.scrollHeight;
}

function appendHandResultToLog(state) {
  if (state.phase !== "hand_over" && state.phase !== "showdown") return;
  var winners = state.winners;
  if (!winners || !winners.length) return;

  // Dedup: key on hands_played + winner ids+amounts
  var key = "hand|" + (state.hands_played || 0) + "|" +
            winners.map(function(w) { return w.player_id + "+" + w.amount; }).join(",");
  if (key === lastHandResultKey) return;
  lastHandResultKey = key;

  var nameMap = {};
  (state.players || []).forEach(function(p) {
    nameMap[p.id] = (!SPECTATOR_MODE && p.id === HUMAN_ID) ? "Human" : (p.display_name || p.id);
  });

  var parts = winners.map(function(w) {
    var name = nameMap[w.player_id] || w.player_id;
    var s = '<span class="thinking-name">' + escapeHtml(name) + '</span>'
          + ' <span class="thinking-action">+' + w.amount + '</span>';
    if (w.hand_name) s += ' <span class="thinking-text">(' + escapeHtml(w.hand_name) + ')</span>';
    return s;
  });

  var handNum = (state.hands_played || 0) + 1;
  var label = (MAX_HANDS > 0) ? "Hand " + handNum + "/" + MAX_HANDS : "Hand " + handNum;

  var entry = document.createElement("div");
  entry.className = "thinking-log-entry hand-result";
  entry.innerHTML = '<span class="hand-result-label">— ' + label + ' —</span> ' + parts.join(' &amp; ');

  var log = el("thinking-log");
  log.appendChild(entry);
  log.scrollTop = log.scrollHeight;
}

function triggerBotMove() {
  if (botMoveInProgress || !gameId) return;
  botMoveInProgress = true;
  fetch(API + "/games/" + gameId + "/bot_move?viewer_id=" + HUMAN_ID, { method: "POST" })
    .then(function (r) {
      if (r.status === 404) { clearGame(); return null; }
      return r.json();
    })
    .then(function (s) {
      botMoveInProgress = false;
      if (s) renderState(s);
    })
    .catch(function () { botMoveInProgress = false; });
}

function renderActions(state) {
  var msg = el("message");
  var btns = el("action-buttons");
  btns.innerHTML = "";

  // Hand over / showdown
  if (state.phase === "hand_over" || state.phase === "showdown") {
    // Build a player-id → display_name lookup from current state
    var nameMap = {};
    (state.players || []).forEach(function(p) { nameMap[p.id] = p.display_name || p.id; });

    if (state.winners && state.winners.length) {
      var parts = [];
      for (var i = 0; i < state.winners.length; i++) {
        var w = state.winners[i];
        var wName = nameMap[w.player_id] || w.player_id;
        // Override player_0 name to "Human" in human mode
        if (!SPECTATOR_MODE && w.player_id === HUMAN_ID) wName = "Human";
        var wStr = wName + " (+" + w.amount + ")";
        if (w.hand_name) wStr += " with " + w.hand_name;
        else wStr += " — all others folded";
        parts.push(wStr);
      }
      // Show hand progress alongside winner when a limit is set
      var handNum = (state.hands_played || 0) + 1;
      var progress = (MAX_HANDS > 0) ? "  [Hand " + handNum + "/" + MAX_HANDS + "]" : "";
      msg.textContent = "Winner: " + parts.join(", ") + progress;
    } else {
      msg.textContent = "Hand over.";
    }
    // Show "Next hand" only when not spectator and the game isn't over yet
    if (!SPECTATOR_MODE && !shouldShowSummary(state)) {
      var nextBtn = document.createElement("button");
      nextBtn.textContent = "Next hand";
      nextBtn.onclick = function () {
        if (!gameId) return;
        fetch(API + "/games/" + gameId + "/next_hand?viewer_id=" + HUMAN_ID, { method: "POST" })
          .then(function (r) {
            if (r.status === 404) { clearGame(); return null; }
            return r.json();
          })
          .then(function (s) { if (s) renderState(s); });
      };
      btns.appendChild(nextBtn);
    }
    return;
  }

  // Determine whose turn it is
  var isMyTurn = !SPECTATOR_MODE && (state.current_player_id === HUMAN_ID);

  // My turn: show action buttons (never in spectator mode)
  if (isMyTurn && state.legal_actions && state.legal_actions.length > 0) {
    msg.textContent = "Your turn.";
    var raiseAction = null;

    // mainRow holds Fold / Check / Call / Raise / All-In (always shown first)
    var mainRow = document.createElement("div");
    mainRow.className = "action-main-row";

    for (var i = 0; i < state.legal_actions.length; i++) {
      (function (a) {
        if (a.type === "fold") {
          var b = document.createElement("button");
          b.className = "danger";
          b.textContent = "Fold";
          b.onclick = function () { raiseOpen = false; sendAction({ type: "fold" }); };
          mainRow.appendChild(b);
        } else if (a.type === "check") {
          var b = document.createElement("button");
          b.textContent = "Check";
          b.onclick = function () { raiseOpen = false; sendAction({ type: "check" }); };
          mainRow.appendChild(b);
        } else if (a.type === "call") {
          var b = document.createElement("button");
          b.textContent = "Call " + (a.amount || 0);
          b.onclick = function () { raiseOpen = false; sendAction({ type: "call", amount: a.amount }); };
          mainRow.appendChild(b);
        } else if (a.type === "raise") {
          raiseAction = a;
        }
      })(state.legal_actions[i]);
    }

    // Raise detail panel — replaces mainRow in-place when "Raise" is clicked
    var raiseDetail = null;
    if (raiseAction) {
      var minAmt = raiseAction.min_amount;
      var maxAmt = raiseAction.max_amount;
      var bb = (state.config && state.config.big_blind) || 10;

      var raiseBtn = document.createElement("button");
      raiseBtn.textContent = "Raise";
      mainRow.appendChild(raiseBtn);

      var allinBtn = document.createElement("button");
      allinBtn.className = "allin";
      allinBtn.textContent = "All In (" + maxAmt + ")";
      allinBtn.onclick = function () {
        raiseOpen = false;
        sendAction({ type: "raise", amount: maxAmt });
      };
      mainRow.appendChild(allinBtn);

      // Raise detail: slider + confirm + preset chips (1x/3x/5x BB) + cancel
      raiseDetail = document.createElement("div");
      raiseDetail.className = "raise-detail";
      raiseDetail.style.display = "none";

      var slider = document.createElement("input");
      slider.type = "range";
      slider.min = minAmt;
      slider.max = maxAmt;
      slider.value = minAmt;
      slider.className = "raise-slider";

      var amtLabel = document.createElement("span");
      amtLabel.className = "raise-label";
      amtLabel.textContent = minAmt;

      var confirmBtn = document.createElement("button");
      confirmBtn.textContent = "Raise to " + minAmt;
      slider.oninput = function () {
        amtLabel.textContent = slider.value;
        confirmBtn.textContent = "Raise to " + slider.value;
      };
      confirmBtn.onclick = function () {
        raiseOpen = false;
        sendAction({ type: "raise", amount: parseInt(slider.value, 10) });
      };

      raiseDetail.appendChild(slider);
      raiseDetail.appendChild(amtLabel);
      raiseDetail.appendChild(confirmBtn);

      // Preset buttons: 1x BB, 3x BB, 5x BB — show chip amount as number
      var presets = [ bb * 1, bb * 3, bb * 5 ];
      for (var pi = 0; pi < presets.length; pi++) {
        (function (amt) {
          if (amt >= minAmt && amt <= maxAmt) {
            var pb = document.createElement("button");
            pb.className = "preset";
            pb.textContent = amt;
            pb.onclick = function () {
              raiseOpen = false;
              sendAction({ type: "raise", amount: amt });
            };
            raiseDetail.appendChild(pb);
          }
        })(presets[pi]);
      }

      var cancelBtn = document.createElement("button");
      cancelBtn.textContent = "Cancel";
      cancelBtn.onclick = function () {
        raiseOpen = false;
        raiseDetail.style.display = "none";
        mainRow.style.display = "flex";
      };
      raiseDetail.appendChild(cancelBtn);

      // Toggle: clicking Raise swaps mainRow ↔ raiseDetail in the same row
      raiseBtn.onclick = function () {
        raiseOpen = true;
        mainRow.style.display = "none";
        raiseDetail.style.display = "flex";
      };
    }

    btns.appendChild(mainRow);
    if (raiseDetail) btns.appendChild(raiseDetail);
    return;
  }

  // Bot's turn: auto-trigger after a brief delay so the user can see who is acting.
  // In spectator mode the arena.py polling loop drives all moves instead.
  if (state.current_player_id != null && !isMyTurn) {
    var curPlayer = null;
    for (var pi = 0; pi < (state.players || []).length; pi++) {
      if (state.players[pi].id === state.current_player_id) { curPlayer = state.players[pi]; break; }
    }
    var curName = (curPlayer && curPlayer.display_name) || state.current_player_id;
    msg.textContent = curName + " is thinking...";
    if (!SPECTATOR_MODE) setTimeout(triggerBotMove, 500);
    return;
  }

  msg.textContent = "";
}

function renderState(state) {
  if (!state) return;
  lastStateJSON = JSON.stringify(state);
  renderPlayers(state);
  renderCommunity(state.community_cards || []);
  renderPot(state.pot || 0);
  renderProgress(state);
  renderThinking(state.last_action || null);
  appendHandResultToLog(state);
  renderActions(state);

  // Show summary overlay when the game is over (only once)
  if (shouldShowSummary(state)) {
    var modal = el("game-summary-modal");
    if (modal && modal.style.display !== "flex") {
      renderSummary(state);
    }
  }
}

function sendAction(action) {
  fetch(API + "/games/" + gameId + "/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ player_id: HUMAN_ID, action: action }),
  })
    .then(function (r) {
      if (r.status === 404) { clearGame(); return null; }
      if (!r.ok) return r.text().then(function (t) { throw new Error(t); });
      return r.json();
    })
    .then(function (s) { if (s) renderState(s); })
    .catch(function (e) {
      el("message").textContent = "Error: " + (e.message || "failed");
    });
}

function fetchState() {
  if (!gameId) return;
  // In spectator mode we don't pass viewer_id — the server then shows all
  // players' hole cards so human spectators can follow every model's hand.
  var stateUrl = SPECTATOR_MODE
    ? API + "/games/" + gameId
    : API + "/games/" + gameId + "?viewer_id=" + HUMAN_ID;
  fetch(stateUrl)
    .then(function (r) {
      if (r.status === 404) { clearGame(); return null; }
      return r.json();
    })
    .then(function (s) {
      if (!s) return;
      var newJSON = JSON.stringify(s);
      if (newJSON !== lastStateJSON && !raiseOpen) {
        renderState(s);
      }
    })
    .catch(function () {});
}

function newGame() {
  var numPlayers = parseInt(el("num-players").value, 10) || 2;
  var botIds = [];
  for (var i = 1; i < numPlayers; i++) {
    botIds.push("player_" + i);
  }
  fetch(API + "/games", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      num_players: numPlayers,
      small_blind: 5,
      big_blind: 10,
      starting_stack: 500,
      bot_player_ids: botIds,
    }),
  })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      gameId = data.game_id;
      lastStateJSON = "";
      botMoveInProgress = false;
      el("game-id").textContent = "Game: " + gameId;
      fetchState();
    })
    .catch(function (e) {
      el("message").textContent = "Failed to create game: " + (e.message || "error");
    });
}

el("btn-new-game").onclick = newGame;

// "Clear log" button
el("btn-clear-log").onclick = function() {
  el("thinking-log").innerHTML = "";
  lastLogKey = null;
};

// Activate the two-column sidebar layout for both spectator and human modes.
document.body.classList.add("sidebar-active");
if (SPECTATOR_MODE) {
  document.body.classList.add("spectator-mode");
} else {
  // Human mode: relabel the sidebar as "Action log" (no reasoning shown).
  document.body.classList.add("human-mode");
  var _logHeader = document.querySelector("#thinking-log-header span");
  if (_logHeader) _logHeader.textContent = "Action log";
}

// Auto-connect to a game passed via ?game_id= URL param (used by arena.py)
if (_initialGameId) {
  gameId = _initialGameId;
  if (!ARENA_MODE) el("game-id").textContent = "Game: " + gameId;
  el("message").textContent = SPECTATOR_MODE ? "Watching..." : "Create or join a game to play.";
  fetchState();
}

// ── Arena mode ────────────────────────────────────────────────────────────────
// When launched via arena.py (?arena=1): hide player-count selector and
// override "New Game" to restart the same configuration via /arena/restart.
if (ARENA_MODE) {
  var numLabel = document.querySelector('label[for="num-players"]');
  var numSelect = el("num-players");
  if (numLabel)  numLabel.style.display  = "none";
  if (numSelect) numSelect.style.display = "none";

  if (SPECTATOR_MODE) {
    // In spectator mode arena.py drives everything — hide the new game button.
    el("btn-new-game").style.display = "none";
  } else {
    // Human-vs-AI mode: restart game via /arena/restart, navigate to new game.
    el("btn-new-game").textContent = "New Game";
    el("btn-new-game").onclick = function() {
      var sourceId = gameId || _initialGameId;
      if (!sourceId) { newGame(); return; }
      fetch(API + "/arena/restart", { method: "POST" })
        .then(function(r) { return r.json(); })
        .then(function(data) {
          el("thinking-log").innerHTML = "";
          lastLogKey = null;
          lastThinkingKey = null;
          var restartUrl = "/?game_id=" + data.game_id + "&arena=1";
          if (MAX_HANDS > 0) restartUrl += "&hands=" + MAX_HANDS;
          window.location.href = restartUrl;
        })
        .catch(function(e) {
          el("message").textContent = "Restart failed: " + (e.message || "error");
        });
    };
  }
}

// Poll state to keep display in sync; only re-renders if state actually changed
setInterval(fetchState, 2000);
