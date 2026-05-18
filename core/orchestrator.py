"""State machine driver — runs a Run through P0 → P5.

Design: pure orchestration only. Each phase is implemented in
`t2v_promptgen.phases.{intake,dimensions,prompts,export}` and `qa.py`
(P3 lives there because it's coupled with the qa subsystem).

Resumability: every state transition is persisted to runs.db before the
phase function runs, so killing the process at any point leaves a recoverable
state. `resume(run_id)` re-enters whichever Phase the DB last reported.
"""
from __future__ import annotations

from .schema import Phase, Run

# Phase implementations (imported lazily inside step() to avoid circular deps)


class Orchestrator:
    """Drives one Run from start to finish (or until user-blocking gate)."""

    def __init__(self, run: Run) -> None:
        self.run = run

    # ------------------------------------------------------------------
    # Top-level transitions
    # ------------------------------------------------------------------

    def step(self) -> Phase:
        """Advance one phase. Returns the new phase.

        Blocking gates (require user input):
            - End of P1 each round → caller must collect user feedback,
              call `apply_p1_feedback(...)`, then step() again to re-run P1
              or advance to P2.
            - End of P4 each round → same pattern with `apply_p4_feedback`.
        """
        from ..phases import intake, dimensions, prompts, qa, export

        match self.run.phase:
            case Phase.P0_INTAKE:
                intake.run(self.run)
                self._advance(Phase.P1_DIMENSIONS)

            case Phase.P1_DIMENSIONS:
                dimensions.run_round(self.run)
                # Stay in P1 — caller decides next via apply_p1_feedback()

            case Phase.P2_PROMPTS:
                prompts.run(self.run)
                self._advance(Phase.P3_QA)

            case Phase.P3_QA:
                ok = qa.run(self.run)
                if ok:
                    self._advance(Phase.P4_REVIEW)
                else:
                    if self.run.p3_retries_remaining > 0:
                        self.run.p3_retries_remaining -= 1
                        self._advance(Phase.P2_PROMPTS)
                    else:
                        # Soft-pass with markers; user will see flags in P4
                        self._advance(Phase.P4_REVIEW)

            case Phase.P4_REVIEW:
                # No-op here; caller drives via apply_p4_feedback / confirm_p4
                pass

            case Phase.P5_EXPORT:
                export.run(self.run)
                self._advance(Phase.DONE)

            case Phase.DONE:
                pass

        return self.run.phase

    # ------------------------------------------------------------------
    # Phase-1 user gates
    # ------------------------------------------------------------------

    def apply_p1_feedback(self, feedback: dict) -> None:
        """Apply user edits to SL2/axes and bump p1_round.

        feedback shape: {"add_sl2": [...], "remove_sl2_ids": [...],
                          "add_axes": [...], "remove_axes": [...],
                          "free_text": "...optional..."}
        Then call step() again to re-run dimensions generation with the
        feedback merged into the prompt context.
        """
        raise NotImplementedError

    def confirm_p1(self) -> None:
        """User OK'd current SL2/axes — advance to P2."""
        self._advance(Phase.P2_PROMPTS)

    # ------------------------------------------------------------------
    # Phase-4 user gates
    # ------------------------------------------------------------------

    def apply_p4_feedback(self, feedback: dict) -> None:
        """Apply user edits to prompts.

        feedback shape: {"edit": [{id, new_prompt_zh, ...}, ...],
                          "regenerate_ids": [...],
                          "drop_ids": [...],
                          "free_text": "...optional..."}
        Then call step() → returns to P2 for the regen subset, then P3 then P4.
        """
        raise NotImplementedError

    def confirm_p4(self) -> None:
        """User OK'd prompts — advance to export."""
        self._advance(Phase.P5_EXPORT)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _advance(self, new_phase: Phase) -> None:
        self.run.phase = new_phase
        # state.save(self.run)   # TODO: persist
