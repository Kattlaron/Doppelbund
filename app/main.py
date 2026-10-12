from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .game import Game


BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Doppelbund")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
CARD_PLAY_DELAY_SECONDS = 2.0
BOT_NAMES = ("", "Anna", "Ben", "Clara")


class TableRequest(BaseModel):
    name: str = "Spieler"
    mode: Literal["ai", "human"] = "ai"


@dataclass
class Table:
    id: str
    mode: Literal["ai", "human"]
    players: dict[int, str]
    game: Game | None = None
    sockets: dict[int, WebSocket] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def bot_seats(self) -> set[int]:
        return {1, 2, 3} if self.mode == "ai" else set()

    @property
    def ready(self) -> bool:
        return self.mode == "ai" or len(self.players) == 4

    def player_names(self) -> tuple[str, str, str, str]:
        names = []
        for seat in range(4):
            if seat in self.players:
                names.append(self.players[seat])
            elif self.mode == "ai":
                names.append(BOT_NAMES[seat])
            else:
                names.append(f"Spieler {seat + 1}")
        return tuple(names)  # type: ignore[return-value]

    def start_game(self) -> None:
        if not self.ready or self.game is not None:
            return
        self.game = Game()
        self.game.start(
            round_number=1,
            match_scores=[0, 0, 0, 0],
            start_player=random.randrange(4),
            player_names=self.player_names(),
            automated_players=self.bot_seats,
        )

    def start_next_round(self) -> None:
        if self.game is None or not self.game.match_finished and self.game.phase.value != "finished":
            raise ValueError("Die aktuelle Partie ist noch nicht beendet.")
        if self.game.match_finished:
            raise ValueError("Das Gesamtspiel ist bereits beendet.")
        previous = self.game
        self.game = Game()
        self.game.start(
            round_number=previous.round_number + 1,
            match_scores=previous.match_scores,
            start_player=(previous.start_player + 1) % 4,
            player_names=self.player_names(),
            automated_players=self.bot_seats,
        )

    def lobby_view(self) -> dict:
        return {
            "table_id": self.id,
            "mode": self.mode,
            "players": [
                {
                    "seat": seat,
                    "name": name,
                    "connected": seat in self.sockets,
                }
                for seat, name in sorted(self.players.items())
            ],
            "player_count": len(self.players),
            "required_players": 1 if self.mode == "ai" else 4,
            "ready": self.ready,
        }


class TableManager:
    def __init__(self) -> None:
        self.tables: dict[str, Table] = {}

    def create(self, mode: Literal["ai", "human"], name: str) -> Table:
        table = Table(str(uuid.uuid4())[:8], mode, {0: name.strip() or "Spieler"})
        table.start_game()
        self.tables[table.id] = table
        return table

    def get(self, table_id: str) -> Table:
        if table_id not in self.tables:
            raise KeyError(table_id)
        return self.tables[table_id]

    def join(self, table_id: str, name: str) -> tuple[Table, int]:
        table = self.get(table_id)
        if table.mode == "ai":
            raise ValueError("Dieser Tisch spielt gegen die KI.")
        if len(table.players) >= 4:
            raise ValueError("Der Tisch ist bereits voll.")
        seat = next(seat for seat in range(4) if seat not in table.players)
        table.players[seat] = name.strip() or f"Spieler {seat + 1}"
        table.start_game()
        return table, seat


manager = TableManager()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/tables")
async def list_tables() -> list[dict]:
    return [table.lobby_view() for table in manager.tables.values()]


@app.post("/api/tables")
async def create_table(request: TableRequest) -> dict:
    table = manager.create(request.mode, request.name)
    return {"table_id": table.id, "seat": 0, "mode": table.mode}


@app.post("/api/tables/{table_id}/join")
async def join_table(table_id: str, request: TableRequest) -> dict:
    try:
        table, seat = manager.join(table_id, request.name)
    except KeyError:
        raise HTTPException(status_code=404, detail="Tisch nicht gefunden.")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    # Inform already connected browsers both while the table is still waiting
    # for players and when the fourth player makes the game ready.  Without
    # this broadcast, the lobby shown in the first browser stayed stale until
    # the page was refreshed.
    await broadcast_lobby_or_state(table)
    return {"table_id": table.id, "seat": seat, "mode": table.mode}


@app.websocket("/ws/{table_id}/{seat}")
async def table_socket(websocket: WebSocket, table_id: str, seat: int) -> None:
    try:
        table = manager.get(table_id)
        if seat not in table.players:
            await websocket.close(code=1008, reason="Unbekannter Sitzplatz")
            return
    except KeyError:
        await websocket.close(code=1008, reason="Unbekannter Tisch")
        return

    await websocket.accept()
    table.sockets[seat] = websocket
    try:
        await broadcast_lobby_or_state(table)
        if table.mode == "ai":
            await run_bots_until_human(table)
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            if message_type == "back_to_menu":
                await websocket.send_json({"type": "redirect", "url": "/"})
                continue
            if message_type == "choose_goal":
                async with table.lock:
                    try:
                        game = require_game(table)
                        game.choose_goal(seat, str(message.get("goal_id")))
                    except ValueError as exc:
                        await websocket.send_json({"type": "error", "message": str(exc)})
                        continue
                await broadcast_lobby_or_state(table)
                if table.mode == "ai":
                    await run_bots_until_human(table)
                continue
            if message_type == "next_round":
                async with table.lock:
                    try:
                        table.start_next_round()
                    except ValueError as exc:
                        await websocket.send_json({"type": "error", "message": str(exc)})
                        continue
                await broadcast_lobby_or_state(table)
                if table.mode == "ai":
                    await run_bots_until_human(table)
                continue
            if message_type != "play_card":
                await websocket.send_json({"type": "error", "message": "Unbekannter Befehl."})
                continue

            async with table.lock:
                try:
                    game = require_game(table)
                    game.play(seat, str(message.get("card_id")))
                except ValueError as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
                    continue
            await broadcast_after_move(table)
            if table.mode == "ai":
                await run_bots_until_human(table)
    except WebSocketDisconnect:
        pass
    finally:
        if table.sockets.get(seat) is websocket:
            del table.sockets[seat]
            # The remaining browsers should immediately see that this player
            # is temporarily offline and can wait for a reconnect.
            await broadcast_lobby_or_state(table)


def require_game(table: Table) -> Game:
    if table.game is None:
        raise ValueError("Der Tisch wartet noch auf weitere Spieler.")
    return table.game


async def broadcast_lobby_or_state(table: Table, include_result: bool = True) -> None:
    if table.game is None:
        payload = {"type": "lobby", "lobby": table.lobby_view()}
        await _broadcast(table, payload)
        return
    for seat in list(table.sockets):
        await _send_state(table, seat, {
            "type": "state",
            "state": state_for(table, seat, include_result=include_result),
        })


def state_for(table: Table, seat: int, include_result: bool = True) -> dict:
    if table.game is None:
        raise ValueError("Der Tisch wartet noch auf weitere Spieler.")
    state = table.game.view_for(seat, include_result=include_result)
    state["player_status"] = [
        "online" if player in table.sockets or player in table.bot_seats else "offline"
        for player in range(4)
    ]
    return state


async def _broadcast(table: Table, payload: dict) -> None:
    """Send a payload to all current connections and discard dead sockets."""
    for seat in list(table.sockets):
        await _send_payload(table, seat, payload)


async def _send_state(table: Table, seat: int, payload: dict) -> None:
    await _send_payload(table, seat, payload)


async def _send_payload(table: Table, seat: int, payload: dict) -> None:
    socket = table.sockets.get(seat)
    if socket is None:
        return
    try:
        await socket.send_json(payload)
    except (WebSocketDisconnect, OSError, RuntimeError):
        # A browser can disappear between taking the socket snapshot and the
        # send.  One abandoned connection must not prevent the other players
        # from receiving the current table state.
        if table.sockets.get(seat) is socket:
            del table.sockets[seat]


async def broadcast_after_move(table: Table) -> None:
    game = require_game(table)
    if game.phase.value == "finished":
        await broadcast_lobby_or_state(table, include_result=False)
        await asyncio.sleep(CARD_PLAY_DELAY_SECONDS)
    await broadcast_lobby_or_state(table)


async def run_bots_until_human(table: Table) -> None:
    if table.mode != "ai" or table.game is None:
        return
    game = table.game
    if not game._goal_selection_complete():
        return
    while game.phase.value == "playing" and game.current_player != 0:
        await asyncio.sleep(CARD_PLAY_DELAY_SECONDS)
        async with table.lock:
            card = game.choose_ai_card(game.current_player)
            game.play(game.current_player, card.id)
        await broadcast_after_move(table)
