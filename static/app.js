const API = "";
let gameId = null;
const HUMAN_ID = "player_0";
let lastStateJSON = "";
let raiseOpen = false;
let botMoveInProgress = false;

// Thinking-panel/log dedup keys
let lastThinkingKey = null;
let lastLogKey = null;

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
  el("game-id").textContent = "";
  el("players").innerHTML = "";
  el("community-cards").innerHTML = "";
  el("pot").textContent = "Pot: 0";
  el("message").textContent = "Game ended or server restarted. Click New game to play.";
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
  var rx = 44; // horizontal radius in %
  var ry = 43; // vertical radius in %
  // Push diagonal seats (corners) further out so they don't overlap neighbors.
  // diagonalness is 1.0 at 45/135/225/315° and 0.0 at 0/90/180/270°.
  var diagonalness = Math.abs(Math.sin(2 * angle));
  var boost = 1 + 0.22 * diagonalness;
  var left = 50 - rx * boost * Math.sin(angle);
  var top  = 50 + ry * boost * Math.cos(angle);
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
      if (p.folded) cls += " folded";
      if (p.id === currentId) cls += " current-turn";
      if (p.id === HUMAN_ID) cls += " is-human";
      if (existing.className !== cls) existing.className = cls;

      // Update name + badges
      var label = p.display_name || p.id;
      if (!SPECTATOR_MODE && p.id === HUMAN_ID) label += " (You)";
      var badges = "";
      if (i === dealerIdx) badges += '<span class="badge badge-dealer">D</span>';
      if (i === sbIdx) badges += '<span class="badge badge-sb">SB</span>';
      if (i === bbIdx) badges += '<span class="badge badge-bb">BB</span>';
      var nameEl = existing.querySelector(".name");
      var nameHTML = label + badges;
      if (nameEl.innerHTML !== nameHTML) nameEl.innerHTML = nameHTML;

      // Update stack
      var stackEl = existing.querySelector(".stack");
      var stackText = "Stack: " + p.stack;
      if (stackEl.textContent !== stackText) stackEl.textContent = stackText;

      // Update bet
      var betEl = existing.querySelector(".bet");
      var betText = "Bet: " + p.current_bet;
      if (betEl.textContent !== betText) betEl.textContent = betText;

      // Only rebuild cards if they actually changed
      var newCK = cardsKey(p);
      if (existing.getAttribute("data-cards") !== newCK) {
        existing.setAttribute("data-cards", newCK);
        var cardsEl = existing.querySelector(".cards");
        cardsEl.innerHTML = "";
        if (p.hole_cards && p.hole_cards.length > 0) {
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
      if (p.folded) cls += " folded";
      if (p.id === currentId) cls += " current-turn";
      if (p.id === HUMAN_ID) cls += " is-human";
      div.className = cls;

      div.style.top = pos.top;
      div.style.left = pos.left;

      var label = p.display_name || p.id;
      if (!SPECTATOR_MODE && p.id === HUMAN_ID) label += " (You)";
      var badges = "";
      if (i === dealerIdx) badges += '<span class="badge badge-dealer">D</span>';
      if (i === sbIdx) badges += '<span class="badge badge-sb">SB</span>';
      if (i === bbIdx) badges += '<span class="badge badge-bb">BB</span>';

      var ck = cardsKey(p);
      div.setAttribute("data-cards", ck);

      div.innerHTML =
        '<div class="name">' + label + badges + '</div>' +
        '<div class="stack">Stack: ' + p.stack + '</div>' +
        '<div class="bet">Bet: ' + p.current_bet + '</div>' +
        '<div class="cards"></div>';
      var cardsEl = div.querySelector(".cards");
      if (p.hole_cards && p.hole_cards.length > 0) {
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

// ── Game-over summary ─────────────────────────────────────────────────────────

function shouldShowSummary(state) {
  var phase = state.phase;
  if (phase !== "hand_over" && phase !== "showdown") return false;
  // Arena.py explicitly flagged the session as finished
  if (state.arena_finished) return true;
  // Natural end: only 1 player has chips (works outside arena mode too)
  var active = (state.players || []).filter(function(p) { return p.stack > 0; });
  if (active.length <= 1 && (state.players || []).length > 1) return true;
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

  var html = '<div class="summary-card">';
  html += '<h2 class="summary-title">🏆 Game Over</h2>';
  html += '<p class="summary-subtitle">Final results &mdash; ' + handsPlayed + ' hand' + (handsPlayed !== 1 ? "s" : "") + ' played</p>';
  html += '<div class="summary-rankings">';

  ranked.forEach(function(r) {
    var medal = medals[r.rank - 1] || ("#" + r.rank);
    var name  = r.player.display_name || r.player.id;
    var pf    = failStats[r.player.id] || {};
    var totalMoves   = pf.total_moves || 0;
    var totalFails   = (pf.timeout || 0) + (pf.parse_error || 0) + (pf.parse_error_rescued || 0) + (pf.api_error || 0);

    html += '<div class="summary-row rank-' + r.rank + '">';
    html += '<span class="summary-medal">' + medal + '</span>';
    html += '<span class="summary-name">' + escapeHtml(name) + '</span>';
    if (r.type === "alive") {
      html += '<span class="summary-chips">' + r.player.stack.toLocaleString() + ' chips</span>';
    } else {
      html += '<span class="summary-bust">busted (hand ' + r.hand_number + ')</span>';
    }
    html += '</div>';

    // Failure stats line (only show when there's move data to report)
    if (totalMoves > 0) {
      var failCls = totalFails > 0 ? " has-failures" : "";
      html += '<div class="summary-failures' + failCls + '">';
      if (totalFails === 0) {
        html += '✓ ' + totalMoves + ' moves &mdash; 0 failures';
      } else {
        var pct = Math.round(totalFails / totalMoves * 100);
        var parts = [];
        if (pf.timeout)               parts.push(pf.timeout + ' timeout' + (pf.timeout > 1 ? 's' : ''));
        if (pf.parse_error)           parts.push(pf.parse_error + ' parse error' + (pf.parse_error > 1 ? 's' : '') + ' (fallback)');
        if (pf.parse_error_rescued)   parts.push(pf.parse_error_rescued + ' parse error' + (pf.parse_error_rescued > 1 ? 's' : '') + ' (guardrail ✓)');
        if (pf.api_error)             parts.push(pf.api_error + ' api error' + (pf.api_error > 1 ? 's' : ''));
        html += totalFails + '/' + totalMoves + ' moves failed (' + pct + '%) &mdash; ' + parts.join(', ');
      }
      html += '</div>';
    }
  });

  html += '</div>';  // .summary-rankings
  html += '<div class="summary-buttons">';
  if (ARENA_MODE && !SPECTATOR_MODE) {
    html += '<button class="primary" id="summary-play-again">Play Again</button>';
  }
  html += '<button id="summary-close">Close</button>';
  html += '</div>';
  html += '</div>';  // .summary-card

  modal.innerHTML = html;
  modal.style.display = "flex";

  // Tell arena.py the leaderboard is now visible so it can exit cleanly.
  if (ARENA_MODE && SPECTATOR_MODE) {
    fetch(API + "/arena/ack_summary", { method: "POST" }).catch(function(){});
  }

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
  if (!lastAction) return;  // keep old content visible; never hide between turns
  var key = _thinkingKey(lastAction);
  if (key === lastThinkingKey) return;  // same action, skip re-render
  lastThinkingKey = key;

  var panel = el("thinking-panel");
  panel.innerHTML = _buildThinkingHtml(lastAction);
  panel.style.display = "block";

  appendToThinkingLog(lastAction, key);
}

function appendToThinkingLog(lastAction, key) {
  if (!key) key = _thinkingKey(lastAction);
  if (key === lastLogKey) return;  // already logged
  lastLogKey = key;

  var entry = document.createElement("div");
  entry.className = "thinking-log-entry";
  entry.innerHTML = _buildThinkingHtml(lastAction);

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
    if (state.winners && state.winners.length) {
      var parts = [];
      for (var i = 0; i < state.winners.length; i++) {
        parts.push(state.winners[i].player_id + " (+" + state.winners[i].amount + ")");
      }
      msg.textContent = "Winners: " + parts.join(", ");
    } else {
      msg.textContent = "Hand over.";
    }
    if (!SPECTATOR_MODE) {
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
    for (var i = 0; i < state.legal_actions.length; i++) {
      (function (a) {
        if (a.type === "fold") {
          var b = document.createElement("button");
          b.className = "danger";
          b.textContent = "Fold";
          b.onclick = function () { raiseOpen = false; sendAction({ type: "fold" }); };
          btns.appendChild(b);
        } else if (a.type === "check") {
          var b = document.createElement("button");
          b.textContent = "Check";
          b.onclick = function () { raiseOpen = false; sendAction({ type: "check" }); };
          btns.appendChild(b);
        } else if (a.type === "call") {
          var b = document.createElement("button");
          b.textContent = "Call " + (a.amount || 0);
          b.onclick = function () { raiseOpen = false; sendAction({ type: "call", amount: a.amount }); };
          btns.appendChild(b);
        } else if (a.type === "raise") {
          raiseAction = a;
        }
      })(state.legal_actions[i]);
    }
    // Raise + All-in buttons inline with fold/check/call
    if (raiseAction) {
      var minAmt = raiseAction.min_amount;
      var maxAmt = raiseAction.max_amount;
      var bb = (state.config && state.config.big_blind) || 10;

      var raiseBtn = document.createElement("button");
      raiseBtn.textContent = "Raise";
      raiseBtn.onclick = function () {
        raiseOpen = true;
        raiseDetail.style.display = "flex";
      };
      btns.appendChild(raiseBtn);

      var allinBtn = document.createElement("button");
      allinBtn.className = "allin";
      allinBtn.textContent = "All in (" + maxAmt + ")";
      allinBtn.onclick = function () {
        raiseOpen = false;
        sendAction({ type: "raise", amount: maxAmt });
      };
      btns.appendChild(allinBtn);

      // Raise detail row (slider + preset buttons + cancel) — shown below
      var raiseDetail = document.createElement("div");
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

      // Preset buttons: 3x BB, 5x BB — show the actual chip amount
      var presets = [
        { multiplier: 3, amount: bb * 3 },
        { multiplier: 5, amount: bb * 5 }
      ];
      for (var pi = 0; pi < presets.length; pi++) {
        (function (preset) {
          var amt = preset.amount;
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
      };
      raiseDetail.appendChild(cancelBtn);

      btns.appendChild(raiseDetail);
    }
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
  renderThinking(state.last_action || null);
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
    el("btn-new-game").textContent = "New game";
    el("btn-new-game").onclick = function() {
      var sourceId = gameId || _initialGameId;
      if (!sourceId) { newGame(); return; }
      fetch(API + "/arena/restart", { method: "POST" })
        .then(function(r) { return r.json(); })
        .then(function(data) {
          el("thinking-log").innerHTML = "";
          lastLogKey = null;
          lastThinkingKey = null;
          window.location.href = "/?game_id=" + data.game_id + "&arena=1";
        })
        .catch(function(e) {
          el("message").textContent = "Restart failed: " + (e.message || "error");
        });
    };
  }
}

// Poll state to keep display in sync; only re-renders if state actually changed
setInterval(fetchState, 2000);
