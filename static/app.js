const API = "";
let gameId = null;
const HUMAN_ID = "player_0";
let lastStateJSON = "";
let raiseOpen = false;
let botMoveInProgress = false;

function el(id) {
  return document.getElementById(id);
}

function clearGame() {
  gameId = null;
  lastStateJSON = "";
  raiseOpen = false;
  botMoveInProgress = false;
  el("game-id").textContent = "";
  el("players").innerHTML = "";
  el("community-cards").innerHTML = "";
  el("pot").textContent = "Pot: 0";
  el("message").textContent = "Game ended or server restarted. Click New game to play.";
  el("action-buttons").innerHTML = "";
}

function cardEl(code, hidden) {
  var wrap = document.createElement("span");
  wrap.className = "card-wrap";
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

function renderPlayers(state) {
  var container = el("players");
  container.innerHTML = "";
  var currentId = state.current_player_id;
  for (var i = 0; i < state.players.length; i++) {
    var p = state.players[i];
    var div = document.createElement("div");
    var cls = "player";
    if (p.folded) cls += " folded";
    if (p.id === currentId) cls += " current-turn";
    if (p.id === HUMAN_ID) cls += " is-human";
    div.className = cls;
    var label = p.display_name || p.id;
    if (p.id === HUMAN_ID) label += " (You)";
    div.innerHTML =
      '<div class="name">' + label + '</div>' +
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

function renderCommunity(codes) {
  var container = el("community-cards");
  container.innerHTML = "";
  for (var i = 0; i < codes.length; i++) {
    container.appendChild(cardEl(codes[i], false));
  }
}

function renderPot(pot) {
  el("pot").textContent = "Pot: " + pot;
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
    return;
  }

  // Determine whose turn it is
  var isMyTurn = (state.current_player_id === HUMAN_ID);

  // My turn: show action buttons
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
    // Raise controls inline (slider + confirm + all-in)
    if (raiseAction) {
      var raiseWrap = document.createElement("div");
      raiseWrap.className = "raise-controls";

      var raiseBtn = document.createElement("button");
      raiseBtn.textContent = "Raise...";
      raiseBtn.onclick = function () {
        raiseOpen = true;
        raiseDetail.style.display = "flex";
        raiseBtn.style.display = "none";
      };
      raiseWrap.appendChild(raiseBtn);

      var raiseDetail = document.createElement("div");
      raiseDetail.className = "raise-detail";
      raiseDetail.style.display = "none";

      var minAmt = raiseAction.min_amount;
      var maxAmt = raiseAction.max_amount;

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

      var allinBtn = document.createElement("button");
      allinBtn.className = "allin";
      allinBtn.textContent = "All in (" + maxAmt + ")";
      allinBtn.onclick = function () {
        raiseOpen = false;
        sendAction({ type: "raise", amount: maxAmt });
      };

      var cancelBtn = document.createElement("button");
      cancelBtn.textContent = "Cancel";
      cancelBtn.onclick = function () {
        raiseOpen = false;
        raiseDetail.style.display = "none";
        raiseBtn.style.display = "";
      };

      raiseDetail.appendChild(slider);
      raiseDetail.appendChild(amtLabel);
      raiseDetail.appendChild(confirmBtn);
      raiseDetail.appendChild(allinBtn);
      raiseDetail.appendChild(cancelBtn);
      raiseWrap.appendChild(raiseDetail);
      btns.appendChild(raiseWrap);
    }
    return;
  }

  // Bot's turn: auto-trigger after a brief delay so the user can see who is acting
  if (state.current_player_id != null && !isMyTurn) {
    msg.textContent = state.current_player_id + " is thinking...";
    setTimeout(triggerBotMove, 500);
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
  renderActions(state);
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
  fetch(API + "/games/" + gameId + "?viewer_id=" + HUMAN_ID)
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

// Poll state to keep display in sync; only re-renders if state actually changed
setInterval(fetchState, 2000);
