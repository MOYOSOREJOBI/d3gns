"""
Live terminal dashboard using `rich`.

Displays a real-time equity table + mini sparkline bar for each bot,
refreshing every 2 seconds.

Layout:
  ┌─ Header: time elapsed, portfolio total, overall progress ──────────────┐
  ├─ Per-bot table: phase | bankroll | locked | progress | bets | W% | bar ┤
  ├─ Recent events log (last 8 lines) ─────────────────────────────────────┤
  └─ Phase legend ──────────────────────────────────────────────────────────┘

Install: pip install rich
"""

import time
import threading
import collections
from typing import Optional
from risk_manager import RiskManager

try:
    from rich.console import Console
    from rich.table   import Table
    from rich.panel   import Panel
    from rich.layout  import Layout
    from rich.live    import Live
    from rich.text    import Text
    from rich import box
    RICH_OK = True
except ImportError:
    RICH_OK = False

from config import BOT_INITIAL_BANK, TARGET_MULTIPLIER

# ── Sparkline ─────────────────────────────────────────────────────────────────
_BLOCKS = " ▁▂▃▄▅▆▇█"

def _sparkline(values: list[float], width: int = 12) -> str:
    if not values:
        return " " * width
    vals = values[-width:]
    lo   = min(vals)
    hi   = max(vals)
    rng  = hi - lo or 1.0
    chars = []
    for v in vals:
        idx = int((v - lo) / rng * (len(_BLOCKS) - 1))
        chars.append(_BLOCKS[idx])
    return "".join(chars).rjust(width)


# ── Phase colours ─────────────────────────────────────────────────────────────
_PHASE_STYLE = {
    "recovery"  : "bold red",
    "phase1"    : "yellow",
    "phase2"    : "cyan",
    "phase3"    : "bold green",
    "halted"    : "bold red on black",
}

_PHASE_LABEL = {
    "recovery" : "⚠ RECOVERY",
    "phase1"   : "① protect",
    "phase2"   : "② grow",
    "phase3"   : "③ PUSH 🔥",
    "halted"   : "⛔ HALTED",
}


# ═══════════════════════════════════════════════════════════════
#  Dashboard
# ═══════════════════════════════════════════════════════════════

class Dashboard:
    """
    Pass in a list of RiskManager objects (one per bot).
    Call start() to begin the live display in a background thread.
    Call stop() to end it.
    """

    def __init__(self, rms: list, sim_speed: float = 1.0, sim_hours: float = 24.0):
        self.rms        = rms
        self.sim_speed  = sim_speed     # e.g. 10 = 10× real time
        self.sim_hours  = sim_hours
        self.start_wall = time.time()
        self.stop_event = threading.Event()
        self._history   = {rm.bot_id: collections.deque(maxlen=60) for rm in rms}
        self._events    = collections.deque(maxlen=10)
        self._lock      = threading.Lock()

        if not RICH_OK:
            print("  [dashboard] Install 'rich' for the live UI: pip install rich")

    def log_event(self, msg: str):
        with self._lock:
            ts = time.strftime("%H:%M:%S")
            self._events.append(f"[dim]{ts}[/dim] {msg}")

    def _record_snapshot(self):
        for rm in self.rms:
            self._history[rm.bot_id].append(rm.progress_multiplier)

    def _build_table(self) -> "Table":
        t = Table(
            box             = box.SIMPLE_HEAVY,
            show_header     = True,
            header_style    = "bold white on grey23",
            border_style    = "grey50",
            expand          = True,
        )
        t.add_column("Bot",       style="bold", width=14)
        t.add_column("Phase",     width=12)
        t.add_column("Bankroll",  justify="right", width=11)
        t.add_column("Locked $",  justify="right", width=10)
        t.add_column("Progress",  justify="right", width=10)
        t.add_column("To 10x",    justify="right", width=10)
        t.add_column("Bets",      justify="right", width=6)
        t.add_column("Win%",      justify="right", width=6)
        t.add_column("Equity ▲",  width=14)

        for rm in self.rms:
            s       = rm.status()
            history = list(self._history[rm.bot_id])
            spark   = _sparkline(history, 12)

            phase_style = _PHASE_STYLE.get(s["phase"], "white")
            phase_label = _PHASE_LABEL.get(s["phase"], s["phase"])

            # Progress bar toward 10x
            pct    = min(s["progress_x"] / TARGET_MULTIPLIER, 1.0)
            filled = int(pct * 10)
            bar    = "█" * filled + "░" * (10 - filled)
            prog   = f"{s['progress_x']:.2f}x {bar}"

            to_target = f"${s['to_target']:.2f}"
            if s["target_hit"]:
                to_target = "🚀 DONE!"

            t.add_row(
                s["bot_id"],
                Text(phase_label, style=phase_style),
                f"${s['bankroll']:.4f}",
                f"${s['total_withdrawn']:.2f}",
                prog,
                to_target,
                str(s["bets"]),
                f"{s['win_rate_pct']:.0f}%",
                Text(spark, style="green"),
            )

        return t

    def _build_header(self) -> "Panel":
        wall_elapsed = time.time() - self.start_wall
        sim_elapsed  = wall_elapsed * self.sim_speed
        sim_pct      = min(sim_elapsed / (self.sim_hours * 3600) * 100, 100)

        total_init      = sum(rm.initial_bankroll for rm in self.rms)
        total_active    = sum(rm.current_bankroll for rm in self.rms)
        total_locked    = sum(rm.total_withdrawn  for rm in self.rms)
        total_progress  = total_active + total_locked
        overall_x       = total_progress / total_init if total_init else 0
        portfolio_target= total_init * TARGET_MULTIPLIER
        bots_hit_target = sum(1 for rm in self.rms if rm.is_target_hit)
        bots_halted     = sum(1 for rm in self.rms if rm.is_halted)

        elapsed_h  = int(sim_elapsed // 3600)
        elapsed_m  = int((sim_elapsed % 3600) // 60)
        elapsed_s  = int(sim_elapsed % 60)

        mode_tag = "[bold yellow]PAPER TRADING[/bold yellow]"
        content  = (
            f"{mode_tag}  │  "
            f"Sim time: [cyan]{elapsed_h:02d}:{elapsed_m:02d}:{elapsed_s:02d}[/cyan] "
            f"({sim_pct:.1f}% of {self.sim_hours:.0f}h)  │  "
            f"Speed: [cyan]{self.sim_speed:.0f}×[/cyan]\n"
            f"Portfolio: [bold]${total_progress:.2f}[/bold]  │  "
            f"Overall progress: [bold green]{overall_x:.2f}×[/bold green]  │  "
            f"Target: ${portfolio_target:.0f}  │  "
            f"Bots 10×: [green]{bots_hit_target}[/green]  │  "
            f"Halted: [red]{bots_halted}[/red]"
        )
        return Panel(content, title="[bold]6-BOT DRY RUN DASHBOARD[/bold]",
                     border_style="bright_blue")

    def _build_events(self) -> "Panel":
        with self._lock:
            events = list(self._events)
        text = "\n".join(events[-8:]) if events else "[dim]No events yet…[/dim]"
        return Panel(text, title="Recent Events", border_style="grey50", height=12)

    def _build_legend(self) -> str:
        return (
            " [red]⚠ RECOVERY[/red] bank<90%  "
            "[yellow]① protect[/yellow] 1×–2×  "
            "[cyan]② grow[/cyan] 2×–5×  "
            "[bold green]③ PUSH 🔥[/bold green] 5×+  "
            "  Hard floor: [red]$80[/red]  Withdraw trigger: [green]$115[/green]"
        )

    def _render(self, console: "Console"):
        self._record_snapshot()
        console.clear()
        console.print(self._build_header())
        console.print(self._build_table())
        console.print(self._build_events())
        console.print(Text.from_markup(self._build_legend()))

    def start(self, refresh_seconds: float = 2.0):
        if not RICH_OK:
            return
        self._thread = threading.Thread(
            target=self._loop,
            args=(refresh_seconds,),
            daemon=True,
        )
        self._thread.start()

    def _loop(self, refresh: float):
        console = Console()
        while not self.stop_event.is_set():
            try:
                self._render(console)
            except Exception:
                pass
            time.sleep(refresh)

    def stop(self):
        self.stop_event.set()


# ── Fallback plain-text status (if rich not installed) ────────────────────────

def plain_status(rms: list, sim_speed: float, start_wall: float,
                 sim_hours: float):
    elapsed  = (time.time() - start_wall) * sim_speed
    h, rem   = divmod(int(elapsed), 3600)
    m, s     = divmod(rem, 60)
    total_p  = sum(rm.progress for rm in rms)
    total_i  = sum(rm.initial_bankroll for rm in rms)
    print(f"\n{'─'*70}")
    print(f"  Sim {h:02d}:{m:02d}:{s:02d}  |  Portfolio ${total_p:.2f} "
          f"({total_p/total_i:.2f}×)")
    for rm in rms:
        s_ = rm.status()
        print(
            f"  {s_['bot_id']:<14} {s_['phase']:<10} "
            f"${s_['bankroll']:>8.4f}  locked=${s_['total_withdrawn']:>8.4f}  "
            f"{s_['progress_x']:.2f}×  W{s_['win_rate_pct']:.0f}%"
        )
    print(f"{'─'*70}")
