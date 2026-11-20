"""Parser, timeline and rule tests. Fixtures are synthetic (not Kuku's scripts)."""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import pytest

from promofinder.parse import fmt_time, load_show, parse_episode, parse_episode_range, parse_time
from promofinder.timeline import (Promo, clean_cuts, dialogue_in_progress, make_segment, segment_from_times,
                                  view_items, window)
from promofinder.validate import flags, validate

FIX = Path(__file__).parent / "fixtures" / "show"
PCFG = {"min_s": 20.0, "max_s": 60.0, "min_segment_s": 3.0, "max_segments": 3, "hook_window_s": 3.0}
PACK = Path(os.environ.get(
    "PROMOFINDER_PACK",
    Path(__file__).resolve().parents[2] / "I Married a Broken Soldier. He was a Duke-scripts (2) (1)"
    / "I Married a Broken Soldier. He was a Duke",
))


@pytest.fixture(scope="module")
def show():
    eps = load_show(FIX)
    return eps, {e.number: e for e in eps}


def test_time_roundtrip():
    assert parse_time("01:05.60") == 65.6
    assert parse_time("1:02:03.5") == 3723.5
    assert fmt_time(65.6) == "01:05.60"


def test_parse_lower_and_upper_case_formats(show):
    eps, E = show
    assert [e.number for e in eps] == [1, 2]
    e1, e2 = E[1], E[2]
    assert e1.logline.startswith("A baker")
    assert len(e1.scenes) == 2 and e1.scenes[1].start == 30.0
    # episode 2 uses upper-case field names, like episode 6 of the real pack
    assert len(e2.scenes) == 1 and e2.scenes[0].heading.startswith("EXT. STREET") and e2.scenes[0].end == 45.0
    assert not e1.warnings and not e2.warnings
    kinds = Counter(b.kind for b in e1.blocks)
    assert kinds == {"CAMERA": 7, "DIALOGUE": 7, "ACTION": 1, "SFX": 2}


def test_speaker_label_split(show):
    _, E = show
    vo = next(b for b in E[1].blocks if b.tags)
    assert vo.speaker == "MARA" and vo.tags == ["V.O."] and vo.emotion == "Steely"
    os_ = next(b for b in E[2].blocks if b.tags)
    assert os_.speaker == "MAN IN SUIT" and os_.tags == ["O.S."] and os_.emotion == "Menacing"
    assert os_.text.startswith("Hello again")  # surrounding quotes stripped


def test_clean_cuts_never_inside_dialogue(show):
    _, E = show
    for ep in E.values():
        for t in clean_cuts(ep):
            assert dialogue_in_progress(ep, t) is None
    # 10.0 is a shot change but MARA is mid-line (9.5–12.5): not a clean cut
    assert 10.0 not in clean_cuts(E[1])


def test_make_segment_snaps_out_of_dialogue(show):
    _, E = show
    ep = E[1]
    # start on the camera block that begins at 10.0 while MARA speaks 9.5–12.5 -> pulled back to 9.5
    cam10 = next(b for b in ep.blocks if b.kind == "CAMERA" and b.start == 10.0)
    seg = make_segment(ep, cam10.local_id, "B016")
    assert seg.start == 9.5
    assert dialogue_in_progress(ep, seg.start) is None and dialogue_in_progress(ep, seg.end) is None
    with pytest.raises(ValueError, match="do not exist"):
        make_segment(ep, "B999", "B001")


def test_segment_from_times_and_validate_duration(show):
    _, E = show
    ep = E[1]
    seg = segment_from_times(ep, 10.0, 49.0)
    assert seg.start == 9.5  # pulled back out of MARA's line (9.5–12.5)
    assert seg.end == 49.0  # not inside dialogue; nearest shot boundary (50.0) is beyond the 0.5s snap
    p = Promo("x", "single", "llm", [seg])
    assert validate(p, E, PCFG) == []
    short = Promo("y", "single", "llm", [make_segment(ep, "B001", "B004")])
    assert any("total length" in e for e in validate(short, E, PCFG))


def test_montage_overlap_and_time_accumulation(show):
    _, E = show
    a = make_segment(E[1], "B004", "B011")  # 06.0 – 27.0 snapped
    b = make_segment(E[2], "B001", "B003")
    m = Promo("m", "montage", "llm", [a, b])
    assert validate(m, E, PCFG) == []
    items = view_items(m, E)
    second = [it for it in items if it.segment_index == 1]
    assert abs(second[0].rel_start - a.duration) < 0.01
    assert "cross_episode" in flags(m, E, PCFG)
    dup = Promo("d", "montage", "llm", [a, make_segment(E[1], "B008", "B012")])
    assert any("repeat the same footage" in e for e in validate(dup, E, PCFG))


def test_hook_window_contents(show):
    _, E = show
    p = Promo("h", "single", "llm", [make_segment(E[2], "B001", "B009")])
    first = window(view_items(p, E), 0, 3)
    assert any("slap" in it.block.text.lower() for it in first)


def test_episode_range():
    assert parse_episode_range("1-3,7") == {1, 2, 3, 7}
    assert parse_episode_range(None) is None


@pytest.mark.skipif(not PACK.exists(), reason="Kuku input pack not present (it is confidential and not in the repo)")
def test_real_pack_totals():
    eps = load_show(PACK)
    kinds = Counter(b.kind for e in eps for b in e.blocks)
    assert len(eps) == 50
    assert sum(len(e.scenes) for e in eps) == 100
    assert kinds == {"CAMERA": 1754, "ACTION": 372, "DIALOGUE": 1487, "SFX": 114}
    assert not [w for e in eps for w in e.warnings]
    e6 = next(e for e in eps if e.number == 6)
    assert len(e6.scenes) == 2 and e6.duration == 115.0


def test_hook_claim_check(show):
    from promofinder.validate import hook_claim_check
    _, E = show
    seg = make_segment(E[2], "B001", "B009")
    def p(hook):
        return Promo("c", "single", "llm", [seg], hook=hook)
    assert hook_claim_check(p('Mara: "Ten years and you sold it like it was nothing!"'), E, 3.0) == "ok"
    assert hook_claim_check(p('"The man you just met is not who he says he is."'), E, 3.0) == "later_in_clip"
    assert hook_claim_check(p('"Hello again, Mara. We need to talk."'), E, 3.0) in ("later_in_clip", "outside_clip")
    assert hook_claim_check(p('"You will never see this shop again!"'), E, 3.0) == "not_in_script"
    assert hook_claim_check(p("a slap across the face"), E, 3.0) is None  # nothing quoted -> not checkable
