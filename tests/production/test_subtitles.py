from scripts.production.providers.voice import WordTiming
from scripts.production.subtitles import (
    MAX_LINES_PER_CUE,
    MAX_WORDS_PER_CUE,
    group_words_into_cues,
    wrap_cue_text,
    write_ass,
)


def _word(text, start, end):
    return WordTiming(text=text, start=start, end=end)


def test_groups_words_by_max_word_count():
    words = [_word(f"w{i}", i * 0.3, i * 0.3 + 0.3) for i in range(12)]
    cues = group_words_into_cues(words, max_words=6, max_seconds=999)
    assert len(cues) == 2
    assert cues[0].text == "w0 w1 w2 w3 w4 w5"
    assert cues[1].text == "w6 w7 w8 w9 w10 w11"


def test_groups_words_by_max_duration():
    words = [_word(f"w{i}", i * 1.0, i * 1.0 + 1.0) for i in range(6)]
    cues = group_words_into_cues(words, max_words=99, max_seconds=2.5)
    # Each cue's span (end - start) must never exceed max_seconds.
    for cue in cues:
        assert cue.end - cue.start <= 2.5 + 1e-9


def test_breaks_early_on_sentence_end_punctuation():
    words = [_word("Hello,", 0.0, 0.5), _word("world.", 0.5, 1.0), _word("Next", 1.0, 1.5), _word("sentence", 1.5, 2.0)]
    cues = group_words_into_cues(words, max_words=10, max_seconds=999)
    assert len(cues) == 2
    assert cues[0].text == "Hello, world."
    assert cues[1].text == "Next sentence"


def test_time_offset_shifts_onto_a_global_timeline():
    words = [_word("later", 0.0, 1.0)]
    cues = group_words_into_cues(words, time_offset=10.0)
    assert cues[0].start == 10.0
    assert cues[0].end == 11.0


def test_start_index_continues_numbering_across_scenes():
    words = [_word("word", 0.0, 1.0)]
    cues = group_words_into_cues(words, start_index=5)
    assert cues[0].index == 5


def test_empty_word_list_yields_no_cues():
    assert group_words_into_cues([]) == []


def test_default_max_words_per_cue_is_within_the_requested_5_to_7_range():
    assert 5 <= MAX_WORDS_PER_CUE <= 7


# --- Line wrapping (max 2 lines, respecting word count and visual width) --


def test_short_text_stays_on_a_single_line():
    assert "\n" not in wrap_cue_text("short line")


def test_long_text_wraps_to_at_most_max_lines():
    text = "here is a fairly long caption chunk with several words in it"
    wrapped = wrap_cue_text(text, max_chars_per_line=20)
    assert wrapped.count("\n") <= MAX_LINES_PER_CUE - 1
    assert len(wrapped.split("\n")) <= MAX_LINES_PER_CUE


def test_wrap_never_exceeds_two_lines_even_for_many_words():
    text = " ".join(f"word{i}" for i in range(20))
    wrapped = wrap_cue_text(text, max_chars_per_line=15, max_lines=2)
    assert len(wrapped.split("\n")) == 2


def test_wrap_respects_max_chars_per_line_when_possible():
    text = "alpha bravo charlie delta"
    wrapped = wrap_cue_text(text, max_chars_per_line=12)
    lines = wrapped.split("\n")
    assert len(lines) <= 2
    # The first line (the only one wrapping can shorten) must respect the
    # width budget when a valid break point exists before the limit.
    assert len(lines[0]) <= 12 or " " not in lines[0]


def test_wrap_preserves_all_words():
    text = "one two three four five six"
    wrapped = wrap_cue_text(text, max_chars_per_line=10)
    assert sorted(wrapped.replace("\n", " ").split()) == sorted(text.split())


def test_group_words_into_cues_applies_wrapping_for_long_chunks():
    # 6 longer words should exceed a narrow per-line budget and wrap.
    words = [_word(w, i * 0.4, i * 0.4 + 0.4) for i, w in enumerate(["performance", "graphics", "settings", "comparison", "review", "today"])]
    cues = group_words_into_cues(words, max_words=6, max_seconds=999)
    assert len(cues) == 1
    assert cues[0].text.count("\n") <= MAX_LINES_PER_CUE - 1


# --- No overlapping simultaneous subtitle events ---------------------------


def test_cues_within_one_scene_never_overlap():
    words = [_word(f"w{i}", i * 0.5, i * 0.5 + 0.5) for i in range(20)]
    cues = group_words_into_cues(words, max_words=5, max_seconds=999)
    for earlier, later in zip(cues, cues[1:]):
        assert earlier.end <= later.start


def test_cues_across_scenes_never_overlap():
    """Simulates voice_generation.py's real usage: each scene's word
    timings are grouped separately with a running time_offset equal to the
    cumulative duration of previous scenes."""
    scene_a_words = [_word(f"a{i}", i * 0.5, i * 0.5 + 0.5) for i in range(8)]
    scene_b_words = [_word(f"b{i}", i * 0.4, i * 0.4 + 0.4) for i in range(8)]

    cues_a = group_words_into_cues(scene_a_words, time_offset=0.0, start_index=1)
    scene_a_duration = scene_a_words[-1].end
    cues_b = group_words_into_cues(scene_b_words, time_offset=scene_a_duration, start_index=len(cues_a) + 1)

    all_cues = cues_a + cues_b
    for earlier, later in zip(all_cues, all_cues[1:]):
        assert earlier.end <= later.start


# --- .ass output -------------------------------------------------------


def test_write_ass_produces_a_valid_header_and_events(tmp_path):
    words = [_word("Hello", 0.0, 0.5), _word("world.", 0.5, 1.2)]
    cues = group_words_into_cues(words)
    path = tmp_path / "captions.ass"
    write_ass(cues, path)

    content = path.read_text(encoding="utf-8")
    assert "[Script Info]" in content
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "[V4+ Styles]" in content
    assert "[Events]" in content
    assert "Dialogue: 0,0:00:00.00," in content
    assert "Hello world." in content


def test_write_ass_converts_manual_line_breaks_to_ass_escape(tmp_path):
    from scripts.production.models import SubtitleCue

    cue = SubtitleCue(index=1, start=0.0, end=1.0, text="line one\nline two")
    path = tmp_path / "captions.ass"
    write_ass([cue], path)

    content = path.read_text(encoding="utf-8")
    assert "line one\\Nline two" in content
    # The Dialogue event itself must be a single physical line.
    dialogue_lines = [line for line in content.splitlines() if line.startswith("Dialogue:")]
    assert len(dialogue_lines) == 1


def test_write_ass_strips_curly_braces_to_prevent_override_injection(tmp_path):
    from scripts.production.models import SubtitleCue

    cue = SubtitleCue(index=1, start=0.0, end=1.0, text="watch out {\\pos(0,0)} here")
    path = tmp_path / "captions.ass"
    write_ass([cue], path)

    content = path.read_text(encoding="utf-8")
    assert "{" not in content.split("[Events]")[1]
    assert "}" not in content.split("[Events]")[1]
