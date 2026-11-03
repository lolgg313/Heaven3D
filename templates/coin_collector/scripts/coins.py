# scripts/coins.py — game logic for the "Coin Collector" template.
# Heaven3D loads every .py file in this project's scripts/ folder
# automatically (editor AND game), so the @blueprint classes below are
# available in the Inspector and survive save/load.
import math
import glm
from Heaven3D import Blueprint, blueprint


@blueprint
class Coin(Blueprint):
    """A spinning coin. Walk into it to collect it."""

    def on_begin_play(self):
        self.start_y = self.node.position.y
        self.taken = False
        if self.node.collider is not None:          # walk THROUGH coins
            self.node.collider.is_trigger = True

    def on_tick(self, dt):
        if self.taken:
            return
        self.node.rotation_euler.y = (self.node.rotation_euler.y + 120 * dt) % 360
        self.node.position.y = self.start_y + math.sin(self.engine.time * 3) * 0.15
        player = self.engine.first_blueprint("FPSController")
        if player is not None and glm.distance(player.node.position, self.node.position) < 1.5:
            self.taken = True
            self.node.visible = False
            self.engine.audio.play("pickup")
            self.engine.send_message("coin_collected")

    def on_end_play(self):                          # Stop -> put it back
        self.node.visible = True
        self.node.position.y = self.start_y


@blueprint
class CoinCounter(Blueprint):
    """Counts coins, shows the score and a timer, and the win screen.
    The best time is kept with engine.save_game(), so it survives
    restarts (and travels with an exported game in its saves folder)."""
    TIME_LIMIT = 60.0

    def on_begin_play(self):
        self.total = len(self.engine.find_nodes_with_blueprint("Coin"))
        self.collected = 0
        self.time_left = self.TIME_LIMIT
        self.over = False
        self.saved = False
        self.best = (self.engine.load_game("best", default={}) or {}).get("seconds")

    def on_message(self, event, data):
        if event == "coin_collected" and not self.over:
            self.collected += 1
            if self.collected == self.total:
                self.engine.audio.play("heal")

    def on_tick(self, dt):
        hud = self.engine.hud
        won = self.total and self.collected == self.total
        if not won and not self.over:
            self.time_left -= dt
            if self.time_left <= 0:
                self.over = True
                self.engine.audio.play("hurt")
        hud.text(0.03, 0.04, f"Coins: {self.collected} / {self.total}", size=32,
                 color=(1.0, 0.85, 0.2, 1.0))
        hud.text(0.03, 0.09, f"Time: {max(0, self.time_left):.0f}", size=32)
        if self.best is not None:
            hud.text(0.03, 0.14, f"Best: {self.best:.1f} s", size=24, color=(0.7, 0.8, 1.0, 1.0))
        if won and not self.saved:
            self.saved = True
            took = self.TIME_LIMIT - self.time_left
            if self.best is None or took < self.best:
                self.best = took
                self.engine.save_game("best", {"seconds": round(took, 2)})
        if won:
            hud.center_text(0.40, "YOU WIN!", size=72, color=(0.3, 1.0, 0.4, 1.0))
        elif self.over:
            hud.center_text(0.40, "TIME'S UP!", size=72, color=(1.0, 0.3, 0.3, 1.0))
