from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from .events import GameEvent, load_supported_events
from .goals import Goal, load_goals


SUITS = ("Rot", "Grün", "Blau", "Gelb")
RANKS = ("1", "2", "3", "4", "5")
PLAYER_NAMES = ("Du", "Anna", "Ben", "Clara")
MAX_ROUNDS = 4


class Phase(str, Enum):
    PLAYING = "playing"
    FINISHED = "finished"


@dataclass(frozen=True)
class Card:
    id: str
    suit: str
    rank: str

    @property
    def label(self) -> str:
        return self.rank

    @property
    def points(self) -> int:
        if self.suit == "Schwarz":
            return 0
        return {"1": 3, "2": 2, "3": 1, "4": 0, "5": 0}[self.rank]


@dataclass
class TrickCard:
    player: int
    card: Card


def make_deck() -> list[Card]:
    return [
        Card(f"{suit[:2]}-{rank}-{copy}", suit, rank)
        for suit in SUITS
        for rank in RANKS
        for copy in (1, 2)
    ] + [
        Card(f"sc-{rank}-{copy}", "Schwarz", rank)
        for rank in ("6", "7")
        for copy in (1, 2)
    ]


def is_trump(
    card: Card,
    event_id: str | None = None,
    trick_number: int | None = None,
) -> bool:
    if event_id == "silence" and (trick_number is None or trick_number <= 3):
        return card.suit == "Schwarz"
    if event_id == "balance":
        return card.suit == "Schwarz"
    return card.suit in ("Schwarz", "Rot")


def card_category(
    card: Card,
    lead: Card | None,
    event_id: str | None = None,
    trick_number: int | None = None,
) -> str:
    if event_id == "silence" and (trick_number is None or trick_number <= 3):
        return "trump" if card.suit == "Schwarz" else "suit"
    if is_trump(card, event_id, trick_number):
        return "trump"
    if lead and not is_trump(lead, event_id, trick_number) and card.suit == lead.suit:
        return "suit"
    return "off"


def beats(
    candidate: Card,
    current: Card,
    lead: Card,
    event_id: str | None = None,
    trick_cards: Iterable[Card] | None = None,
    trick_number: int | None = None,
) -> bool:
    candidate_category = card_category(candidate, lead, event_id, trick_number)
    current_category = card_category(current, lead, event_id, trick_number)
    if candidate_category == "trump" and current_category != "trump":
        return True
    if candidate_category != "trump" and current_category == "trump":
        return False
    if candidate_category == "suit" and current_category != "suit":
        return True
    if candidate_category != "suit" and current_category == "suit":
        return False
    if candidate_category in ("trump", "suit") and current_category in ("trump", "suit"):
        if event_id == "cycle" and candidate.suit == current.suit:
            cards_in_trick = list(trick_cards) if trick_cards is not None else [
                candidate,
                current,
                lead,
            ]
            five_is_in_trick = any(
                card.suit == candidate.suit and card.rank == "5"
                for card in cards_in_trick
            )
            if five_is_in_trick:
                if candidate.rank == "1" and current.rank != "1":
                    return True
                if current.rank == "1" and candidate.rank != "1":
                    return False
        if event_id == "rebellion":
            return int(candidate.rank) < int(current.rank)
        order = {str(value): value for value in range(1, 8)}
        return order[candidate.rank] > order[current.rank]
    return False


@dataclass
class Game:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    round_number: int = 1
    start_player: int = 0
    event: GameEvent | None = None
    use_goals: bool = True
    use_events: bool = True
    player_names: tuple[str, str, str, str] = PLAYER_NAMES
    match_scores: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    hands: list[list[Card]] = field(default_factory=lambda: [[] for _ in range(4)])
    current_player: int = 0
    trick: list[TrickCard] = field(default_factory=list)
    trick_number: int = 1
    scores: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    phase: Phase = Phase.PLAYING
    last_message: str = ""
    history: list[str] = field(default_factory=list)
    teams: list[int] = field(default_factory=lambda: [0, 1, 0, 1])
    revealed_black_players: set[int] = field(default_factory=set)
    played_black_players: set[int] = field(default_factory=set)
    last_trick: list[TrickCard] = field(default_factory=list)
    last_trick_winner: int | None = None
    prophecy_cards: list[Card | None] = field(default_factory=lambda: [None] * 4)
    revelation_cards: list[Card | None] = field(default_factory=lambda: [None] * 4)
    blocked_lead_card_ids: list[str | None] = field(default_factory=lambda: [None] * 4)
    pending_next_leader: int | None = None
    pending_reveal_player: int | None = None
    goals: list[Goal] = field(default_factory=list)
    goal_options: list[list[Goal]] = field(default_factory=lambda: [[] for _ in range(4)])
    selected_goals: list[Goal | None] = field(default_factory=lambda: [None, None, None, None])
    completed_goals: list[Goal | None] = field(default_factory=lambda: [None, None, None, None])
    revealed_goals: list[dict[str, Any]] = field(default_factory=list)
    _automated_players: set[int] = field(default_factory=set, repr=False)
    prophecy_selected: list[bool] = field(default_factory=lambda: [False] * 4)
    captured_cards: list[list[Card]] = field(default_factory=lambda: [[] for _ in range(4)])
    won_tricks: list[list[list[Card]]] = field(default_factory=lambda: [[] for _ in range(4)])
    trick_winners: list[int] = field(default_factory=list)
    round_result: dict[str, Any] | None = None
    match_finished: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def start(
        self,
        round_number: int = 1,
        match_scores: list[int] | None = None,
        start_player: int | None = None,
        player_names: tuple[str, str, str, str] | None = None,
        automated_players: set[int] | None = None,
        event: GameEvent | None = None,
        use_goals: bool = True,
        use_events: bool = True,
    ) -> None:
        self.round_number = round_number
        self.use_goals = use_goals
        self.use_events = use_events
        if start_player is not None:
            self.start_player = start_player
        if player_names is not None:
            self.player_names = player_names
        if not use_events:
            event = None
        elif event is None:
            event = random.choice(load_supported_events())
        self.event = event
        automated_players = set(automated_players or ())
        self._automated_players = automated_players
        if match_scores is not None:
            self.match_scores = match_scores[:]
        self.goals = load_goals() if use_goals else []
        self.goal_options = (
            [random.sample(self.goals, 2) for _ in range(4)]
            if use_goals
            else [[] for _ in range(4)]
        )
        self.selected_goals = [
            random.choice(options) if use_goals and player in automated_players else None
            for player, options in enumerate(self.goal_options)
        ]
        self.completed_goals = [None, None, None, None]
        self.revealed_goals = []
        self.captured_cards = [[] for _ in range(4)]
        self.won_tricks = [[] for _ in range(4)]
        self.trick_winners = []
        self.round_result = None
        self.match_finished = False
        # Die beiden schwarzen 6er definieren Team Schwarz.
        # Neu mischen, falls beide 6er beim gleichen Spieler landen.
        while True:
            deck = make_deck()
            random.shuffle(deck)
            hands = [deck[i * 11 : (i + 1) * 11] for i in range(4)]
            black_six_holders = {
                player
                for player, hand in enumerate(hands)
                if any(card.suit == "Schwarz" and card.rank == "6" for card in hand)
            }
            if len(black_six_holders) == 2:
                break
        self.hands = [
            sorted(hand, key=lambda c: (c.suit != "Schwarz", c.suit, int(c.rank)))
            for hand in hands
        ]
        self.teams = [0 if player in black_six_holders else 1 for player in range(4)]
        self.current_player = self.start_player
        self.trick = []
        self.last_trick = []
        self.last_trick_winner = None
        self.revealed_black_players = set()
        self.played_black_players = set()
        self.prophecy_cards = [None] * 4
        self.prophecy_selected = [False] * 4
        self.revelation_cards = [None] * 4
        self.blocked_lead_card_ids = [None] * 4
        self.pending_next_leader = None
        self.pending_reveal_player = None
        self.trick_number = 1
        self.scores = [0, 0, 0, 0]
        self.phase = Phase.PLAYING
        if use_goals:
            setup_message = "Wähle einen Auftrag."
        elif self.event_id == "prophecy":
            setup_message = "Wähle eine Karte für die Prophezeiung."
        else:
            setup_message = "Das Spiel beginnt."
        self.last_message = setup_message
        self.history = [
            f"Partie {self.round_number} gestartet.",
            *([f"Event: {self.event.name}."] if self.event else []),
            f"Startspieler: {self.player_names[self.start_player]}.",
            setup_message,
        ]
        self._choose_automated_prophecy_cards()

    @property
    def event_id(self) -> str | None:
        return self.event.id if self.event else None

    def _is_trump(self, card: Card, trick_number: int | None = None) -> bool:
        return is_trump(
            card,
            self.event_id,
            self.trick_number if trick_number is None else trick_number,
        )

    def _beats(
        self,
        candidate: Card,
        current: Card,
        lead: Card,
        trick_cards: Iterable[Card] | None = None,
    ) -> bool:
        if trick_cards is None:
            trick_cards = [played.card for played in self.trick]
            if candidate not in trick_cards:
                trick_cards = [*trick_cards, candidate]
        return beats(
            candidate,
            current,
            lead,
            self.event_id,
            trick_cards,
            self.trick_number,
        )

    def _is_concealed_event_card(self, play_order: int, trick_number: int) -> bool:
        if self.event_id == "darkness":
            return play_order == 2
        if self.event_id == "mystery":
            return trick_number == 2
        return False

    def _reveal_concealed_event_cards(
        self,
        trick: list[TrickCard],
        trick_number: int,
    ) -> None:
        concealed = [
            (play_order, played)
            for play_order, played in enumerate(trick, start=1)
            if self._is_concealed_event_card(play_order, trick_number)
        ]
        for _, played in concealed:
            if played.card.suit == "Schwarz" and played.card.rank == "6":
                self._reveal_black_player(played.player)
        if not concealed:
            return

        if self.event_id == "mystery":
            revealed = "; ".join(
                f"{self.player_names[played.player]}: {played.card.rank} {played.card.suit}"
                for _, played in concealed
            )
            self.history.append(f"Stich {trick_number} aufgedeckt: {revealed}.")
        else:
            for _, played in concealed:
                self.history.append(
                    f"Die verdeckte Karte von {self.player_names[played.player]} "
                    f"war {played.card.rank} {played.card.suit}."
                )

    @property
    def prophecy_complete(self) -> bool:
        return self.event_id != "prophecy" or all(self.prophecy_selected)

    @property
    def pending_event_actor(self) -> int | None:
        if (
            self.event_id == "prophecy"
            and self._goal_selection_complete()
            and not self.prophecy_complete
        ):
            return next(
                (
                    player for player in sorted(self._automated_players)
                    if not self.prophecy_selected[player]
                ),
                None,
            )
        if self.pending_next_leader is not None:
            return self.pending_next_leader
        return self.pending_reveal_player

    def _reveal_black_player(self, player: int) -> None:
        if player not in self.revealed_black_players:
            self.revealed_black_players.add(player)
            self.history.append(f"{self.player_names[player]} ist Team Schwarz.")

    def _choose_ai_public_card(self, player: int) -> Card:
        """Choose a low-risk card for an AI player to reveal publicly."""
        goal = self.selected_goals[player]

        def strategic_penalty(card: Card) -> int:
            if goal is None:
                return 0
            target_color = {
                "capture_four_blue": "Blau",
                "capture_four_green": "Grün",
                "capture_four_yellow": "Gelb",
                "capture_four_red": "Rot",
            }.get(goal.id)
            target_three = {
                "win_blue_3": ("Blau", "3"),
                "win_red_3": ("Rot", "3"),
                "win_green_3": ("Grün", "3"),
                "win_yellow_3": ("Gelb", "3"),
            }.get(goal.id)
            if target_three == (card.suit, card.rank):
                return 20
            if target_color == card.suit:
                return 5
            return 0

        return min(
            self.hands[player],
            key=lambda card: (
                strategic_penalty(card),
                card.points,
                self._is_trump(card),
                int(card.rank),
            ),
        )

    def _choose_automated_prophecy_cards(self) -> None:
        if self.event_id != "prophecy" or not self._goal_selection_complete():
            return
        for player in sorted(self._automated_players):
            if not self.prophecy_selected[player]:
                card = self._choose_ai_public_card(player)
                self.choose_prophecy_card(player, card.id)

    def choose_prophecy_card(self, player: int, card_id: str) -> None:
        if self.event_id != "prophecy":
            raise ValueError("Diese Partie hat kein Prophezeiungs-Event.")
        if not self._goal_selection_complete():
            raise ValueError("Alle Spieler müssen zuerst einen Auftrag wählen.")
        if self.phase != Phase.PLAYING or self.prophecy_selected[player]:
            raise ValueError("Du hast bereits eine Karte für die Prophezeiung gewählt.")
        card = next((card for card in self.hands[player] if card.id == card_id), None)
        if card is None:
            raise ValueError("Diese Karte ist nicht auf deiner Hand.")
        self.prophecy_cards[player] = card
        self.prophecy_selected[player] = True
        self.history.append(
            f"{self.player_names[player]} hat verdeckt eine Karte für die Prophezeiung gewählt."
        )
        if self.prophecy_complete:
            self.history.append("Alle Prophezeiungskarten werden aufgedeckt.")
            for seat, chosen_card in enumerate(self.prophecy_cards):
                if (
                    chosen_card is not None
                    and chosen_card.suit == "Schwarz"
                    and chosen_card.rank == "6"
                ):
                    self._reveal_black_player(seat)

    def choose_next_leader(self, player: int, next_player: int) -> None:
        if self.event_id != "wandering_throne" or self.pending_next_leader != player:
            raise ValueError("Du darfst derzeit keinen Anspieler bestimmen.")
        if next_player not in range(4) or next_player == player:
            raise ValueError("Wähle einen der drei anderen Spieler.")
        self.current_player = next_player
        self.pending_next_leader = None
        self.history.append(
            f"{self.player_names[player]} bestimmt {self.player_names[next_player]} "
            "als Anspieler für den nächsten Stich."
        )
        self.last_message = f"{self.player_names[next_player]} spielt den nächsten Stich an."

    def choose_revealed_card(self, player: int, card_id: str) -> None:
        if self.event_id != "revelation" or self.pending_reveal_player != player:
            raise ValueError("Du darfst derzeit keine Karte offen zeigen.")
        card = next((card for card in self.hands[player] if card.id == card_id), None)
        if card is None:
            raise ValueError("Diese Karte ist nicht auf deiner Hand.")
        self.revelation_cards[player] = card
        self.blocked_lead_card_ids[player] = card.id
        self.pending_reveal_player = None
        self.history.append(
            f"{self.player_names[player]} zeigt offen {card.rank} {card.suit}."
        )
        if card.suit == "Schwarz" and card.rank == "6":
            self._reveal_black_player(player)
        self.last_message = f"{self.player_names[player]} hat eine Karte offen gezeigt."

    def choose_ai_event_action(self, player: int) -> None:
        if self.pending_next_leader == player:
            partner = self.known_teammate(player)
            candidates = [seat for seat in range(4) if seat != player]
            target = partner if partner in candidates else random.choice(candidates)
            self.choose_next_leader(player, target)
            return
        if self.pending_reveal_player == player:
            card = min(
                self.hands[player],
                key=lambda item: (
                    item.points,
                    self._is_trump(item),
                    int(item.rank),
                ),
            )
            self.choose_revealed_card(player, card.id)

    def _points_for_trick(self, trick: list[TrickCard], trick_number: int) -> int:
        points = sum(played.card.points for played in trick)
        if self.event_id == "harvest":
            points += sum(played.card.suit == "Grün" for played in trick)
        if self.event_id == "storm" and trick_number >= 9:
            points *= 2
        return points

    def _displayed_card_points(self, card: Card, trick_number: int) -> int:
        points = card.points
        if self.event_id == "harvest" and card.suit == "Grün":
            points += 1
        if self.event_id == "storm" and trick_number >= 9:
            points *= 2
        return points

    def event_view(self) -> dict[str, str] | None:
        if self.event is None:
            return None
        return {"id": self.event.id, "name": self.event.name, "effect": self.event.effect}

    def set_event_before_play(
        self,
        event: GameEvent,
        automated_players: set[int] | None = None,
    ) -> None:
        """Replace the event while the dealt cards have not been played yet."""
        if any(len(hand) < 11 for hand in self.hands):
            raise ValueError("Das Event kann nach dem ersten ausgespielten Zug nicht mehr geändert werden.")
        if automated_players is not None:
            self._automated_players = set(automated_players)
        previous_event_id = self.event_id
        self.event = event
        # No card has been played yet, so any public team discovery must have
        # come from the old prophecy setup and is no longer applicable.
        self.revealed_black_players.clear()
        self.played_black_players.clear()
        self.history = [
            item for item in self.history
            if "zeigt für die Prophezeiung" not in item
            and "ist Team Schwarz." not in item
        ]
        self.pending_next_leader = None
        self.pending_reveal_player = None
        self.revelation_cards = [None] * 4
        self.blocked_lead_card_ids = [None] * 4
        if previous_event_id == "prophecy" or event.id == "prophecy":
            self.prophecy_cards = [None] * 4
            self.prophecy_selected = [False] * 4
        if event.id == "prophecy" and self.use_goals:
            # Goal choices come first. Entering this event therefore starts
            # that choice phase again before anyone selects a card to reveal.
            self.selected_goals = [
                random.choice(options) if player in self._automated_players else None
                for player, options in enumerate(self.goal_options)
            ]
        if self.use_goals:
            setup_message = "Wähle einen Auftrag."
        elif event.id == "prophecy":
            setup_message = "Wähle eine Karte für die Prophezeiung."
        else:
            setup_message = "Das Spiel beginnt."
        self.history = [
            f"Event: {event.name}." if item.startswith("Event: ")
            else setup_message if item in {
                "Wähle eine Karte für die Prophezeiung.",
                "Wähle einen Auftrag.",
            }
            else item
            for item in self.history
        ]
        self.last_message = setup_message
        self._choose_automated_prophecy_cards()

    @staticmethod
    def _goal_view(goal: Goal | None) -> dict[str, Any] | None:
        if goal is None:
            return None
        return {"id": goal.id, "condition": goal.condition, "reward": goal.reward}

    def choose_goal(self, player: int, goal_id: str) -> None:
        if not self.use_goals:
            raise ValueError("Aufträge sind für diese Partie deaktiviert.")
        if self.phase != Phase.PLAYING:
            raise ValueError("Die Partie ist bereits beendet.")
        if self.selected_goals[player] is not None:
            raise ValueError("Du hast bereits einen Auftrag gewählt.")
        selected = next((goal for goal in self.goal_options[player] if goal.id == goal_id), None)
        if selected is None:
            raise ValueError("Dieser Auftrag wurde dir nicht angeboten.")
        self.selected_goals[player] = selected
        if not self._goal_selection_complete():
            self.last_message = "Warte auf die Auftragsauswahl der anderen Spieler."
        elif self.event_id == "prophecy" and not self.prophecy_complete:
            self.last_message = "Wähle eine Karte verdeckt für die Prophezeiung."
        else:
            self.last_message = (
                "Du bist am Zug."
                if self.current_player == player
                else f"{self.player_names[self.current_player]} ist am Zug."
            )
        self.history.append(f"{self.player_names[player]} hat einen Auftrag gewählt.")
        self._choose_automated_prophecy_cards()

    def _complete_goal(self, player: int, goal: Goal) -> None:
        if self.completed_goals[player] is not None:
            return
        self.completed_goals[player] = goal
        self.match_scores[player] += goal.reward
        self.revealed_goals.append({
            "player": player,
            "player_name": self.player_names[player],
            "id": goal.id,
            "condition": goal.condition,
            "reward": goal.reward,
        })
        self.history.append(
            f"{self.player_names[player]} erfüllt den Auftrag und erhält {goal.reward} Siegpunkte."
        )

    def _check_goal_after_trick(self, player: int, trick: list[TrickCard]) -> None:
        if not self.use_goals:
            return
        goal = self.selected_goals[player]
        if goal is None or self.completed_goals[player] is not None:
            return
        cards = [played.card for played in trick]
        if goal.id == "win_non_five_color_trick":
            won_card = next(item.card for item in trick if item.player == player)
            fulfilled = not self._is_trump(won_card) and won_card.rank != "5"
        elif goal.id == "win_trick_two_colors":
            fulfilled = len({card.suit for card in cards if not self._is_trump(card)}) >= 2
        elif goal.id == "win_trick_six_points":
            fulfilled = self._points_for_trick(trick, self.trick_number) >= 6
        elif goal.id in {"win_blue_3", "win_red_3", "win_green_3", "win_yellow_3"}:
            color = {
                "win_blue_3": "Blau",
                "win_red_3": "Rot",
                "win_green_3": "Grün",
                "win_yellow_3": "Gelb",
            }[goal.id]
            fulfilled = any(card.suit == color and card.rank == "3" for card in cards)
        elif goal.id == "win_two_tricks_in_row":
            fulfilled = len(self.trick_winners) >= 2 and self.trick_winners[-2:] == [player, player]
        else:
            fulfilled = False
        if fulfilled:
            self._complete_goal(player, goal)

    def _check_end_goals(self) -> None:
        if not self.use_goals:
            return
        for player, goal in enumerate(self.selected_goals):
            if goal is None or self.completed_goals[player] is not None:
                continue
            captured = self.captured_cards[player]
            won_count = len(self.won_tricks[player])
            color_counts = {suit: sum(card.suit == suit for card in captured) for suit in SUITS}
            if goal.id == "win_last_trick":
                fulfilled = self.trick_winners[-1:] == [player]
            elif goal.id == "win_exactly_three_tricks":
                fulfilled = won_count == 3
            elif goal.id == "capture_no_five":
                fulfilled = all(card.rank != "5" for card in captured)
            elif goal.id.startswith("capture_four_"):
                color = {
                    "capture_four_blue": "Blau",
                    "capture_four_green": "Grün",
                    "capture_four_yellow": "Gelb",
                    "capture_four_red": "Rot",
                }[goal.id]
                fulfilled = color_counts[color] >= 4
            else:
                fulfilled = False
            if fulfilled:
                self._complete_goal(player, goal)

    def _goal_selection_complete(self) -> bool:
        return not self.use_goals or all(goal is not None for goal in self.selected_goals)

    def legal_cards(self, player: int) -> list[Card]:
        hand = self.hands[player]
        if not self.trick:
            if (
                self.event_id == "revelation"
                and self.blocked_lead_card_ids[player]
                and len(hand) > 1
            ):
                return [
                    card for card in hand
                    if card.id != self.blocked_lead_card_ids[player]
                ]
            return hand[:]
        if (
            self.event_id == "silence" and self.trick_number <= 3
        ) or (
            self.event_id == "mystery" and self.trick_number == 2
        ):
            return hand[:]
        lead = self.trick[0].card
        lead_category = "trump" if self._is_trump(lead) else lead.suit
        matching = [
            c for c in hand
            if ("trump" if self._is_trump(c) else c.suit) == lead_category
        ]
        return matching or hand[:]

    def play(self, player: int, card_id: str) -> None:
        if self.phase != Phase.PLAYING:
            raise ValueError("Das Spiel ist bereits beendet.")
        if not self._goal_selection_complete():
            raise ValueError("Alle Spieler müssen zuerst einen Auftrag wählen.")
        if not self.prophecy_complete:
            raise ValueError("Alle Spieler müssen zuerst eine Prophezeiungskarte wählen.")
        if self.pending_next_leader is not None or self.pending_reveal_player is not None:
            raise ValueError("Die ausstehende Event-Auswahl muss zuerst abgeschlossen werden.")
        if player != self.current_player:
            raise ValueError("Du bist nicht am Zug.")
        card = next((c for c in self.hands[player] if c.id == card_id), None)
        if card is None:
            raise ValueError("Diese Karte ist nicht auf deiner Hand.")
        if card not in self.legal_cards(player):
            raise ValueError("Diese Karte darfst du für diesen Zug nicht ausspielen.")
        is_lead = not self.trick
        if is_lead and self.blocked_lead_card_ids[player]:
            self.blocked_lead_card_ids[player] = None
        self.hands[player].remove(card)
        self.trick.append(TrickCard(player, card))
        play_order = len(self.trick)
        hidden = self._is_concealed_event_card(play_order, self.trick_number)
        if hidden:
            self.history.append(f"{self.player_names[player]} spielt verdeckt.")
        else:
            self.history.append(f"{self.player_names[player]} spielt {card.rank} {card.suit}.")
        if self.prophecy_cards[player] is not None and self.prophecy_cards[player].id == card.id:
            self.prophecy_cards[player] = None
        if self.revelation_cards[player] is not None and self.revelation_cards[player].id == card.id:
            self.revelation_cards[player] = None
            self.blocked_lead_card_ids[player] = None
        if card.suit == "Schwarz" and card.rank == "6":
            self.played_black_players.add(player)
            if not hidden:
                self._reveal_black_player(player)

        if len(self.trick) < 4:
            self.current_player = (player + 1) % 4
            self.last_message = f"{self.player_names[self.current_player]} ist am Zug."
            return

        winner = self.trick[0].player
        lead = self.trick[0].card
        trick_cards = [played.card for played in self.trick]
        for played in self.trick[1:]:
            if self._beats(
                played.card,
                next(x.card for x in self.trick if x.player == winner),
                lead,
                trick_cards,
            ):
                winner = played.player
        points = self._points_for_trick(self.trick, self.trick_number)
        self.scores[winner] += points
        self.history.append(f"{self.player_names[winner]} gewinnt Stich {self.trick_number} ({points} Augen).")
        won_cards = self.trick[:]
        completed_trick_number = self.trick_number
        self._reveal_concealed_event_cards(won_cards, completed_trick_number)
        self.captured_cards[winner].extend(item.card for item in won_cards)
        self.won_tricks[winner].append([item.card for item in won_cards])
        self.trick_winners.append(winner)
        self._check_goal_after_trick(winner, won_cards)
        self.current_player = winner
        self.last_trick = self.trick[:]
        self.last_trick_winner = winner
        self.trick = []
        self.trick_number += 1
        if not any(self.hands):
            self._check_end_goals()
            self._finish_round()
            self.phase = Phase.FINISHED
            self.last_message = "Spiel beendet."
        else:
            if self.event_id == "wandering_throne":
                self.pending_next_leader = winner
                self.last_message = (
                    f"{self.player_names[winner]} bestimmt den Anspieler für den nächsten Stich."
                )
            elif self.event_id == "revelation":
                self.pending_reveal_player = winner
                self.last_message = (
                    f"{self.player_names[winner]} muss eine Karte offen zeigen."
                )
            else:
                self.last_message = f"{self.player_names[winner]} gewinnt den Stich und ist am Zug."

    def _finish_round(self) -> None:
        team_scores = [
            sum(self.scores[seat] for seat, team in enumerate(self.teams) if team == team_id)
            for team_id in (0, 1)
        ]
        winning_team = 0 if team_scores[0] > team_scores[1] else 1
        for player, team in enumerate(self.teams):
            if team == winning_team:
                self.match_scores[player] += 2
        self.match_finished = self.round_number >= MAX_ROUNDS
        winner = max(range(4), key=lambda player: self.match_scores[player])
        self.round_result = {
            "round_number": self.round_number,
            "team_scores": team_scores,
            "winning_team": winning_team,
            "player_scores": self.scores[:],
            "match_scores": self.match_scores[:],
            "match_finished": self.match_finished,
            "match_winner": winner if self.match_finished else None,
            "revealed_goals": self.revealed_goals[:],
        }

    def choose_ai_card(self, player: int) -> Card:
        base_card = self._choose_base_ai_card(player)
        return self._adjust_ai_card_for_goal(player, base_card)

    def _adjust_ai_card_for_goal(self, player: int, base_card: Card) -> Card:
        goal = self.selected_goals[player]
        if goal is None or self.completed_goals[player] is not None:
            return base_card

        legal = self.legal_cards(player)
        if not legal:
            return base_card

        partner = self.known_teammate(player)
        pool = legal
        if partner is not None and self.trick:
            best_player, best_card = self._current_trick_winner()
            if best_player == partner:
                safe = [card for card in legal if not self._beats(card, best_card, self.trick[0].card)]
                pool = safe or [base_card]

        def would_win(card: Card) -> bool:
            if not self.trick:
                return False
            _, best_card = self._current_trick_winner()
            return self._beats(card, best_card, self.trick[0].card)

        trick_cards = [item.card for item in self.trick]
        target_suit = {
            "capture_four_blue": "Blau",
            "capture_four_green": "Grün",
            "capture_four_yellow": "Gelb",
            "capture_four_red": "Rot",
        }.get(goal.id)
        target_card = {
            "win_blue_3": ("Blau", "3"),
            "win_red_3": ("Rot", "3"),
            "win_green_3": ("Grün", "3"),
            "win_yellow_3": ("Gelb", "3"),
        }.get(goal.id)

        def goal_bonus(card: Card) -> int:
            wins = would_win(card)
            bonus = 0
            if goal.id == "capture_no_five":
                if wins and (card.rank == "5" or any(item.rank == "5" for item in trick_cards)):
                    bonus -= 60
                elif card.rank == "5":
                    bonus -= 8
            elif goal.id in {"capture_four_blue", "capture_four_green", "capture_four_yellow", "capture_four_red"}:
                captured_count = sum(item.suit == target_suit for item in self.captured_cards[player])
                if captured_count < 4 and card.suit == target_suit:
                    bonus += 8
                if wins and any(item.suit == target_suit for item in trick_cards + [card]):
                    bonus += 25
            elif goal.id == "win_non_five_color_trick":
                if wins and not self._is_trump(card) and card.rank != "5":
                    bonus += 35
            elif goal.id == "win_trick_two_colors":
                colors = {item.suit for item in trick_cards + [card] if not self._is_trump(item)}
                if wins and len(colors) >= 2:
                    bonus += 35
            elif goal.id == "win_trick_six_points":
                candidate_trick = self.trick + [TrickCard(player, card)]
                if wins and self._points_for_trick(candidate_trick, self.trick_number) >= 6:
                    bonus += 35
            elif goal.id in {"win_blue_3", "win_red_3", "win_green_3", "win_yellow_3"}:
                if card.suit == target_card[0] and card.rank == target_card[1]:
                    bonus += 12
                    if wins or (not self.trick and len(self.hands[player]) <= 1):
                        bonus += 45
            elif goal.id == "win_two_tricks_in_row":
                if wins and self.trick_winners[-1:] == [player]:
                    bonus += 35
            elif goal.id == "win_exactly_three_tricks":
                if wins and len(self.won_tricks[player]) < 3:
                    bonus += 30
                elif wins and len(self.won_tricks[player]) >= 3:
                    bonus -= 50
            elif goal.id == "win_last_trick":
                if len(self.hands[player]) <= 1 and wins:
                    bonus += 50
            return bonus

        return max(
            pool,
            key=lambda card: (
                goal_bonus(card),
                1 if card.id == base_card.id else 0,
                -card.points,
                -int(card.rank),
            ),
        )

    def _choose_base_ai_card(self, player: int) -> Card:
        legal = self.legal_cards(player)
        if not legal:
            raise ValueError("Der Computer hat keine erlaubte Karte.")

        # Beim Anspiel wird bevorzugt eine wertlose Farbkarte abgelegt.
        # Gibt es keine 5, wird ein möglichst billiger roter Trumpf gespielt.
        if not self.trick:
            color_fives = [card for card in legal if not self._is_trump(card) and card.rank == "5"]
            if color_fives:
                color_counts = {
                    suit: sum(
                        not self._is_trump(hand_card) and hand_card.suit == suit
                        for hand_card in self.hands[player]
                    )
                    for suit in SUITS
                }
                color_points = {
                    suit: sum(
                        hand_card.points
                        for hand_card in self.hands[player]
                        if not self._is_trump(hand_card) and hand_card.suit == suit
                    )
                    for suit in SUITS
                }
                # Eine 5 in der kürzesten Farbkarte entlastet diese Farbe
                # und lässt längere Farben als spätere Bedienoption offen.
                # Bei gleicher Länge wird die Farbe mit dem höheren
                # Punktepotenzial zuerst eröffnet.
                return min(
                    color_fives,
                    key=lambda card: (
                        color_counts[card.suit],
                        -color_points[card.suit],
                        self._card_value(card),
                    ),
                )

            red_trumps = [card for card in legal if card.suit == "Rot" and self._is_trump(card)]
            if red_trumps:
                return min(red_trumps, key=lambda card: self._card_value(card))

            non_black = [card for card in legal if card.suit != "Schwarz"]
            return min(non_black or legal, key=lambda card: self._card_value(card))

        lead = self.trick[0].card
        best_player, best_card = self._current_trick_winner()
        teammate = self.known_teammate(player)

        # Wenn der Partner bereits gewinnt, wird der Stich nicht überboten.
        # Stattdessen wird möglichst eine wertvolle Karte abgegeben.
        if teammate == best_player:
            safe_cards = [card for card in legal if not self._beats(card, best_card, lead)]
            safe_color_cards = [card for card in safe_cards if not self._is_trump(card)]
            return max(
                safe_color_cards or safe_cards or legal,
                key=lambda card: (
                    card.points + (1 if self.event_id == "harvest" and card.suit == "Grün" else 0),
                    self._rank_strength(card),
                ),
            )

        if self._is_trump(lead):
            # Trumpfstich: gerade so hoch über den aktuellen Gewinner gehen.
            winning_trumps = [card for card in legal if self._beats(card, best_card, lead)]
            if winning_trumps:
                return min(winning_trumps, key=lambda card: (self._rank_strength(card), card.points))
        else:
            # Farbspiel: möglichst einen wertvollen Farbpunkt stechen.
            winning_color_cards = [
                card for card in legal
                if not self._is_trump(card) and card.suit == lead.suit and self._beats(card, best_card, lead)
            ]
            if winning_color_cards:
                return max(winning_color_cards, key=lambda card: (card.points, int(card.rank)))

            # Wenn die Farbe nicht bedient werden kann, mit dem kleinsten
            # ausreichenden Trumpf übernehmen.
            winning_trumps = [card for card in legal if self._is_trump(card) and self._beats(card, best_card, lead)]
            if winning_trumps:
                return min(winning_trumps, key=lambda card: (self._rank_strength(card), card.points))

        # Kein sinnvoller Stichgewinn: möglichst wenige Punkte wegwerfen.
        return min(legal, key=lambda card: self._card_value(card))

    def _rank_strength(self, card: Card) -> int:
        rank = int(card.rank)
        return -rank if self.event_id == "rebellion" else rank

    def _card_value(self, card: Card) -> tuple[int, int, bool]:
        """Sortierschlüssel für billige Abwurfkarten."""
        return card.points, self._rank_strength(card), self._is_trump(card)

    def _current_trick_winner(self) -> tuple[int, Card]:
        winner = self.trick[0].player
        lead = self.trick[0].card
        trick_cards = [played.card for played in self.trick]
        for played in self.trick[1:]:
            current = next(item.card for item in self.trick if item.player == winner)
            if self._beats(played.card, current, lead, trick_cards):
                winner = played.player
        winning_card = next(item.card for item in self.trick if item.player == winner)
        return winner, winning_card

    def known_teammate(self, player: int) -> int | None:
        """Gibt nur einen Partner zurück, den der Spieler regelkonform kennt."""
        has_black_six = any(
            card.suit == "Schwarz" and card.rank == "6"
            for card in self.hands[player]
        ) or player in self.played_black_players or player in self.revealed_black_players

        if has_black_six:
            known_black_partners = self.revealed_black_players - {player}
            return min(known_black_partners) if known_black_partners else None

        # Erst wenn beide schwarzen 6er offen liegen, ist für Team Hell
        # der andere Nicht-Schwarz-Spieler eindeutig bekannt.
        if len(self.revealed_black_players) == 2:
            known_black = set(self.revealed_black_players)
            possible_partners = set(range(4)) - known_black - {player}
            if len(possible_partners) == 1:
                return next(iter(possible_partners))
        return None

    def _event_action_view(self, player: int) -> dict[str, Any] | None:
        if self.use_goals and self.event_id == "prophecy" and not self._goal_selection_complete():
            if self.selected_goals[player] is None:
                return None
            actor = next(
                index for index, goal in enumerate(self.selected_goals)
                if goal is None
            )
            return {
                "type": "waiting",
                "action": "Auftragswahl",
                "player": actor,
                "player_name": self.player_names[actor],
            }

        if self.event_id == "prophecy" and not self.prophecy_complete:
            if not self.prophecy_selected[player]:
                return {
                    "type": "prophecy",
                    "can_choose": True,
                    "message": "Wähle eine Karte aus deiner Hand. Sie bleibt verdeckt, bis alle gewählt haben.",
                }
            return {
                "type": "waiting",
                "action": "Prophezeiungswahl",
                "player_name": "den übrigen Spielern",
                "message": "Deine Karte bleibt verdeckt, bis alle Spieler gewählt haben.",
            }

        if self.pending_next_leader is not None:
            actor = self.pending_next_leader
            if actor == player:
                return {
                    "type": "choose_leader",
                    "can_choose": True,
                    "candidates": [
                        {"player": seat, "name": self.player_names[seat]}
                        for seat in range(4)
                        if seat != actor
                    ],
                }
            return {
                "type": "waiting",
                "action": "Anspieler-Auswahl",
                "player": actor,
                "player_name": self.player_names[actor],
            }

        if self.pending_reveal_player is not None:
            actor = self.pending_reveal_player
            if actor == player:
                return {
                    "type": "reveal",
                    "can_choose": True,
                    "message": "Wähle eine Karte aus deiner Hand, die du offen zeigst.",
                }
            return {
                "type": "waiting",
                "action": "Karten-Enthüllung",
                "player": actor,
                "player_name": self.player_names[actor],
            }
        return None

    def view_for(self, player: int = 0, include_result: bool = True) -> dict[str, Any]:
        result = None
        if include_result and self.phase == Phase.FINISHED and self.round_result is not None:
            result = dict(self.round_result)
            result["players"] = [
                {"player": seat, "team": self.teams[seat], "score": self.scores[seat]}
                for seat in range(4)
            ]
        if self.use_goals and self.event_id == "prophecy" and not self._goal_selection_complete():
            if self.selected_goals[player] is None:
                viewer_message = "Wähle zuerst einen Auftrag."
            else:
                actor = next(
                    index for index, goal in enumerate(self.selected_goals)
                    if goal is None
                )
                viewer_message = f"Warte auf {self.player_names[actor]}, der einen Auftrag wählt."
        elif self.event_id == "prophecy" and not self.prophecy_complete:
            viewer_message = (
                "Wähle eine Karte verdeckt für die Prophezeiung."
                if not self.prophecy_selected[player]
                else "Deine Karte ist verdeckt gewählt. Warte auf die übrigen Spieler."
            )
        elif self.pending_next_leader is not None:
            actor = self.pending_next_leader
            viewer_message = (
                "Bestimme, wer den nächsten Stich anspielt."
                if actor == player
                else f"{self.player_names[actor]} bestimmt den Anspieler."
            )
        elif self.pending_reveal_player is not None:
            actor = self.pending_reveal_player
            viewer_message = (
                "Zeige eine Karte offen."
                if actor == player
                else f"{self.player_names[actor]} zeigt eine Karte offen."
            )
        elif self.use_goals and self.selected_goals[player] is None:
            viewer_message = "Wähle einen Auftrag."
        elif self.use_goals and not self._goal_selection_complete():
            viewer_message = "Warte auf die Auftragsauswahl der anderen Spieler."
        elif self.phase == Phase.FINISHED:
            viewer_message = self.last_message
        elif self.current_player == player:
            viewer_message = "Du bist am Zug."
        else:
            viewer_message = f"{self.player_names[self.current_player]} ist am Zug."
        displayed_trick = self.trick if self.trick else self.last_trick
        displayed_trick_number = self.trick_number if self.trick else max(1, self.trick_number - 1)
        displayed_trick_view = []
        for play_order, played in enumerate(displayed_trick, start=1):
            concealed = (
                bool(self.trick)
                and played.player != player
                and self._is_concealed_event_card(play_order, displayed_trick_number)
            )
            if concealed:
                displayed_trick_view.append({
                    "player": played.player,
                    "concealed": True,
                    "winner": False,
                })
            else:
                displayed_trick_view.append({
                    "player": played.player,
                    "label": played.card.label,
                    "suit": played.card.suit,
                    "rank": played.card.rank,
                    "points": self._displayed_card_points(played.card, displayed_trick_number),
                    "trump": self._is_trump(played.card, displayed_trick_number),
                    "winner": self.last_trick_winner == played.player if not self.trick else False,
                })
        team_status = [
            "black" if player in self.revealed_black_players
            else "white" if len(self.revealed_black_players) == 2
            else "unknown"
            for player in range(4)
        ]
        return {
            "game_id": self.id,
            "phase": self.phase.value,
            "current_player": self.current_player,
            "trick_number": self.trick_number,
            "scores": self.scores,
            "match_scores": self.match_scores,
            "round_number": self.round_number,
            "max_rounds": MAX_ROUNDS,
            "event": self.event_view(),
            "settings": {
                "use_goals": self.use_goals,
                "use_events": self.use_events,
            },
            "start_player": self.start_player,
            "start_player_name": self.player_names[self.start_player],
            "player_names": self.player_names,
            "revealed_black_players": sorted(self.revealed_black_players),
            "team_status": team_status,
            "result": result,
            "goal_selection_required": self.use_goals and self.selected_goals[player] is None,
            "waiting_for_goals": not self._goal_selection_complete(),
            "goal_options": [
                self._goal_view(goal) for goal in self.goal_options[player]
            ] if self.use_goals and self.selected_goals[player] is None else [],
            "prophecy_complete": self.prophecy_complete,
            "prophecy_cards": [
                None if not self.prophecy_complete or card is None else {
                    "player": seat,
                    "rank": card.rank,
                    "suit": card.suit,
                    "points": card.points,
                    "trump": self._is_trump(card),
                }
                for seat, card in enumerate(self.prophecy_cards)
            ],
            "revealed_cards": [
                None if card is None else {
                    "player": seat,
                    "rank": card.rank,
                    "suit": card.suit,
                    "points": card.points,
                    "trump": self._is_trump(card),
                }
                for seat, card in enumerate(self.revelation_cards)
            ],
            "event_action": self._event_action_view(player),
            "selected_goal": self._goal_view(self.selected_goals[player]),
            "completed_goal": self._goal_view(self.completed_goals[player]),
            "revealed_goals": self.revealed_goals,
            "hand": [
                {
                    "id": c.id,
                    "label": c.label,
                    "suit": c.suit,
                    "rank": c.rank,
                    "points": c.points,
                    "event_bonus": 1 if self.event_id == "harvest" and c.suit == "Grün" else 0,
                    "trump": self._is_trump(c),
                }
                for c in self.hands[player]
            ],
            "trick": displayed_trick_view,
            "hand_counts": [len(hand) for hand in self.hands],
            "legal_ids": [
                c.id for c in self.legal_cards(player)
            ] if (
                self.phase == Phase.PLAYING
                and self.current_player == player
                and self.prophecy_complete
                and self._goal_selection_complete()
                and (not self.use_goals or self.selected_goals[player] is not None)
                and self.pending_next_leader is None
                and self.pending_reveal_player is None
            ) else [],
            "message": viewer_message,
            "history": self.history[-8:],
        }


class GameManager:
    def __init__(self) -> None:
        self.games: dict[str, Game] = {}
        self.used_event_ids: dict[str, set[str]] = {}

    def new_game(self) -> Game:
        game = Game()
        event = random.choice(load_supported_events())
        self.used_event_ids[game.id] = {event.id}
        game.start(
            round_number=1,
            match_scores=[0, 0, 0, 0],
            start_player=random.randrange(4),
            automated_players={1, 2, 3},
            event=event,
        )
        self.games[game.id] = game
        return game

    def next_round(self, game_id: str) -> Game:
        game = self.get(game_id)
        if game.phase != Phase.FINISHED:
            raise ValueError("Die aktuelle Partie ist noch nicht beendet.")
        if game.match_finished:
            raise ValueError("Das Gesamtspiel ist bereits beendet.")
        used = self.used_event_ids.setdefault(game.id, {game.event_id} if game.event_id else set())
        available = [event for event in load_supported_events() if event.id not in used]
        if not available:
            raise ValueError("Für dieses Match sind keine unterschiedlichen Events mehr verfügbar.")
        event = random.choice(available)
        used.add(event.id)
        game.start(
            round_number=game.round_number + 1,
            match_scores=game.match_scores,
            start_player=(game.start_player + 1) % 4,
            automated_players={1, 2, 3},
            event=event,
        )
        return game

    def get(self, game_id: str) -> Game:
        if game_id not in self.games:
            raise KeyError(game_id)
        return self.games[game_id]
