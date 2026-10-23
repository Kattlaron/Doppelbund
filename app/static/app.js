let socket;
const query = new URLSearchParams(location.search);
let tableId = query.get("table");
let joinTableId = query.get("join");
let mySeat = Number(query.get("seat"));
let currentState = null;
let shouldReconnect = true;
let reconnectTimer = null;
let reconnectAttempt = 0;

const menuView = document.querySelector("#menu-view");
const gameView = document.querySelector("#game-view");
const suitClass = { "Rot": "rot", "Grün": "gruen", "Blau": "blau", "Gelb": "gelb", "Schwarz": "schwarz" };
const settingsStorageKey = "doppelbund-game-settings";

function readGameSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(settingsStorageKey) || "{}");
    return {
      use_goals: saved.use_goals !== false,
      use_events: saved.use_events !== false,
    };
  } catch {
    return { use_goals: true, use_events: true };
  }
}

function selectedGameSettings() {
  return {
    use_goals: document.querySelector("#setting-goals").checked,
    use_events: document.querySelector("#setting-events").checked,
  };
}

function saveGameSettings() {
  localStorage.setItem(settingsStorageKey, JSON.stringify(selectedGameSettings()));
}

const savedGameSettings = readGameSettings();
document.querySelector("#setting-goals").checked = savedGameSettings.use_goals;
document.querySelector("#setting-events").checked = savedGameSettings.use_events;

function playerName() {
  return document.querySelector("#player-name").value.trim() || "Spieler";
}

function goToTable(id, seat) {
  location.href = `/?table=${encodeURIComponent(id)}&seat=${seat}`;
}

function joinLink(id = tableId) {
  return `${location.origin}/?join=${encodeURIComponent(id)}`;
}

async function createTable(mode) {
  setMenuStatus("");
  try {
    const response = await fetch("/api/tables", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: playerName(), mode, ...selectedGameSettings() })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Tisch konnte nicht erstellt werden.");
    goToTable(data.table_id, data.seat);
  } catch (error) {
    setMenuStatus(error.message);
  }
}

async function joinTable(id) {
  const tableCode = id || document.querySelector("#table-id").value.trim();
  if (!tableCode) return setMenuStatus("Bitte einen Tisch-Code eingeben.");
  try {
    const response = await fetch(`/api/tables/${encodeURIComponent(tableCode)}/join`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: playerName(), mode: "human" })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Beitreten nicht möglich.");
    goToTable(data.table_id, data.seat);
  } catch (error) {
    setMenuStatus(error.message);
  }
}

async function refreshTables() {
  const list = document.querySelector("#table-list");
  try {
    const response = await fetch("/api/tables");
    const tables = await response.json();
    const openTables = tables.filter(table => table.mode === "human" && table.player_count < 4);
    list.innerHTML = openTables.length ? openTables.map(table => `
      <div class="table-row">
        <div><strong>${table.table_id}</strong><small>${table.player_count}/4 Spieler · ${table.players.map(player => player.name).join(", ")}</small><small>${table.settings?.use_goals === false ? "Ohne Aufträge" : "Mit Aufträgen"} · ${table.settings?.use_events === false ? "Ohne Events" : "Mit Events"}</small></div>
        <button class="secondary join-open-table" data-table-id="${table.table_id}">Beitreten</button>
      </div>`).join("") : "<p class='subtitle'>Keine offenen Tische vorhanden.</p>";
    list.querySelectorAll(".join-open-table").forEach(button =>
      button.addEventListener("click", () => joinTable(button.dataset.tableId))
    );
  } catch (error) {
    list.innerHTML = "<p class='menu-status'>Tische konnten nicht geladen werden.</p>";
  }
}

function setMenuStatus(text) {
  document.querySelector("#menu-status").textContent = text;
}

function showMenu() {
  menuView.hidden = false;
  gameView.hidden = true;
  if (joinTableId) {
    document.querySelector("#table-id").value = joinTableId;
  }
  refreshTables();
}

function openHelp() {
  document.querySelector("#help-modal").hidden = false;
}

function closeHelp() {
  document.querySelector("#help-modal").hidden = true;
}

function connect(id, seat) {
  menuView.hidden = true;
  gameView.hidden = false;
  tableId = id;
  mySeat = seat;
  shouldReconnect = true;
  openSocket();
}

function openSocket() {
  if (!shouldReconnect) return;
  if (socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(socket.readyState)) return;
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const connection = new WebSocket(`${protocol}://${location.host}/ws/${tableId}/${mySeat}`);
  socket = connection;
  showStatus(
    reconnectAttempt
      ? `Verbindung wird wiederhergestellt (Versuch ${reconnectAttempt}) …`
      : "Verbinde …",
    false
  );

  connection.onopen = () => {
    reconnectAttempt = 0;
    showStatus("Verbunden.", false);
  };
  connection.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "state") render(message.state);
    if (message.type === "lobby") renderLobby(message.lobby);
    if (message.type === "error") showStatus(message.message, true);
    if (message.type === "redirect") location.href = message.url;
  };
  connection.onerror = () => {
    // onclose performs the retry.  This handler only provides immediate
    // feedback while the browser is waiting for the close event.
    showStatus("Verbindungsfehler – neuer Versuch wird vorbereitet.", true);
  };
  connection.onclose = () => {
    if (socket === connection) socket = null;
    if (shouldReconnect) scheduleReconnect();
  };
}

function scheduleReconnect() {
  if (reconnectTimer || !shouldReconnect) return;
  reconnectAttempt += 1;
  const delay = Math.min(10000, 1000 * Math.pow(2, Math.min(reconnectAttempt - 1, 3)));
  showStatus(
    `Verbindung unterbrochen. Neuer Versuch in ${Math.ceil(delay / 1000)} s …`,
    true
  );
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    openSocket();
  }, delay);
}

async function copyJoinLink() {
  const link = joinLink();
  try {
    await navigator.clipboard.writeText(link);
  } catch {
    const input = document.createElement("input");
    input.value = link;
    document.body.appendChild(input);
    input.select();
    document.execCommand("copy");
    input.remove();
  }
  showStatus("Beitrittslink wurde kopiert.", false);
}

function wireCopyLinkButton() {
  const button = document.querySelector("#copy-join-link");
  if (button) button.addEventListener("click", copyJoinLink);
}

function requestNextRound() {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    showStatus("Keine Verbindung zum Tisch.", true);
    return;
  }
  document.querySelector("#next-round-top").hidden = true;
  socket.send(JSON.stringify({ type: "next_round" }));
}

function renderLobby(lobby) {
  gameView.hidden = false;
  document.querySelector("#event-banner").hidden = true;
  showStatus(`Warte auf weitere Spieler: ${lobby.player_count}/${lobby.required_players}`, false);
  const playersBySeat = new Map(lobby.players.map(player => [player.seat, player.name]));
  [0, 1, 2, 3].forEach(seat => {
    const joinedName = playersBySeat.get(seat);
    document.querySelector(`#name-${seat}`).textContent =
      joinedName || `Platz ${seat + 1} frei`;
    document.querySelector(`#count-${seat}`).textContent =
      joinedName ? "Beigetreten" : "Offener Platz";
    const area = document.querySelector(`#player-area-${seat}`);
    area.classList.remove("team-black", "team-white");
    area.classList.add("team-unknown");
    area.querySelector(".opponent").classList.toggle("you", seat === mySeat);
    const player = lobby.players.find(item => item.seat === seat);
    const status = document.querySelector(`#status-${seat}`);
    status.textContent = player?.connected ? "Online" : (joinedName ? "Offline" : "");
    status.className = `player-status ${player?.connected ? "online" : "offline"}`;
    document.querySelector(`#played-${seat}`).innerHTML = "";
    document.querySelector(`#opponent-hand-${seat}`).innerHTML = "";
    document.querySelector(`#event-public-cards-${seat}`).innerHTML = "";
  });
  document.querySelector("#history").innerHTML = [0, 1, 2, 3].map(seat => {
    const name = playersBySeat.get(seat);
    return `<li>${name
      ? `${name} sitzt auf Platz ${seat + 1}.`
      : `Platz ${seat + 1} ist noch frei.`}</li>`;
  }).join("");
  document.querySelector("#scores").innerHTML =
    `<div><span>Tisch-Code</span><strong>${lobby.table_id}</strong></div>
     <div><span>Modus</span><strong>Standart</strong></div>
     <div><span>Belegte Plätze</span><strong>${lobby.player_count}/${lobby.required_players}</strong></div>
     <button id="copy-join-link" class="secondary copy-link">Beitrittslink kopieren</button>`;
  wireCopyLinkButton();
  document.querySelector("#hand").innerHTML = "<p class='subtitle'>Die Karten werden ausgeteilt, sobald vier Spieler beigetreten sind.</p>";
  document.querySelector("#goal-status").textContent = "Aufträge werden nach dem Beitritt aller Spieler verteilt.";
  document.querySelector("#event-action-panel").hidden = true;
}

function render(state) {
  currentState = state;
  showStatus(state.message, false);
  const eventBanner = document.querySelector("#event-banner");
  const activeEvent = state.event;
  eventBanner.hidden = !activeEvent;
  if (activeEvent) {
    document.querySelector("#event-name").textContent = activeEvent.name;
    document.querySelector("#event-effect").textContent = activeEvent.effect;
  }
  renderDebugEventSelector(state, activeEvent);
  document.querySelector("#game-hint").textContent =
    `Klicke eine leuchtende Karte. ${activeEvent ? "Die Regeln des Events stehen oben." : "Es gelten die Standardregeln."}`;
  renderEventAction(state.event_action);
  renderPublicEventCards(state);
  document.querySelector("#next-round-top").hidden =
    !state.result || state.result.match_finished;
  document.querySelector("#debug-indicator").hidden = !state.debug_mode;
  state.player_names.forEach((name, seat) => {
    document.querySelector(`#name-${seat}`).textContent = name;
    document.querySelector(`#count-${seat}`).textContent = `${state.hand_counts[seat]} Karten`;
    const area = document.querySelector(`#player-area-${seat}`);
    const relativeSeat = (seat - mySeat + 4) % 4;
    const positionClass = ["player-bottom", "player-left", "player-top", "player-right"][relativeSeat];
    area.classList.remove("player-bottom", "player-left", "player-top", "player-right", "team-unknown", "team-black", "team-white");
    area.classList.add(positionClass, `team-${state.team_status[seat]}`);
    const eventPositionClass = [
      "event-near-bottom",
      "event-near-left",
      "event-near-top",
      "event-near-right",
    ][relativeSeat];
    const eventCardArea = document.querySelector(`#event-public-cards-${seat}`);
    eventCardArea.classList.remove(
      "event-near-bottom",
      "event-near-left",
      "event-near-top",
      "event-near-right",
    );
    eventCardArea.classList.add(eventPositionClass);
    area.querySelector(".opponent").classList.toggle("you", seat === mySeat);
    const connected = state.player_status?.[seat] === "online";
    const status = document.querySelector(`#status-${seat}`);
    status.textContent = connected ? "Online" : "Offline";
    status.className = `player-status ${connected ? "online" : "offline"}`;
  });
  [0, 1, 2, 3].forEach(seat => {
    document.querySelector(`#opponent-hand-${seat}`).innerHTML = seat === mySeat
      ? ""
      : '<span class="card-back"></span>'.repeat(state.hand_counts[seat]);
  });

  const scores = document.querySelector("#scores");
  const shareButton = state.table_mode === "human"
    ? `<button id="copy-join-link" class="secondary copy-link">Beitrittslink kopieren</button>`
    : "";
  scores.innerHTML = state.scores.map((score, seat) =>
    `<div><span>${state.player_names[seat]}${state.revealed_black_players.includes(seat) ? " · Team Schwarz" : ""}</span><strong>${score} Augen · ${state.match_scores[seat]} SP</strong></div>`
  ).join("") + `<hr><div><span>Partie</span><strong>${state.round_number}/${state.max_rounds}</strong></div>
    ${shareButton}`;
  wireCopyLinkButton();

  document.querySelectorAll(".played-slot").forEach(slot => { slot.innerHTML = ""; });
  state.trick.forEach(card => {
    if (card.concealed) {
      document.querySelector(`#played-${card.player}`).innerHTML =
        `<div class="played-hidden-card"><small class="played-by">${state.player_names[card.player]}</small><span>?</span><small>Verdeckt</small></div>`;
      return;
    }
    document.querySelector(`#played-${card.player}`).innerHTML =
      `<div class="card played-card suit-${suitClass[card.suit]} ${card.trump ? "trump" : ""} ${card.winner ? "winner-card" : ""}">
        <small class="played-by">${state.player_names[card.player]}</small><span class="rank">${card.rank}</span><small>${card.suit}</small><small class="points">${card.points} P</small>
      </div>`;
  });

  const legal = new Set(state.legal_ids);
  document.querySelector("#hand").innerHTML = state.hand.map(card => {
    const cardAction = state.event_action?.can_choose &&
      ["prophecy", "reveal"].includes(state.event_action.type);
    const enabled = legal.has(card.id) || cardAction;
    const actionClass = cardAction ? "selectable" : "";
    return `<button class="card suit-${suitClass[card.suit]} ${card.trump ? "trump" : ""} ${enabled ? "enabled" : ""} ${actionClass}" ${enabled ? "" : "disabled"} data-id="${card.id}">
      <span class="rank">${card.rank}</span><small>${card.suit}</small><small class="points">${card.points}${card.event_bonus ? "+1*" : ""} P</small>
    </button>`;
  }).join("");
  document.querySelectorAll(".card.enabled").forEach(button =>
    button.addEventListener("click", () => {
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        showStatus("Keine Verbindung zum Tisch.", true);
        return;
      }
      if (currentState.event_action?.type === "prophecy") {
        socket.send(JSON.stringify({ type: "choose_prophecy", card_id: button.dataset.id }));
      } else if (currentState.event_action?.type === "reveal") {
        socket.send(JSON.stringify({ type: "choose_reveal", card_id: button.dataset.id }));
      } else {
        socket.send(JSON.stringify({ type: "play_card", card_id: button.dataset.id }));
      }
    })
  );

  document.querySelector("#history").innerHTML = state.history.slice().reverse().map(item => `<li>${item}</li>`).join("");
  renderGoalState(state);
  renderResult(state.result, state.player_names);
}

function renderEventAction(action) {
  const panel = document.querySelector("#event-action-panel");
  const title = document.querySelector("#event-action-title");
  const message = document.querySelector("#event-action-message");
  const choices = document.querySelector("#event-action-choices");
  panel.hidden = !action;
  choices.innerHTML = "";
  if (!action) return;

  if (action.type === "prophecy") {
    title.textContent = "Prophezeiung";
    message.textContent = action.message;
  } else if (action.type === "reveal") {
    title.textContent = "Enthüllung";
    message.textContent = action.message;
  } else if (action.type === "choose_leader") {
    title.textContent = "Wandernder Thron";
    message.textContent = "Bestimme einen der drei anderen Spieler als Anspieler.";
    choices.innerHTML = (action.candidates || []).map(candidate =>
      `<button class="secondary event-choice-button" data-player="${candidate.player}">${candidate.name} spielt an</button>`
    ).join("");
    choices.querySelectorAll(".event-choice-button").forEach(button =>
      button.addEventListener("click", () => {
        if (socket && socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({
            type: "choose_next_leader",
            player: Number(button.dataset.player),
          }));
        }
      })
    );
  } else if (action.type === "waiting") {
    title.textContent = "Event-Aktion";
    message.textContent = action.message ||
      `Warte auf ${action.player_name} (${action.action}).`;
  }
}

function renderPublicEventCards(state) {
  [0, 1, 2, 3].forEach(seat => {
    const entries = [
      state.prophecy_cards?.[seat]
        ? { card: state.prophecy_cards[seat], label: "Prophezeiung" }
        : null,
      state.revealed_cards?.[seat]
        ? { card: state.revealed_cards[seat], label: "Offen gezeigt" }
        : null,
    ].filter(Boolean);
    document.querySelector(`#event-public-cards-${seat}`).innerHTML = entries.map(({ card, label }) =>
      `<div class="event-public-card">
        <small>${label}</small>
        <div class="card suit-${suitClass[card.suit]} ${card.trump ? "trump" : ""}">
          <span class="rank">${card.rank}</span><small>${card.suit}</small><small class="points">${card.points} P</small>
        </div>
      </div>`
    ).join("");
  });
}

function renderDebugEventSelector(state, activeEvent) {
  const controls = document.querySelector("#debug-event-controls");
  const select = document.querySelector("#debug-event-select");
  const note = document.querySelector("#debug-event-note");
  const visible = Boolean(state.debug_event_selector);
  controls.hidden = !visible;
  if (!visible) return;

  const target = state.debug_event_target === "current" ? "diese Partie" : "die nächste Partie";
  const selectedId = state.debug_event_target === "current"
    ? activeEvent?.id
    : (state.pending_event_id || "random");
  select.innerHTML = `<option value="random">Zufall</option>` +
    (state.debug_event_options || []).map(event =>
      `<option value="${event.id}">${event.name}</option>`
    ).join("");
  select.value = selectedId || "random";
  const selectedName = (state.debug_event_options || [])
    .find(event => event.id === selectedId)?.name;
  note.textContent = state.debug_event_target === "current"
    ? "Auswahl gilt für diese Partie."
    : selectedName
      ? `Für ${target} vorgemerkt: ${selectedName}.`
      : `Für ${target} gilt die Zufallsauswahl.`;
  select.onchange = () => {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      showStatus("Keine Verbindung zum Tisch.", true);
      return;
    }
    socket.send(JSON.stringify({ type: "select_event", event_id: select.value }));
  };
}

function renderGoalState(state) {
  const status = document.querySelector("#goal-status");
  const revealed = state.revealed_goals?.length
    ? `<div class="revealed-goal-list"><strong>Aufgedeckt:</strong>${state.revealed_goals.map(goal =>
        `<div>${goal.player_name}: ${goal.condition} (+${goal.reward} SP)</div>`
      ).join("")}</div>`
    : "";
  if (state.settings && !state.settings.use_goals) {
    status.innerHTML = `Aufträge sind für dieses Spiel deaktiviert.${revealed}`;
    document.querySelector("#goal-modal").hidden = true;
    return;
  }
  if (!state.selected_goal) {
    const inlineOptions = state.goal_options.map(goal =>
      `<button class="goal-inline-option" data-goal-id="${goal.id}">${goal.condition} <strong>+${goal.reward} SP</strong></button>`
    ).join("");
    status.innerHTML = `Bitte einen Auftrag auswählen.<div class="goal-inline-options">${inlineOptions}</div>${revealed}`;
    status.querySelectorAll(".goal-inline-option").forEach(button => {
      button.addEventListener("click", () => {
        if (!socket || socket.readyState !== WebSocket.OPEN) {
          showStatus("Keine Verbindung zum Tisch.", true);
          return;
        }
        socket.send(JSON.stringify({ type: "choose_goal", goal_id: button.dataset.goalId }));
      });
    });
  } else if (state.waiting_for_goals) {
    status.innerHTML =
      `<strong>Dein Auftrag:</strong> ${state.selected_goal.condition}<br>` +
      `Warte auf die Auftragswahl der anderen Spieler.${revealed}`;
  } else if (!state.prophecy_complete) {
    const prophecyStatus = state.event_action?.type === "prophecy"
      ? "Wähle jetzt verdeckt eine Karte aus deiner Hand."
      : "Deine Karte ist verdeckt gewählt. Sie wird erst aufgedeckt, wenn alle gewählt haben.";
    status.innerHTML =
      `<strong>Dein Auftrag:</strong> ${state.selected_goal.condition}<br>` +
      `${prophecyStatus}${revealed}`;
  } else if (state.selected_goal) {
    const completion = state.completed_goal
      ? `<br><strong class="goal-complete">Erfüllt: +${state.completed_goal.reward} SP</strong>`
      : "<br><span>Noch offen</span>";
    status.innerHTML = `<strong>${state.selected_goal.condition}</strong><br><span>Belohnung: ${state.selected_goal.reward} SP</span>${completion}${revealed}`;
  }
  document.querySelector("#goal-modal").hidden = true;
}

function renderResult(result, playerNames) {
  const modal = document.querySelector("#result-modal");
  const content = document.querySelector("#result-content");
  if (!result) {
    modal.hidden = true;
    return;
  }
  const winner = result.winning_team === 0 ? "Team Schwarz" : "Team Hell";
  const title = result.match_finished ? "Gesamtspiel beendet" : `Partie ${result.round_number} beendet`;
  const matchWinner = result.match_finished
    ? `<p class="winner">Gesamtspiel gewonnen hat: <strong>${playerNames[result.match_winner]}</strong></p>`
    : `<button id="next-round" class="secondary next-round">Nächste Partie</button>`;
  const revealedGoals = result.revealed_goals?.length
    ? `<h3>Erfüllte Aufträge</h3><ul class="revealed-goals">${result.revealed_goals.map(goal =>
        `<li>${goal.player_name}: ${goal.condition} <strong>+${goal.reward} SP</strong></li>`
      ).join("")}</ul>`
    : "";
  content.innerHTML = `<h2 id="result-title">${title}</h2>
    <p class="winner">Partiesieger: <strong>${winner}</strong></p>
    <p>Team Schwarz: <strong>${result.team_scores[0]} Punkte</strong> · Team Hell: <strong>${result.team_scores[1]} Punkte</strong></p>
    ${matchWinner}
    <h3>Gesamt-Siegpunkte</h3>
    <div class="result-match-scores">${result.match_scores.map((score, index) =>
      `<div><span>${playerNames[index]}</span><strong>${score}</strong></div>`
    ).join("")}</div>
    <div class="result-players">${result.players.map(item =>
      `<div><span>${playerNames[item.player]} · ${item.team === 0 ? "Team Schwarz" : "Team Hell"}</span><strong>${item.score}</strong></div>`
    ).join("")}</div>${revealedGoals}`;
  modal.hidden = false;
  const nextRound = document.querySelector("#next-round");
  if (nextRound) nextRound.addEventListener("click", requestNextRound);
}

function showStatus(text, error) {
  const element = document.querySelector("#status");
  element.textContent = text;
  element.classList.toggle("error", error);
}

document.querySelector("#create-ai").addEventListener("click", () => createTable("ai"));
document.querySelector("#create-human").addEventListener("click", () => createTable("human"));
document.querySelector("#setting-goals").addEventListener("change", saveGameSettings);
document.querySelector("#setting-events").addEventListener("change", saveGameSettings);
document.querySelector("#join-table").addEventListener("click", () => joinTable());
document.querySelector("#refresh-tables").addEventListener("click", refreshTables);
document.querySelector("#help-menu").addEventListener("click", openHelp);
document.querySelector("#help-game").addEventListener("click", openHelp);
document.querySelector("#next-round-top").addEventListener("click", requestNextRound);
document.querySelector("#new-game").addEventListener("click", () => {
  shouldReconnect = false;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = null;
  if (socket) socket.close();
  location.href = "/";
});
document.querySelector("#close-result").addEventListener("click", () => {
  document.querySelector("#result-modal").hidden = true;
});
document.querySelector("#result-modal").addEventListener("click", (event) => {
  if (event.target.id === "result-modal") event.currentTarget.hidden = true;
});
document.querySelector("#close-help").addEventListener("click", closeHelp);
document.querySelector("#help-modal").addEventListener("click", (event) => {
  if (event.target.id === "help-modal") closeHelp();
});
document.addEventListener("keydown", (event) => {
  if (
    event.ctrlKey &&
    event.shiftKey &&
    event.altKey &&
    event.key.toLowerCase() === "d"
  ) {
    event.preventDefault();
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      showStatus("Keine Verbindung zum Tisch.", true);
      return;
    }
    socket.send(JSON.stringify({ type: "toggle_debug" }));
    return;
  }
  if (event.key === "Escape") {
    closeHelp();
    document.querySelector("#result-modal").hidden = true;
  }
});

if (tableId && Number.isInteger(mySeat) && mySeat >= 0 && mySeat < 4) {
  connect(tableId, mySeat);
} else {
  showMenu();
}
