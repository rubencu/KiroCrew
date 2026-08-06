"""The trigger matcher contributes a pointer, not a skill body.

Pins the behavior that keeps skill bodies out of the per-turn prompt: a trigger
match names the skill and where to read it, and only ``always: true`` pinning or
an explicit ``$skillname`` still spends body price. Every assertion here fails if
the body injection is restored.
"""

from pathlib import Path

import pytest

from kiro_crew.context import ContextBuilder
from kiro_crew.context_blocks import split_blocks
from kiro_crew.memory import MemoryStore
from kiro_crew.skills import _SHORT_DESC_CHARS, SkillsLoader

BODY_SENTINEL = "STEP ONE: pour the concrete before the rebar."


def _write_skill(
    root: Path,
    name: str,
    *,
    triggers: str | None = "zebra quokka",
    always: bool = False,
    description: str = "Lay a foundation",
    body: str = BODY_SENTINEL,
) -> Path:
    d = root / name
    d.mkdir(parents=True)
    fm = f"---\nname: {name}\ndescription: {description}\n"
    if triggers:
        fm += f"triggers: {triggers}\n"
    if always:
        fm += "always: true\n"
    (d / "SKILL.md").write_text(fm + f"---\n{body}")
    return d / "SKILL.md"


def _builder(tmp_path: Path, skills: SkillsLoader) -> ContextBuilder:
    return ContextBuilder(memory=MemoryStore(workspace=tmp_path / "ws"), skills=skills)


class TestTriggerHintRendering:
    def test_hint_names_the_skill_and_its_path_without_the_body(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        path = _write_skill(skills, "foundation")
        loader = SkillsLoader(skills_path=skills, install_builtins=False)

        hint = loader.trigger_hint(["foundation"])

        assert "[Relevant skills for this message]" in hint
        assert "foundation" in hint
        assert str(path) in hint
        assert str(path.parent) in hint
        assert BODY_SENTINEL not in hint

    def test_hint_tells_the_agent_it_may_already_have_the_skill(self, tmp_path: Path) -> None:
        """The caveat is load-bearing, not decoration.

        Without it the agent re-reads a skill whose body is already in the
        replayed ACP history, spending a tool round-trip to put that body back
        into the window as tool output — worse than the re-injection the pointer
        replaces.
        """
        skills = tmp_path / "skills"
        _write_skill(skills, "foundation")
        loader = SkillsLoader(skills_path=skills, install_builtins=False)

        hint = loader.trigger_hint(["foundation"])

        assert "already appears earlier in this conversation" in hint

    def test_long_description_is_truncated(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        _write_skill(skills, "verbose", description="x" * (_SHORT_DESC_CHARS + 200))
        loader = SkillsLoader(skills_path=skills, install_builtins=False)

        hint = loader.trigger_hint(["verbose"])

        assert "x" * _SHORT_DESC_CHARS in hint
        assert "x" * (_SHORT_DESC_CHARS + 1) not in hint
        assert "…" in hint

    def test_unknown_name_yields_no_block(self, tmp_path: Path) -> None:
        loader = SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False)
        assert loader.trigger_hint(["does-not-exist"]) == ""

    def test_empty_selection_yields_no_block(self, tmp_path: Path) -> None:
        loader = SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False)
        assert loader.trigger_hint([]) == ""


class TestBuiltMessage:
    def test_triggered_skill_body_stays_out_of_the_message(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        path = _write_skill(skills, "foundation")
        loader = SkillsLoader(skills_path=skills, install_builtins=False)

        msg, _ = _builder(tmp_path, loader).build_message("zebra quokka", is_new_session=False)

        assert BODY_SENTINEL not in msg
        assert "[Relevant skills for this message]" in msg
        assert str(path) in msg

    def test_always_skill_keeps_its_full_body(self, tmp_path: Path) -> None:
        """Pinning is the compliance escape hatch and must be untouched."""
        skills = tmp_path / "skills"
        _write_skill(skills, "pinned", triggers=None, always=True)
        loader = SkillsLoader(skills_path=skills, install_builtins=False)

        ctx = _builder(tmp_path, loader).build_session_context()

        assert BODY_SENTINEL in ctx

    def test_no_match_emits_no_block(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        _write_skill(skills, "foundation")
        loader = SkillsLoader(skills_path=skills, install_builtins=False)

        msg, _ = _builder(tmp_path, loader).build_message(
            "unrelated wording", is_new_session=False
        )

        assert "[Relevant skills for this message]" not in msg

    @pytest.mark.parametrize("cap", [0, 3])
    def test_max_triggered_zero_disables_the_hint(self, tmp_path: Path, cap: int) -> None:
        """The floor lift is what makes the path switchable off at all."""
        skills = tmp_path / "skills"
        _write_skill(skills, "foundation")
        loader = SkillsLoader(skills_path=skills, install_builtins=False)
        loader._max_triggered = cap

        msg, _ = _builder(tmp_path, loader).build_message("zebra quokka", is_new_session=False)

        present = "[Relevant skills for this message]" in msg
        assert present is (cap > 0)


class TestInjectOnTriggerOptIn:
    """The per-skill escape hatch for procedures that must be obeyed on match.

    Without it, a skill authored to *enforce* a step is silently downgraded to an
    agent-optional read — and the only alternatives are ``always: true`` (charged
    every turn regardless of relevance) or ``$skillname`` (needs the user to know
    the skill exists).
    """

    def test_opted_in_skill_keeps_its_body(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        d = skills / "preflight"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: preflight\ndescription: Mandatory check\n"
            f"triggers: zebra quokka\ninject_on_trigger: true\n---\n{BODY_SENTINEL}"
        )
        loader = SkillsLoader(skills_path=skills, install_builtins=False)

        msg, _ = _builder(tmp_path, loader).build_message("zebra quokka", is_new_session=False)

        assert BODY_SENTINEL in msg
        assert "[Skill: preflight]" in msg
        assert "[Relevant skills for this message]" not in msg

    def test_split_partitions_and_preserves_order(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        for name, enforced in (("a-ptr", False), ("b-body", True), ("c-ptr", False)):
            d = skills / name
            d.mkdir(parents=True)
            fm = f"---\nname: {name}\ndescription: d\ntriggers: zebra\n"
            if enforced:
                fm += "inject_on_trigger: true\n"
            (d / "SKILL.md").write_text(fm + "---\nbody")
        loader = SkillsLoader(skills_path=skills, install_builtins=False)

        enforced, pointer_only = loader.split_triggered(["a-ptr", "b-body", "c-ptr"])

        assert enforced == ["b-body"]
        assert pointer_only == ["a-ptr", "c-ptr"]

    def test_mixed_match_emits_both_a_body_and_a_pointer(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        d = skills / "enforced"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: enforced\ndescription: d\ntriggers: zebra quokka\n"
            f"inject_on_trigger: true\n---\n{BODY_SENTINEL}"
        )
        _write_skill(skills, "offered", body="POINTER ONLY BODY")
        loader = SkillsLoader(skills_path=skills, install_builtins=False)

        msg, _ = _builder(tmp_path, loader).build_message("zebra quokka", is_new_session=False)

        assert BODY_SENTINEL in msg
        assert "POINTER ONLY BODY" not in msg
        assert "[Relevant skills for this message]" in msg

    def test_absent_flag_defaults_to_pointer(self, tmp_path: Path) -> None:
        """The opt-in must be explicit — a plain skill does not get body price."""
        skills = tmp_path / "skills"
        _write_skill(skills, "plain")
        loader = SkillsLoader(skills_path=skills, install_builtins=False)

        assert loader.split_triggered(["plain"]) == ([], ["plain"])

    def test_unknown_name_is_dropped_from_both_sides(self, tmp_path: Path) -> None:
        loader = SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False)
        assert loader.split_triggered(["ghost"]) == ([], [])


class TestDeliveryIsAuditable:
    """A declined pointer leaves no other trace, so the split must be recorded.

    Without it, "the skill stopped being followed" is indistinguishable from
    "the skill never matched" — the invisible-failure mode a pointer introduces.
    """

    def test_sel_metadata_separates_bodies_from_pointers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        skills = tmp_path / "skills"
        d = skills / "enforced"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: enforced\ndescription: d\ntriggers: zebra quokka\n"
            "inject_on_trigger: true\n---\nbody"
        )
        _write_skill(skills, "offered")
        loader = SkillsLoader(skills_path=skills, install_builtins=False)

        captured: dict[str, str] = {}

        class _Sel:
            def log_tool_invocation(self, **kwargs: object) -> None:
                meta = kwargs.get("metadata")
                if isinstance(meta, dict):
                    captured.update(meta)

        monkeypatch.setattr("kiro_crew.skills.sel", lambda: _Sel())

        loader.get_triggered_skills("zebra quokka")

        assert captured["bodies"] == "enforced"
        assert captured["pointers"] == "offered"


class TestBlockAttribution:
    def test_hint_is_attributed_to_skill_hint_not_the_preceding_block(self) -> None:
        """Mis-attribution would corrupt the measurement this change is judged by."""
        prompt = "[PROJECT] dir\n\n[Relevant skills for this message]\n- **a**: d → `/p`\n"

        blocks = split_blocks(prompt)

        assert blocks.get("skill_hint", 0) > 0
        assert "loaded_skill" not in blocks
